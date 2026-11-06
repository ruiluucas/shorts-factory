"""Detecção de momentos virais (scoring 0-100) e geração de EditPlan.

Consome os dicts de transcript/áudio (`transcript_analysis`) e de visão
(`vision_analysis`) produzidos pelos módulos paralelos e devolve
`ViralCandidate` / `EditPlan` prontos para o Remotion.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # import normal quando rodando da raiz do projeto
    from core.schemas import CategoryEnum, EditPlan, ViralCandidate, WordSegment
except ImportError:  # pragma: no cover - fallback quando core/ está no sys.path
    from schemas import CategoryEnum, EditPlan, ViralCandidate, WordSegment  # type: ignore

try:
    import yaml
except ImportError:  # pragma: no cover - yaml é dependência declarada
    yaml = None  # type: ignore

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pipeline_config.yaml"

# Pesos/penalidades de segurança caso o config venha incompleto.
_FALLBACK_WEIGHTS: Dict[str, float] = {
    "reaction_intensity": 0.25,
    "surprise_and_novelty": 0.20,
    "narrative_completion": 0.20,
    "event_clarity": 0.15,
    "editing_potential": 0.10,
    "audiovisual_quality": 0.10,
}
_FALLBACK_PENALTIES: Dict[str, float] = {
    "lacks_context": -30.0,
    "poor_audio": -25.0,
    "passive_gameplay_cutscene": -40.0,
    "excessive_repetition": -20.0,
}

# ---------------------------------------------------------------------------
# Léxicos PT-BR (normalizados: sem acento, minúsculas)
# ---------------------------------------------------------------------------
REACTION_WORDS = [
    "caralho", "mano", "olha", "meu deus", "nao acredito", "que isso", "puta",
    "vei", "serio", "oloco", "rapaz", "eita", "nossa", "caraca", "porra",
    "mds", "deus", "uau", "wow", "cara", "vish", "ixi", "louco", "insano",
]
SURPRISE_WORDS = [
    "nossa", "nunca", "impossivel", "inacreditavel", "impressionante", "serio",
    "jura", "verdade", "bugou", "apareceu", "do nada", "olha isso", "primeira vez",
    "que isso", "nao acredito", "uau", "wow", "surpresa", "inesperado",
]
FAIL_WORDS = ["caiu", "morreu", "perdi", "errei", "falhei", "deu ruim", "quebrou", "acabou", "game over", "cai", "morri"]
CHAOS_WORDS = ["tudo", "bomba", "explodiu", "corre", "foge", "policia", "perseguicao", "tiro", "fogo", "panico", "desespero"]
DISCOVERY_WORDS = ["achei", "descobri", "encontrei", "escondido", "secreto", "novo", "truque", "dica", "esse lugar", "olha isso"]
TENSION_WORDS = ["medo", "cuidado", "quase", "vai", "calma", "silencio", "espera", "perigo", "nervoso", "tenso"]
FUNNY_LAUGH = ["kkk", "haha", "rs", "lol", "morri de rir", "engracado", "piada"]

_STOPWORDS = {
    "a", "o", "e", "de", "que", "do", "da", "em", "um", "uma", "para", "com",
    "nao", "se", "por", "mais", "as", "os", "no", "na", "eu", "tu", "ele",
    "ela", "voce", "meu", "minha", "ai", "ja", "so", "aqui", "ali", "isso",
}
_VERB_HINTS = [
    "e", "ta", "esta", "foi", "vai", "vou", "tem", "temos", "faz", "fez",
    "olha", "olhei", "vi", "vou", "vamos", "chegou", "pega", "peguei", "cai",
    "caiu", "morre", "morreu", "deu", "vai", "explodiu", "encontrei", "achei",
    "corre", "vem", "sai", "saiu",
]


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def _norm(text: Any) -> str:
    """Minúsculas sem acento e sem pontuação, para casar léxicos."""
    if not isinstance(text, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^\w\s]", " ", stripped.lower()).strip()


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Limita value ao intervalo [low, high]; tolera NaN."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return low
    if v != v:  # NaN
        return low
    return max(low, min(high, v))


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Converte para float com fallback."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_section(config: Dict[str, Any], name: str) -> Dict[str, Any]:
    section = config.get(name) if isinstance(config, dict) else None
    return section if isinstance(section, dict) else {}


def _weights(config: Dict[str, Any]) -> Dict[str, float]:
    """Pesos normalizados para somar 1.0 (config manda, defaults completam)."""
    raw = _get_section(config, "scoring_weights")
    merged = {k: _safe_float(raw.get(k), v) for k, v in _FALLBACK_WEIGHTS.items()}
    total = sum(max(0.0, v) for v in merged.values())
    if total <= 0:
        return dict(_FALLBACK_WEIGHTS)
    return {k: max(0.0, v) / total for k, v in merged.items()}


def _penalties(config: Dict[str, Any]) -> Dict[str, float]:
    """Penalidades do config como valores negativos."""
    raw = _get_section(config, "penalties")
    out: Dict[str, float] = {}
    for key, default in _FALLBACK_PENALTIES.items():
        val = _safe_float(raw.get(key), default)
        out[key] = -abs(val)
    return out


def _iter_words(transcript: Any) -> List[Dict[str, Any]]:
    """Extrai palavras válidas (word/start/end numéricos) de um transcript."""
    if not isinstance(transcript, dict):
        return []
    words = transcript.get("words")
    if not isinstance(words, list):
        return []
    out: List[Dict[str, Any]] = []
    for w in words:
        if not isinstance(w, dict):
            continue
        word = str(w.get("word", "")).strip()
        if not word:
            continue
        start = _safe_float(w.get("start"), float("nan"))
        end = _safe_float(w.get("end"), float("nan"))
        if start != start or end != end or end < start:
            continue
        out.append(
            {
                "word": word,
                "start": max(0.0, start),
                "end": max(start, end),
                "score": _clamp(_safe_float(w.get("score"), 0.5)),
                "speaker": w.get("speaker") if isinstance(w.get("speaker"), str) else None,
            }
        )
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out


def _to_word_segments(words: Sequence[Dict[str, Any]]) -> List[WordSegment]:
    """Converte dicts de palavra em WordSegment (pydantic)."""
    segments: List[WordSegment] = []
    for w in words:
        segments.append(
            WordSegment(
                word=w["word"],
                start=float(w["start"]),
                end=float(w["end"]),
                score=_clamp(_safe_float(w.get("score"), 0.5)),
                speaker=w.get("speaker"),
            )
        )
    return segments


def _audio_events(transcript: Any) -> Dict[str, Any]:
    ev = transcript.get("audio_events") if isinstance(transcript, dict) else None
    return ev if isinstance(ev, dict) else {}


def _peaks(transcript: Any) -> List[Dict[str, float]]:
    """Picos de áudio normalizados para [{start,end,energy}]."""
    out: List[Dict[str, float]] = []
    for p in _audio_events(transcript).get("peaks") or []:
        if not isinstance(p, dict):
            continue
        start = _safe_float(p.get("start"), float("nan"))
        end = _safe_float(p.get("end"), float("nan"))
        if start != start or end != end or end < start:
            continue
        out.append({"start": start, "end": end, "energy": _clamp(_safe_float(p.get("energy"), 0.5))})
    out.sort(key=lambda x: x["start"])
    return out


def _samples(vision: Any) -> List[Dict[str, Any]]:
    if not isinstance(vision, dict):
        return []
    samples = vision.get("samples")
    return [s for s in samples if isinstance(s, dict)] if isinstance(samples, list) else []


def _samples_in_range(vision: Any, start: float, end: float) -> List[Dict[str, Any]]:
    """Amostras visuais cujo t cai na janela (tolerância de 1 amostra)."""
    out = []
    for s in _samples(vision):
        t = _safe_float(s.get("t"), float("nan"))
        if t != t:
            continue
        if start - 0.001 <= t <= end + 0.001:
            out.append(s)
    return out


def _in_window(start: float, end: float, t: float) -> bool:
    return start - 0.001 <= t <= end + 0.001


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Interseção de dois intervalos (0 se não houver)."""
    return max(0.0, min(a1, b1) - max(a0, b0))


def _iou(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """IoU temporal 1-D."""
    inter = _overlap(a[0], a[1], b[0], b[1])
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# 1. Config
# ---------------------------------------------------------------------------
def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Carrega o pipeline_config.yaml (path explícito ou o do projeto)."""
    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if yaml is None:
        logger.warning("PyYAML ausente: usando config padrão em memória")
        return {
            "scoring_weights": dict(_FALLBACK_WEIGHTS),
            "penalties": dict(_FALLBACK_PENALTIES),
            "crop_modes": {"default": "split_screen", "fallback": "gameplay_centered"},
            "project": {"resolution": {"width": 1080, "height": 1920}},
        }
    if not cfg_path.exists():
        logger.warning("config não encontrado em %s; usando defaults", cfg_path)
        return {"scoring_weights": dict(_FALLBACK_WEIGHTS), "penalties": dict(_FALLBACK_PENALTIES)}
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        logger.warning("config inválido (%s); usando dict vazio", cfg_path)
        return {}
    logger.debug("config carregado de %s", cfg_path)
    return data


# ---------------------------------------------------------------------------
# 2. Janelas candidatas
# ---------------------------------------------------------------------------
def _is_sentence_end(word: str) -> bool:
    return bool(re.search(r"[.!?…]+[\"')\]]*$", word))


def build_windows(
    transcript: Dict[str, Any],
    min_dur: float = 8.0,
    max_dur: float = 45.0,
    target_dur: float = 25.0,
) -> List[Dict[str, Any]]:
    """Agrupa palavras em janelas respeitando fins de sentença (com overlap)."""
    words = _iter_words(transcript)
    if not words:
        logger.warning("build_windows: transcript sem palavras")
        return []

    min_dur = max(0.5, _safe_float(min_dur, 8.0))
    max_dur = max(min_dur, _safe_float(max_dur, 45.0))
    target_dur = _clamp(_safe_float(target_dur, 25.0), min_dur, max_dur)

    # Índices que começam uma sentença. Preferência: após pontuação .!?.
    # Pausa/gap só entra se o transcript não tiver pontuação alguma.
    n = len(words)
    candidates: List[Tuple[int, int, bool]] = []  # (first_idx, last_idx, clean_boundary)
    punct_starts: set[int] = {0}
    for idx in range(1, n):
        if _is_sentence_end(words[idx - 1]["word"]):
            punct_starts.add(idx)
    if len(punct_starts) > 1:
        starts_set = punct_starts
    else:
        starts_set = {0}
        gaps = sorted(words[i]["start"] - words[i - 1]["end"] for i in range(1, n)) or [0.0]
        gap_thr = max(0.35, gaps[len(gaps) // 2] * 2.5 + 0.05)
        for idx in range(1, n):
            if words[idx]["start"] - words[idx - 1]["end"] >= gap_thr:
                starts_set.add(idx)
    sentence_starts = sorted(starts_set)

    starts_in_range = [s for s in sentence_starts if words[s]["start"] <= words[-1]["end"] - min_dur]
    if not starts_in_range:
        starts_in_range = [0]

    for si in starts_in_range:
        win_start = words[si]["start"]
        # Fecha na última palavra antes da fronteira seguinte mais próxima do alvo.
        best_close: Optional[int] = None
        best_dist = float("inf")
        nxt_starts = [s for s in sentence_starts if s > si] + [n]
        for s in nxt_starts:
            close = s - 1
            if close <= si:
                continue
            dur = words[close]["end"] - win_start
            if dur < min_dur or dur > max_dur:
                continue
            dist = abs(dur - target_dur)
            if dist < best_dist:
                best_dist, best_close = dist, close
            if dur >= target_dur:  # já passou do alvo: candidatos seguintes só crescem
                break
        if best_close is None:
            # Nenhuma fronteira cabível: estende até caber o mínimo.
            close = si
            while close + 1 < n and (words[close]["end"] - win_start < min_dur
                                     or words[close + 1]["end"] - win_start <= max_dur):
                close += 1
            if close <= si or words[close]["end"] - win_start < min_dur:
                continue
            best_close = close
        candidates.append((si, best_close, _is_sentence_end(words[best_close]["word"])))

    # Fallback: sem janelas válidas (ex.: 1-2 palavras longas) → usa tudo.
    if not candidates and n >= 1:
        candidates.append((0, n - 1, _is_sentence_end(words[-1]["word"])))

    windows: List[Dict[str, Any]] = []
    seen: set[Tuple[float, float]] = set()
    for first, last, _clean in candidates:
        start_t = words[first]["start"]
        end_t = words[last]["end"]
        key = (round(start_t, 2), round(end_t, 2))
        if key in seen:
            continue
        seen.add(key)
        windows.append(
            {
                "start": float(start_t),
                "end": float(end_t),
                "words": _to_word_segments(words[first : last + 1]),
            }
        )

    windows.sort(key=lambda w: w["start"])
    logger.info("build_windows: %d janelas de %d palavras", len(windows), n)
    return windows


# ---------------------------------------------------------------------------
# 3. Scoring
# ---------------------------------------------------------------------------
def _count_keywords(text: str, keywords: Iterable[str]) -> int:
    return sum(1 for kw in keywords if kw and kw in text)


def _window_text(window: Dict[str, Any]) -> str:
    parts = []
    for w in window.get("words", []):
        parts.append(w.word if isinstance(w, WordSegment) else str(w.get("word", "")))
    return _norm(" ".join(parts))


def _audio_energy(window: Dict[str, Any], transcript: Any) -> float:
    """Energia média dos picos dentro da janela (0 se não houver peaks)."""
    start, end = window["start"], window["end"]
    energies = [p["energy"] for p in _peaks(transcript) if _in_window(start, end, (p["start"] + p["end"]) / 2)]
    if energies:
        return sum(energies) / len(energies)
    # Fallback: RMS bruto na janela, se a série existir.
    ev = _audio_events(transcript)
    rms = ev.get("rms_series")
    duration = _safe_float(_safe_float(transcript.get("duration") if isinstance(transcript, dict) else 0), 0)
    if isinstance(rms, list) and rms and duration > 0:
        per_sample = duration / len(rms)
        idx = [i for i in range(len(rms)) if _in_window(start, end, i * per_sample)]
        if idx:
            vals = [_clamp(_safe_float(rms[i], 0.0)) for i in idx]
            return sum(vals) / len(vals)
    return 0.0


def _motion_in_window(window: Dict[str, Any], vision: Any) -> float:
    """Movimento médio nas amostras da janela (0 se não houver visão)."""
    start, end = window["start"], window["end"]
    vals = [_clamp(_safe_float(s.get("motion"), 0.0)) for s in _samples_in_range(vision, start, end)]
    if not vals:
        mean_motion = _safe_float(vision.get("mean_motion") if isinstance(vision, dict) else 0.0, 0.0)
        return _clamp(mean_motion)
    return sum(vals) / len(vals)


def _passive_ratio_in_window(window: Dict[str, Any], vision: Any) -> Optional[float]:
    """passive_ratio efetivo da janela; None quando não há dado visual."""
    samples = _samples_in_range(vision, window["start"], window["end"])
    if samples:
        passive_flags = []
        for s in samples:
            scene = str(s.get("scene", "")).lower()
            motion = _clamp(_safe_float(s.get("motion"), 0.0))
            passive_flags.append(1.0 if ("cutscene" in scene or "passive" in scene or motion < 0.05) else 0.0)
        return sum(passive_flags) / len(passive_flags)
    if isinstance(vision, dict) and vision.get("passive_ratio") is not None:
        return _clamp(_safe_float(vision.get("passive_ratio"), 0.0))
    return None


def _motion_variation(window: Dict[str, Any], vision: Any) -> float:
    """Desvio padrão do motion normalizado = potencial de corte/câmera."""
    vals = [_clamp(_safe_float(s.get("motion"), 0.0)) for s in _samples_in_range(vision, window["start"], window["end"])]
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return _clamp((var ** 0.5) / 0.35)  # 0.35 ≈ variação "rica"


def _starts_mid_sentence(window: Dict[str, Any], words: Sequence[Dict[str, Any]]) -> bool:
    """True se a janela começa logo após uma palavra sem fim de sentença."""
    if not words:
        return False
    start = window["start"]
    first_idx = None
    for i, w in enumerate(words):
        if w["start"] >= start - 0.05:
            first_idx = i
            break
    if first_idx is None or first_idx == 0:
        return False
    prev = words[first_idx - 1]
    return not _is_sentence_end(prev["word"])


def _repetition_ratio(window: Dict[str, Any]) -> float:
    """Fração de conteúdo coberto por palavras repetidas (fora de stopwords)."""
    tokens = [_norm(w.word if isinstance(w, WordSegment) else str(w.get("word", ""))) for w in window.get("words", [])]
    tokens = [t for t in tokens if t and t not in _STOPWORDS]
    if len(tokens) < 4:
        return 0.0
    counts: Dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    repeated = sum(c for c in counts.values() if c > 1)
    return repeated / len(tokens)


def _has_subject_verb(text: str) -> bool:
    """Heurística: presença de pronome/sujeito + verbo frequente PT-BR."""
    has_subject = any(re.search(rf"\b{re.escape(s)}\b", text) for s in ("eu", "voce", "ele", "ela", "a gente", "nos", "o cara", "o cara"))
    has_verb = any(re.search(rf"\b{re.escape(v)}\b", text) for v in _VERB_HINTS)
    return has_subject and has_verb


def score_window(
    window: Dict[str, Any],
    transcript: Dict[str, Any],
    vision: Optional[Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Calcula as 6 dimensões (0-1), a nota final 0-100 e as penalidades."""
    weights = _weights(config)
    pen = _penalties(config)

    start = _safe_float(window.get("start"), 0.0)
    end = _safe_float(window.get("end"), start)
    duration = max(0.0, end - start)
    text = _window_text(window)
    words = _iter_words(window if "words" in window else {"words": []})
    # Aceita tanto dicts quanto WordSegment na janela.
    if not words and window.get("words"):
        words = [
            {"word": w.word, "start": w.start, "end": w.end, "score": w.score, "speaker": w.speaker}
            if isinstance(w, WordSegment)
            else w
            for w in window["words"]
        ]
    n_words = len(words)
    wps = (n_words / duration) if duration > 0 else 0.0

    peaks_in = [p for p in _peaks(transcript) if _in_window(start, end, (p["start"] + p["end"]) / 2)]
    peak_density = _clamp(len(peaks_in) / max(1.0, duration / 10.0))  # ~1 pico/10s = saturação
    energy = _clamp(_audio_energy(window, transcript))
    motion = _motion_in_window(window, vision)
    passive = _passive_ratio_in_window(window, vision)

    ev = _audio_events(transcript)
    risk_flags: List[str] = []

    # --- reaction_intensity
    n_reaction = _count_keywords(text, [_norm(k) for k in REACTION_WORDS])
    reaction_lex = _clamp(n_reaction / max(1.0, duration / 10.0) / 2.0)  # 2 hits/10s satura
    reaction_intensity = _clamp(0.60 * reaction_lex + 0.25 * energy + 0.15 * peak_density)

    # --- surprise_and_novelty
    n_surprise = _count_keywords(text, [_norm(k) for k in SURPRISE_WORDS])
    surprise_lex = _clamp(n_surprise / 2.0)
    surprise_and_novelty = _clamp(0.45 * surprise_lex + 0.30 * peak_density + 0.25 * motion)

    # --- narrative_completion
    clean_start = not _starts_mid_sentence(window, _iter_words(transcript))
    last_word = words[-1]["word"] if words else ""
    clean_end = _is_sentence_end(last_word) if last_word else False
    dur_fit = 1.0 - min(1.0, abs(duration - 25.0) / 25.0)
    sentence_q = _clamp((0.5 if clean_start else 0.0) + (0.5 if clean_end else 0.0))
    narrative_completion = _clamp(0.55 * sentence_q + 0.45 * dur_fit)

    # --- event_clarity
    # 2.0–4.5 palavras/s é a faixa "inteligível mas animada".
    if wps <= 0:
        density_q = 0.0
    elif wps < 2.0:
        density_q = wps / 2.0
    elif wps <= 4.5:
        density_q = 1.0
    else:
        density_q = _clamp(1.0 - (wps - 4.5) / 4.5)
    grammar_q = 1.0 if _has_subject_verb(text) else (0.6 if n_words >= 5 else 0.3)
    event_clarity = _clamp(0.7 * density_q + 0.3 * grammar_q)

    # --- editing_potential
    sfx_q = _clamp(len(peaks_in) / 3.0)
    variation_q = _motion_variation(window, vision)
    editing_potential = _clamp(0.6 * sfx_q + 0.4 * variation_q)

    # --- audiovisual_quality
    clipping = _clamp(_safe_float(ev.get("clipping_ratio"), 0.0))
    clip_q = _clamp(1.0 - clipping / 0.05)  # 5% de clipping = zero
    mean_rms = _safe_float(ev.get("mean_rms"), 0.0)
    if mean_rms <= 0:
        rms_q = 0.5 if not ev else 0.3
    else:
        # Saudável em ~0.15-0.35; penaliza mudo e estourado.
        rms_q = _clamp(1.0 - abs(mean_rms - 0.25) / 0.35)
    passive_q = 1.0 if passive is None else _clamp(1.0 - passive)
    audiovisual_quality = _clamp(0.45 * clip_q + 0.30 * rms_q + 0.25 * passive_q)

    components = {
        "reaction_intensity": _clamp(reaction_intensity),
        "surprise_and_novelty": _clamp(surprise_and_novelty),
        "narrative_completion": _clamp(narrative_completion),
        "event_clarity": _clamp(event_clarity),
        "editing_potential": _clamp(editing_potential),
        "audiovisual_quality": _clamp(audiovisual_quality),
    }
    base_score = 100.0 * sum(components[k] * weights.get(k, 0.0) for k in components)

    # ----------------------------- penalidades -----------------------------
    penalties_applied: List[str] = []
    total_penalty = 0.0

    def apply(name: str) -> None:
        nonlocal total_penalty
        total_penalty += pen[name]
        penalties_applied.append(name)

    # passive_gameplay_cutscene
    scene_tally: Dict[str, int] = {}
    for s in _samples_in_range(vision, start, end):
        scene = str(s.get("scene") or "unknown")
        scene_tally[scene] = scene_tally.get(scene, 0) + 1
    n_samples = sum(scene_tally.values())
    passive_scene_majority = bool(
        n_samples and ("cutscene_passive" in scene_tally or "passive" in scene_tally)
        and (scene_tally.get("cutscene_passive", 0) + scene_tally.get("passive", 0)) >= 0.5 * n_samples
    )
    if passive is not None and passive >= 0.65 or passive_scene_majority:
        apply("passive_gameplay_cutscene")
        risk_flags.append("passive_gameplay")
        risk_flags.append("cutscene_no_facecam")

    # poor_audio
    if bool(ev.get("is_quiet")) or clipping >= 0.03:
        apply("poor_audio")
        risk_flags.append("poor_audio" if not bool(ev.get("is_quiet")) else "audio_too_quiet")
        if clipping >= 0.03:
            risk_flags.append("audio_clipping")

    # lacks_context
    if _starts_mid_sentence(window, _iter_words(transcript)):
        apply("lacks_context")
        risk_flags.append("starts_mid_sentence")

    # excessive_repetition
    rep = _repetition_ratio(window)
    if rep >= 0.45:
        apply("excessive_repetition")
        risk_flags.append("excessive_repetition")

    if duration > 45.0:
        risk_flags.append("too_long")
    if duration < 8.0:
        risk_flags.append("too_short")
    if not isinstance(vision, dict) or not vision:
        risk_flags.append("no_vision_analysis")
    if not peaks_in:
        risk_flags.append("no_audio_peaks")

    final_score = _clamp(base_score + total_penalty, 0.0, 100.0)

    return {
        "score": round(final_score, 2),
        "base_score": round(_clamp(base_score, 0.0, 100.0), 2),
        "components": {k: round(v, 4) for k, v in components.items()},
        "penalties_applied": penalties_applied,
        "penalty_total": round(total_penalty, 2),
        "risk_flags": sorted(set(risk_flags)),
        "duration": round(duration, 3),
    }


# ---------------------------------------------------------------------------
# 4. Categoria
# ---------------------------------------------------------------------------
def classify_category(window: Dict[str, Any], scoring: Dict[str, Any]) -> CategoryEnum:
    """Mapeia a janela para uma categoria viral por léxico + componentes."""
    text = _window_text(window)
    comps = scoring.get("components") or {}
    flags = set(scoring.get("risk_flags") or [])

    scores: Dict[CategoryEnum, float] = {
        CategoryEnum.FUNNY: 1.2 * _count_keywords(text, FUNNY_LAUGH) + 0.6 * _count_keywords(text, [_norm(k) for k in REACTION_WORDS]),
        CategoryEnum.FAIL: 1.6 * _count_keywords(text, [_norm(k) for k in FAIL_WORDS]) + (0.5 if "poor_audio" in flags else 0.0),
        CategoryEnum.CHAOS: 1.4 * _count_keywords(text, [_norm(k) for k in CHAOS_WORDS]) + 0.8 * _safe_float(comps.get("surprise_and_novelty")),
        CategoryEnum.REACTION: 1.5 * _count_keywords(text, [_norm(k) for k in REACTION_WORDS]) + 0.8 * _safe_float(comps.get("reaction_intensity")),
        CategoryEnum.SURPRISE: 1.5 * _count_keywords(text, [_norm(k) for k in SURPRISE_WORDS]) + 0.8 * _safe_float(comps.get("surprise_and_novelty")),
        CategoryEnum.DISCOVERY: 1.5 * _count_keywords(text, [_norm(k) for k in DISCOVERY_WORDS]),
        CategoryEnum.TENSION: 1.3 * _count_keywords(text, [_norm(k) for k in TENSION_WORDS]) + 0.5 * (1.0 - _safe_float(comps.get("editing_potential"))),
    }
    best = max(scores.items(), key=lambda kv: (kv[1], -list(CategoryEnum).index(kv[0])))
    if best[1] <= 0.3:
        # Sem evidência léxica: usa a dimensão dominante.
        dominant = max(comps.items(), key=lambda kv: kv[1])[0] if comps else ""
        return {
            "reaction_intensity": CategoryEnum.REACTION,
            "surprise_and_novelty": CategoryEnum.SURPRISE,
            "event_clarity": CategoryEnum.TENSION,
        }.get(dominant, CategoryEnum.CHAOS)
    return best[0]


# ---------------------------------------------------------------------------
# 5. Pipeline
# ---------------------------------------------------------------------------
def _make_id(index: int, start: float, end: float) -> str:
    return f"cand_{index:03d}_{int(round(start * 1000)):07d}_{int(round(end * 1000)):07d}"


def _hook_text(window: Dict[str, Any], max_words: int = 10) -> str:
    """Primeiras ~10 palavras da janela (gancho do thumbnail/legenda)."""
    words = [
        (w.word if isinstance(w, WordSegment) else str(w.get("word", "")))
        for w in window.get("words", [])
    ]
    hook = " ".join(w for w in words[:max_words] if w).strip()
    if len(words) > max_words:
        hook += "..."
    return hook[:140] or "(sem transcrição)"


def _justification(scoring: Dict[str, Any], category: CategoryEnum, duration: float) -> str:
    comps = scoring.get("components", {})
    top = sorted(comps.items(), key=lambda kv: kv[1], reverse=True)[:2]
    desc = ", ".join(f"{k}={v:.2f}" for k, v in top)
    pen = scoring.get("penalties_applied") or []
    pen_txt = f" penalidades: {', '.join(pen)}" if pen else " sem penalidades"
    return (
        f"Categoria {category.value} em {duration:.1f}s; forças: {desc}; "
        f"nota base {scoring.get('base_score', scoring.get('score')):.1f} → {scoring['score']:.1f}.{pen_txt}"
    )


def detect_candidates(
    transcript: Dict[str, Any],
    vision: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    top_k: int = 10,
) -> List[ViralCandidate]:
    """Pipeline completo: janelas → score → NMS → top_k ViralCandidate."""
    cfg = config if isinstance(config, dict) and config else load_config()
    if not isinstance(transcript, dict):
        logger.warning("detect_candidates: transcript inválido (%r)", type(transcript))
        return []

    windows = build_windows(transcript)
    if not windows:
        return []

    durations = [_safe_float(w["end"]) - _safe_float(w["start"]) for w in windows]
    scored: List[Tuple[Dict[str, Any], Dict[str, Any], float]] = []
    for w in windows:
        try:
            sc = score_window(w, transcript, vision, cfg)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.exception("score_window falhou para janela %s-%s: %s", w["start"], w["end"], exc)
            continue
        dur = _safe_float(w["end"]) - _safe_float(w["start"])
        norm = sc["score"] * (0.7 + 0.3 * _clamp(dur / max(1.0, max(durations))))  # prefere janelas ricas
        scored.append((w, sc, norm))

    scored.sort(key=lambda x: x[2], reverse=True)

    # NMS por IoU temporal
    kept: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for w, sc, _norm in scored:
        span = (_safe_float(w["start"]), _safe_float(w["end"]))
        if any(_iou(span, (_safe_float(k["start"]), _safe_float(k["end"]))) > 0.45 for k, _ in kept):
            continue
        kept.append((w, sc))
        if len(kept) >= max(1, int(top_k)):
            break

    candidates: List[ViralCandidate] = []
    for idx, (w, sc) in enumerate(kept, start=1):
        start, end = _safe_float(w["start"]), _safe_float(w["end"])
        category = classify_category(w, sc)
        comps = sc.get("components", {})
        # Confidence: qualidade da nota + dispersão (componentes todos altos = confiável)
        strong = sum(1 for v in comps.values() if v >= 0.5) / max(1, len(comps))
        confidence = _clamp(0.6 * (sc["score"] / 100.0) + 0.4 * strong)
        try:
            candidates.append(
                ViralCandidate(
                    candidate_id=_make_id(idx, start, end),
                    start_time=round(start, 3),
                    end_time=round(end, 3),
                    duration=round(max(0.0, end - start), 3),
                    category=category,
                    score=float(sc["score"]),
                    hook_text=_hook_text(w),
                    justification=_justification(sc, category, end - start),
                    risk_flags=list(sc.get("risk_flags", [])),
                    confidence=round(confidence, 3),
                )
            )
        except Exception as exc:  # pragma: no cover - validação pydantic
            logger.error("candidato descartado (validação): %s", exc)

    logger.info(
        "detect_candidates: %d janelas → %d candidatos (top score %.1f)",
        len(windows),
        len(candidates),
        candidates[0].score if candidates else 0.0,
    )
    return candidates


# ---------------------------------------------------------------------------
# 6. EditPlan
# ---------------------------------------------------------------------------
def _sfx_type(peak: Dict[str, float], prev: Optional[Dict[str, float]]) -> str:
    """Classifica o pico: riser (crescimento), whoosh (rápido) ou impact."""
    dur = max(0.0, peak["end"] - peak["start"])
    if prev is not None and peak["energy"] > prev["energy"] + 0.15:
        return "riser"
    if dur <= 0.35:
        return "impact"
    return "whoosh"


def build_edit_plan(
    job_id: str,
    candidate: ViralCandidate,
    transcript: Dict[str, Any],
    vision: Optional[Dict[str, Any]],
    config: Dict[str, Any],
    source_video_path: str,
) -> EditPlan:
    """Monta o EditPlan (words, crop, sfx) de um candidato para o Remotion."""
    cfg = config if isinstance(config, dict) else {}
    start = _safe_float(candidate.start_time, 0.0)
    end = _safe_float(candidate.end_time, start)

    # Palavras dentro da janela, com tempos RELATIVOS ao início do corte.
    words_in: List[WordSegment] = []
    for w in _iter_words(transcript):
        overlap = _overlap(w["start"], w["end"], start, end)
        if overlap <= 0:
            continue
        new_start = max(0.0, w["start"] - start)
        new_end = max(new_start + 0.01, min(end, w["end"]) - start)
        words_in.append(
            WordSegment(
                word=w["word"],
                start=round(new_start, 3),
                end=round(new_end, 3),
                score=_clamp(_safe_float(w.get("score"), 0.5)),
                speaker=w.get("speaker"),
            )
        )

    # crop_mode: visão recomenda > fallback do config quando não há visão > default.
    crop_modes = _get_section(cfg, "crop_modes")
    recommended = None
    if isinstance(vision, dict):
        recommended = vision.get("recommended_crop_mode") or vision.get("crop_mode")
    crop_mode = str(
        recommended
        or crop_modes.get("fallback")
        or crop_modes.get("default")
        or "gameplay_centered"
    )

    template = "GTASplitScreen" if crop_mode == "split_screen" else "GTACinematic"

    # SFX: peaks dentro da janela, em tempo relativo.
    sfx_triggers: List[Dict[str, Any]] = []
    peaks_in = [p for p in _peaks(transcript) if _in_window(start, end, (p["start"] + p["end"]) / 2)]
    prev: Optional[Dict[str, float]] = None
    for p in peaks_in:
        t_rel = round(max(0.0, p["start"] - start), 3)
        sfx_triggers.append(
            {
                "t": t_rel,
                "type": _sfx_type(p, prev),
                "intensity": round(_clamp(p["energy"]), 3),
            }
        )
        prev = p

    plan = EditPlan(
        job_id=str(job_id),
        composition_template=template,
        candidate_id=candidate.candidate_id,
        source_video_path=str(source_video_path or (transcript.get("video_path") if isinstance(transcript, dict) else "") or ""),
        output_duration_seconds=round(max(0.1, end - start), 3),
        crop_mode=crop_mode,
        words=words_in,
        sfx_triggers=sfx_triggers,
        background_music_track=None,
    )
    logger.info(
        "build_edit_plan: %s (%s, %.1fs, %d palavras, %d sfx)",
        plan.candidate_id, template, plan.output_duration_seconds, len(words_in), len(sfx_triggers),
    )
    return plan


# ---------------------------------------------------------------------------
# 7. Persistência
# ---------------------------------------------------------------------------
def save_candidates(candidates: Sequence[ViralCandidate], path: str) -> str:
    """Serializa candidatos em JSON (lista)."""
    out = [c.model_dump(mode="json") for c in candidates]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("save_candidates: %d gravados em %s", len(out), p)
    return str(p)


def save_edit_plan(plan: EditPlan, path: str) -> str:
    """Serializa um EditPlan em JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("save_edit_plan: %s gravado", p)
    return str(p)
