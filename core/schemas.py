"""Contratos de dados do Shorts Factory (Pydantic v2)."""

from enum import StrEnum

from pydantic import BaseModel, Field


class RightsStatus(StrEnum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class CategoryEnum(StrEnum):
    FUNNY = "FUNNY"
    SURPRISE = "SURPRISE"
    FAIL = "FAIL"
    CHAOS = "CHAOS"
    REACTION = "REACTION"
    DISCOVERY = "DISCOVERY"
    TENSION = "TENSION"


class WordSegment(BaseModel):
    word: str
    start: float
    end: float
    score: float
    speaker: str | None = None


class RightsMetadata(BaseModel):
    source_id: str
    creator_handle: str
    status: RightsStatus
    commercial_permission: bool
    evidence_url_or_path: str
    expiration_date: str | None = None


class ViralCandidate(BaseModel):
    candidate_id: str
    start_time: float
    end_time: float
    duration: float
    category: CategoryEnum
    score: float = Field(..., ge=0, le=100)
    hook_text: str
    justification: str
    risk_flags: list[str] = []
    confidence: float


class BrollOverlay(BaseModel):
    """Imagem sobreposta ilustrando o que está sendo falado.

    `path` é absoluto ou relativo ao repo. `t_start`/`t_end` são relativos ao
    início do corte. `license` é obrigatório: sem licença provada, a imagem
    não deveria ter chegado até aqui (ver core/broll.py).
    """

    path: str
    label: str
    t_start: float = Field(ge=0)
    t_end: float = Field(gt=0)
    corner: str = "top"
    source: str = "openverse"
    license: str
    attribution: str = ""
    score: float = 0.0

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


class SilenceCut(BaseModel):
    """Intervalo de silêncio removido, em tempo absoluto do vídeo fonte.

    O render usa os `keep_ranges`; esta lista existe para auditoria e para o
    QC comparar duração esperada com a obtida.
    """

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    removed_s: float = Field(gt=0)

    @property
    def start_end_valid(self) -> bool:
        return self.end > self.start


class EditPlan(BaseModel):
    job_id: str
    composition_template: str  # ex: "ShortSplitScreen", "ShortCinematic"
    candidate_id: str
    source_video_path: str
    output_duration_seconds: float
    crop_mode: str
    words: list[WordSegment]
    sfx_triggers: list[dict]
    background_music_track: str | None = None
    # B-roll: imagens sobrepostas sobre a fala (vazio = comportamento antigo)
    broll: list[BrollOverlay] = []
    # Silêncios removidos na edição (vazio = audio intacto)
    silence_cuts: list[SilenceCut] = []
    # Faixas do vídeo fonte que sobrevivem ao corte, em tempo absoluto.
    # Vazio = usa [candidate.start, candidate.end] sem edição.
    keep_ranges: list[tuple[float, float]] = []
    # Ritmo: janelas de punch-in por ênfase (relativo ao corte)
    punch_ins: list[dict] = []


class QCReport(BaseModel):
    job_id: str
    passed: bool
    audio_clipping_detected: bool
    black_frames_detected: bool
    aspect_ratio_valid: bool
    rights_check_passed: bool
    failure_reasons: list[str] = []


# ---------------------------------------------------------------------------
# Camada de decisão (Jev / heurística)
#
# Jev NÃO gera texto: estas estruturas carregam apenas decisões — escolha,
# notas, probabilidade, veredicto e região. O contrato é o mesmo para qualquer
# backend, então trocar Jev por heurística não muda o que as etapas consomem.
# ---------------------------------------------------------------------------


class DecisionKind(StrEnum):
    """Tipo de primitive do System One."""

    CHOICE = "choice"
    SCORE = "score"
    NOUL = "noul"
    BOX = "box"


class BackendKind(StrEnum):
    """Quem decidiu. Registrado em todo resultado."""

    HEURISTIC = "heuristic"
    JEV_API = "jev_api"
    OPENJEV = "openjev"
    MOCK = "mock"
    VIDEOJEV = "videojev"


class GateVerdict(StrEnum):
    """Veredicto do publish gate. Distinto do gatekeeper jurídico."""

    PUBLISH = "publish"
    REVIEW = "review"
    BLOCK = "block"


class DecisionOption(BaseModel):
    """Uma opção oferecida ao backend (choice) ou um nível (score)."""

    name: str
    description: str = ""


class DecisionRequest(BaseModel):
    """Pergunta única enviada ao backend de decisão.

    `options` é usado por `choice` (nome + descrição) e por `score`
    (descrições ordenadas, posição = nota). `question` fica vazio para `box`.
    """

    kind: DecisionKind
    question: str = ""
    options: list[DecisionOption] = []
    # Texto/estado avaliado. Nunca conteúdo de mídia: só texto derivado.
    state: str = ""
    # Identificador para cache e log. Não vai para o backend remoto.
    request_id: str = ""

    def validate_kind(self) -> "DecisionRequest":
        """Regras da spec: choice/score exigem opções; box não aceita opções."""
        if self.kind in (DecisionKind.CHOICE, DecisionKind.SCORE) and not self.options:
            raise ValueError(f"{self.kind} exige ao menos uma opção")
        if self.kind == DecisionKind.SCORE and not (1 <= len(self.options) <= 10):
            raise ValueError("score aceita entre 1 e 10 níveis")
        if self.kind == DecisionKind.CHOICE and len(self.options) > 255:
            raise ValueError("choice aceita no máximo 255 opções")
        if self.kind == DecisionKind.BOX and self.options:
            raise ValueError("box não aceita opções")
        return self


class DecisionResult(BaseModel):
    """Resposta normalizada de qualquer backend.

    `choice` vem de choice; `score` de score (0..n-1); `noul` é P(sim) em 0-1.
    `probabilities` é preenchido quando o backend devolve distribuição.
    """

    backend: BackendKind
    kind: DecisionKind
    choice: str | None = None
    score: float | None = None
    noul: float | None = None
    probabilities: dict[str, float] = {}
    confidence: float = 0.0
    latency_ms: float = 0.0
    cache_hit: bool = False
    fell_back: bool = False
    fallback_reason: str | None = None

    def is_confident(self, minimum: float) -> bool:
        """Abaixo do limiar, o chamador deve tratar como indisponível."""
        return self.confidence >= minimum


class GateResult(BaseModel):
    """Resultado do publish gate. Roda DEPOIS do gatekeeper jurídico."""

    verdict: GateVerdict
    flags: dict[str, float] = {}
    confidence: float = 0.0
    backend: BackendKind = BackendKind.HEURISTIC
    reasoned_by: str = ""  # qual sinal puxou o veredicto

    def triggered(self, threshold: float) -> list[str]:
        return sorted(f for f, p in self.flags.items() if p >= threshold)


class RegionOfInterest(BaseModel):
    """Região candidata ou escolhida em um quadro/tempo.

    `bbox_norm` usa frações 0-1 da largura/altura, origem no canto superior
    esquerdo — mesmo espaço do Box do Jev, para permitir troca de backend.
    """

    t_start: float
    t_end: float
    bbox_norm: tuple[float, float, float, float]  # left, top, right, bottom
    label: str
    source: str  # "face", "motion", "tile", "object", "jev", "heuristic"
    confidence: float = 0.0

    def validate_box(self) -> "RegionOfInterest":
        left, top, right, bottom = self.bbox_norm
        if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
            raise ValueError(f"bbox inválida: {self.bbox_norm}")
        return self

    @property
    def center(self) -> tuple[float, float]:
        left, top, right, bottom = self.bbox_norm
        return ((left + right) / 2, (top + bottom) / 2)
