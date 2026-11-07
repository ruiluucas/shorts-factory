"""Configuração da camada de decisão, validada por Pydantic no startup.

Duas invariantes que o pipeline NÃO pode violar em silêncio:

  1. `allow_remote: false` significa nenhuma rede. Não é sugestão.
  2. Usos desligados por padrão. Ligar exige mudança explícita no YAML.

`load_decision_config()` levanta `DecisionConfigError` com mensagem legível em vez
de deixar o pipeline rodar com peso errado ou backend inexistente.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

VALID_BACKENDS = ("heuristic", "jev_api", "openjev", "mock", "videojev")

# Uso -> chave no bloco decision.uses
USE_KEYS = (
    "semantic_scoring",
    "hook_selection",
    "publish_gate",
    "visual_localization",
    "video_grounding",
)


class DecisionConfigError(ValueError):
    """Config de decisão inválida — o pipeline deve abortar no startup."""


class DecisionUses(BaseModel):
    """Toggles dos usos A-E. Todos desligados por padrão."""

    semantic_scoring: bool = False
    hook_selection: bool = False
    publish_gate: bool = False
    visual_localization: bool = False
    video_grounding: bool = False

    def enabled(self) -> list[str]:
        return [k for k in USE_KEYS if getattr(self, k)]


class SemanticWeights(BaseModel):
    use_llm: bool = False
    local_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    component_weight: float = Field(default=0.15, ge=0.0, le=1.0)


class PublishGateConfig(BaseModel):
    flag_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    block_at_flags: int = Field(default=3, ge=1, le=len(USE_KEYS) + 8)
    block_always: list[str] = ["copyright_risk"]
    flags: list[str] = [
        "profanity",
        "violence",
        "medical_advice",
        "financial_advice",
        "copyright_risk",
        "harassment",
        "politics",
    ]

    @field_validator("flags")
    @classmethod
    def _no_dupes(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("publish_gate.flags tem entradas duplicadas")
        return v


class VisualConfig(BaseModel):
    send_frames_remote: bool = False
    target_labels: dict[str, list[str]] = Field(default_factory=dict)


class DecisionConfig(BaseModel):
    """Bloco `decision:` do pipeline_config.yaml."""

    backend: str = "heuristic"
    allow_remote: bool = False

    timeout_s: float = Field(default=8.0, gt=0, le=120)
    retries: int = Field(default=2, ge=0, le=10)
    backoff_base_s: float = Field(default=0.5, ge=0, le=30)
    max_backoff_s: float = Field(default=8.0, ge=0, le=120)
    min_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    max_calls_per_job: int = Field(default=60, ge=0, le=10_000)

    cache_dir: str = ".decision_cache"
    cache_enabled: bool = True

    api_key_env: str = "TYPESAFE_API_KEY"
    model: str = "jev-latest"
    base_url: str = "https://api.typesafe.ai"

    openjev_base_url: str = ""
    openjev_api_key_env: str = "OPENJEV_API_KEY"

    videojev_endpoint: str = ""
    videojev_enabled: bool = False

    uses: DecisionUses = Field(default_factory=DecisionUses)
    semantic_weights: SemanticWeights = Field(default_factory=SemanticWeights)
    publish_gate: PublishGateConfig = Field(default_factory=PublishGateConfig)
    visual: VisualConfig = Field(default_factory=VisualConfig)

    @field_validator("backend")
    @classmethod
    def _known_backend(cls, v: str) -> str:
        if v not in VALID_BACKENDS:
            raise ValueError(f"backend '{v}' desconhecido; use um de {list(VALID_BACKENDS)}")
        return v

    @field_validator("base_url")
    @classmethod
    def _https(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"base_url deve começar com http(s):// — recebido: {v}")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> DecisionConfig:
        """Regras que cruzam campos. Errar aqui é melhor que falhar em runtime."""

        # Backend remoto sem permissão de saída de dados é contraditório.
        if self.backend in ("jev_api", "openjev", "videojev") and not self.allow_remote:
            raise ValueError(
                f"backend '{self.backend}' exige decision.allow_remote: true — "
                "sem isso, nenhum texto ou quadro pode sair da máquina"
            )

        # Nenhum uso pode enviar quadros sem permissão explícita.
        if self.visual.send_frames_remote and not self.allow_remote:
            raise ValueError("visual.send_frames_remote: true exige decision.allow_remote: true")

        # VideoJev só faz sentido com endpoint e permissão remotas.
        if self.uses.video_grounding and not self.videojev_enabled:
            raise ValueError(
                "uses.video_grounding: true exige videojev_enabled: true "
                "(o modelo de 4B não cabe em 4 GB de VRAM; só via endpoint)"
            )
        if self.videojev_enabled and not self.videojev_endpoint:
            raise ValueError("videojev_enabled: true exige videojev_endpoint preenchido")

        # openjev precisa de URL.
        if self.backend == "openjev" and not self.openjev_base_url:
            raise ValueError("backend 'openjev' exige openjev_base_url preenchido")

        # max_backoff menor que base é config quebrada.
        if self.max_backoff_s < self.backoff_base_s:
            raise ValueError("max_backoff_s não pode ser menor que backoff_base_s")

        return self

    # ------------------------------------------------------------- helpers

    @property
    def is_remote(self) -> bool:
        """True se o backend escolhido faz chamada de rede."""
        return self.backend in ("jev_api", "openjev", "videojev")

    def any_use_enabled(self) -> bool:
        return bool(self.uses.enabled())


def parse_decision_config(raw: dict[str, Any] | None) -> DecisionConfig:
    """Valida o bloco `decision` de um dict já carregado (ex.: load_config)."""
    block = raw or {}
    if not isinstance(block, dict):
        raise DecisionConfigError(f"bloco 'decision' deve ser um mapa — recebido {type(block)}")
    try:
        return DecisionConfig(**block)
    except Exception as exc:  # pydantic ValidationError e ValueError de validador
        raise DecisionConfigError(f"config de decisão inválida: {exc}") from exc


def load_decision_config(config_path: str | Path | None = None) -> DecisionConfig:
    """Carrega o YAML e devolve a config de decisão validada.

    Ausência do bloco `decision` NÃO é erro: devolve os padrões (tudo desligado,
    backend heurístico). Assim o pipeline antigo continua funcionando sem tocar
    no YAML.
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "config" / "pipeline_config.yaml"
    path = Path(config_path)

    if not path.exists():
        return DecisionConfig()

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    return parse_decision_config(data.get("decision"))


def validate_weights_sum(weights: dict[str, float], tolerance: float = 1e-6) -> None:
    """Levanta se os pesos não somam 1.0 — usado quando semantic_quality entra."""
    total = sum(weights.values())
    if abs(total - 1.0) > tolerance:
        raise DecisionConfigError(
            f"pesos de scoring somam {total:.6f}, deveriam somar 1.0 (diferença {total - 1.0:+.6f})"
        )
