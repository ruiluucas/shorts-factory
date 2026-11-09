"""B-roll: encontrar imagens que ilustram o que está sendo falado.

Duas fontes, nesta ordem de prioridade:

  1. **Pasta local** (`assets/broll/<categoria>/`) — controle total, uso manual.
     Você joga a imagem ali e o pipeline prefere ela.
  2. **Openverse** — banco com licenças Creative Commons, sem chave de API.

Licença é trava, não sugestão: só entra imagem cuja licença permite uso
comercial. Cada asset carrega `source`, `license` e `attribution`, e o
`edit_plan` os publica. Sem licença provada, a imagem não entra.

O que este módulo NÃO faz:
  - não gera imagem (nada de IA de imagem);
  - não usa Google Imagens nem print de site — a licença é desconhecida;
  - não baixa sem registrar a atribuição.
"""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_DIR = PROJECT_ROOT / "assets" / "broll"
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".broll_cache"

OPENVERSE_API = "https://api.openverse.org/v1/images/"
# Só licenças que permitem uso comercial. CC-BY-NC fica de fora de propósito.
COMMERCIAL_LICENSES = {"by", "by-sa", "cc0", "pdm", "zero"}


# ---------------------------------------------------------------------------
# Contrato de saída
# ---------------------------------------------------------------------------


@dataclass
class BrollAsset:
    """Uma imagem escolhida, com proveniência completa."""

    path: str
    label: str  # assunto que ela ilustra (ex: "carro")
    source: str  # "local" | "openverse"
    license: str
    attribution: str
    width: int = 0
    height: int = 0
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "label": self.label,
            "source": self.source,
            "license": self.license,
            "attribution": self.attribution,
            "width": self.width,
            "height": self.height,
            "score": round(self.score, 4),
        }


@dataclass
class BrollPlacement:
    """Onde um asset aparece no short: um intervalo relativo ao corte."""

    asset: BrollAsset
    t_start: float  # relativo ao início do corte
    t_end: float
    trigger_text: str  # a fala que motivou
    corner: str = "top"  # top | top_left | top_right

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset.to_dict(),
            "t_start": round(self.t_start, 3),
            "t_end": round(self.t_end, 3),
            "duration": round(self.duration, 3),
            "trigger_text": self.trigger_text,
            "corner": self.corner,
        }


# ---------------------------------------------------------------------------
# Extração de assunto a partir da fala
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Minúsculas, sem acento — para casar 'carro' com 'Carro' e 'veículo'."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def extract_topics(
    words: list[dict[str, Any]],
    labels: dict[str, list[str]],
    *,
    min_gap: float = 6.0,
    window: float = 1.5,
) -> list[dict[str, Any]]:
    """Encontra menções de assunto na fala.

    `words` são WordSegment serializados (word/start/end). `labels` mapeia
    assunto -> sinônimos, vindo do YAML (`decision.visual.target_labels`).

    Devolve um item por menção, com o horário absoluto no vídeo. Menções do
    mesmo assunto dentro de `min_gap` segundos colapsam numa só — senão
    "olha o carro, olha o carro" colocaria três imagens iguais.
    """
    vocab: list[tuple[str, str]] = []
    for label, synonyms in labels.items():
        for syn in synonyms:
            vocab.append((normalize(syn), label))

    # Sinônimos longos primeiro: "helicoptero" antes de "ele".
    vocab.sort(key=lambda pair: len(pair[0]), reverse=True)

    hits: list[dict[str, Any]] = []
    for w in words:
        token = normalize(str(w.get("word", ""))).strip(".,!?;:…\"'")
        if not token:
            continue
        for syn, label in vocab:
            if token == syn or (len(syn) >= 4 and syn in token):
                start = float(w.get("start", 0.0))
                end = float(w.get("end", start))
                hits.append(
                    {
                        "label": label,
                        "t": start,
                        "t_end": min(end + window, start + window + 1.0),
                        "word": str(w.get("word", "")),
                    }
                )
                break

    # Colapsa repetições próximas do mesmo assunto.
    hits.sort(key=lambda h: h["t"])
    out: list[dict[str, Any]] = []
    for hit in hits:
        same = [p for p in out if p["label"] == hit["label"] and hit["t"] - p["t"] < min_gap]
        if same:
            continue
        out.append(hit)
    return out


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------


class BrollLibrary:
    """Resolve assunto -> imagem licenciada, local primeiro e Openverse depois."""

    def __init__(
        self,
        local_dir: Path | None = None,
        cache_dir: Path | None = None,
        *,
        allow_network: bool = False,
        timeout_s: float = 10.0,
        max_download_bytes: int = 8_000_000,
    ) -> None:
        self.local_dir = Path(local_dir or DEFAULT_LOCAL_DIR)
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.allow_network = allow_network
        self.timeout_s = timeout_s
        self.max_download_bytes = max_download_bytes
        self._failed_labels: set[str] = set()

    # ------------------------------------------------------------------ local

    def _local_candidates(self, label: str) -> list[Path]:
        """Imagens do usuário para um assunto: assets/broll/<label>/*.jpg."""
        folder = self.local_dir / label
        if not folder.is_dir():
            return []
        exts = {".jpg", ".jpeg", ".png", ".webp"}
        return sorted(p for p in folder.iterdir() if p.suffix.lower() in exts)

    def _pick_local(self, label: str, seed: int) -> BrollAsset | None:
        candidates = self._local_candidates(label)
        if not candidates:
            return None
        # Escolha determinística: mesma seed -> mesma imagem.
        chosen = candidates[seed % len(candidates)]
        return BrollAsset(
            path=str(chosen),
            label=label,
            source="local",
            license="user-supplied",
            attribution="acervo local do operador",
            score=1.0,  # local vence a rede por decisão do operador
        )

    # --------------------------------------------------------------- openverse

    def _search_openverse(self, label: str, limit: int = 5) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(
            {
                "q": label,
                "license_type": "commercial",
                "page_size": limit,
                "mature": "false",
            }
        )
        req = urllib.request.Request(
            f"{OPENVERSE_API}?{query}",
            headers={"User-Agent": "shorts-factory/0.2 (broll; contato: local)"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("results") or []

    def _download(self, url: str, dest: Path) -> bool:
        """Baixa uma imagem com limite de tamanho."""
        req = urllib.request.Request(url, headers={"User-Agent": "shorts-factory/0.2 (broll)"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = resp.read(self.max_download_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("broll: download falhou (%s): %s", url[:60], exc)
            return False

        if len(data) > self.max_download_bytes:
            log.warning("broll: imagem grande demais (>%d bytes)", self.max_download_bytes)
            return False
        if len(data) < 1024:
            log.warning("broll: download suspeito de vazio")
            return False

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True

    def _fetch_openverse(self, label: str, seed: int) -> BrollAsset | None:
        if not self.allow_network or label in self._failed_labels:
            return None

        try:
            results = self._search_openverse(label)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            log.warning("broll: Openverse indisponível para '%s': %s", label, exc)
            self._failed_labels.add(label)
            return None

        usable = [
            r
            for r in results
            if str(r.get("license", "")).lower() in COMMERCIAL_LICENSES and (r.get("url") or "").startswith("http")
        ]
        if not usable:
            log.info("broll: nenhuma imagem com licença comercial para '%s'", label)
            self._failed_labels.add(label)
            return None

        result = usable[seed % len(usable)]
        url = result["url"]
        suffix = Path(urllib.parse.urlparse(url).path).suffix or ".jpg"
        key = hashlib.sha256(f"{label}:{url}".encode()).hexdigest()[:16]
        dest = self.cache_dir / f"{label}_{key}{suffix}"

        if not dest.exists() and not self._download(url, dest):
            self._failed_labels.add(label)
            return None

        creator = result.get("creator") or "desconhecido"
        return BrollAsset(
            path=str(dest),
            label=label,
            source="openverse",
            license=f"{result.get('license', '?')}/{result.get('license_version', '')}".rstrip("/"),
            attribution=f"{creator} — {result.get('provider', '?')} (Openverse)",
            width=int(result.get("width") or 0),
            height=int(result.get("height") or 0),
            score=0.8,
        )

    # ------------------------------------------------------------------ API

    def get(self, label: str, seed: int = 0) -> BrollAsset | None:
        """Melhor imagem para um assunto. Local primeiro, rede depois."""
        local = self._pick_local(label, seed)
        if local is not None:
            return local
        return self._fetch_openverse(label, seed)


# ---------------------------------------------------------------------------
# Planejamento: assunto + fala -> placements no corte
# ---------------------------------------------------------------------------


def plan_placements(
    words: list[dict[str, Any]],
    cut_start: float,
    cut_end: float,
    labels: dict[str, list[str]],
    library: BrollLibrary,
    *,
    min_interval: float = 5.0,
    max_placements: int = 4,
    duration: float = 2.5,
) -> list[BrollPlacement]:
    """Decide quais imagens entram, onde e por quanto tempo.

    Regras que evitam poluição visual:
      - no máximo `max_placements` por corte;
      - nunca dois placements a menos de `min_interval` segundos;
      - o placement respeita os limites do corte;
      - a legenda manda: o overlay fica no topo, longe do texto.
    """
    inside = [w for w in words if cut_start <= float(w.get("start", 0)) < cut_end]
    if not inside:
        return []

    topics = extract_topics(inside, labels)
    if not topics:
        return []

    placements: list[BrollPlacement] = []
    last_end = -999.0

    for idx, topic in enumerate(topics):
        if len(placements) >= max_placements:
            break

        t_abs = topic["t"]
        if t_abs - cut_start < min_interval and placements:
            continue
        if placements and t_abs - last_end < min_interval:
            continue

        asset = library.get(topic["label"], seed=idx)
        if asset is None:
            continue

        # Relativo ao corte, nunca fora dos limites.
        t_start = max(0.0, t_abs - cut_start)
        t_end = min(cut_end - cut_start, t_start + duration)
        if t_end - t_start < 1.0:
            continue

        placements.append(
            BrollPlacement(
                asset=asset,
                t_start=t_start,
                t_end=t_end,
                trigger_text=topic["word"],
                corner="top",
            )
        )
        last_end = t_abs + duration

    return placements


def build_catalog_entry(placements: list[BrollPlacement]) -> dict[str, Any]:
    """Registro auditável do que foi usado e sob qual licença."""
    return {
        "count": len(placements),
        "assets": [p.asset.to_dict() for p in placements],
        "licenses": sorted({p.asset.license for p in placements}),
    }
