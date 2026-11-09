"""Edição orgânica: corte de silêncio e ritmo variável.

O que separa "corte mecânico" de "editor humano" é sobretudo o que ele **remove**.
Duas operações, ambas determinísticas e configuráveis:

  1. **Corte de silêncio** — remove pausas longas usando os timestamps
     word-level. Nunca corta no meio de uma palavra: só age no espaço entre
     o fim de uma palavra e o início da próxima.

  2. **Ritmo** — marca punch-ins (zoom suave) nas palavras de ênfase, limitado
     a um por janela de tempo, para o vídeo não tremer.

Tudo aqui é puro: recebe listas, devolve listas. Sem I/O, sem ffmpeg, sem rede.
Isso torna teste fácil e o resultado reproduzível.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

# Palavras que pedem zoom: reação forte, interjeição, intensificador.
EMPHASIS_WORDS = (
    "caralho",
    "porra",
    "merda",
    "meu deus",
    "nao acredito",
    "nao creio",
    "olha",
    "olha isso",
    "que isso",
    "que absurdo",
    "surreal",
    "insano",
    "oloco",
    "eita",
    "mano",
    "serio",
    "cacete",
    "nossa",
    "incrivel",
    "absurdo",
)


@dataclass
class KeepRange:
    """Faixa do vídeo fonte que sobrevive ao corte (tempo absoluto)."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class SilenceEdit:
    """Resultado da análise de silêncio."""

    keep_ranges: list[KeepRange]
    removed: list[tuple[float, float]]  # silêncios removidos (abs)
    removed_total: float
    original_duration: float

    @property
    def kept_duration(self) -> float:
        return sum(r.duration for r in self.keep_ranges)


def _normalize(word: str) -> str:
    import unicodedata

    w = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in w if not unicodedata.combining(c)).strip(".,!?;:…\"'")


def find_silences(
    words: list[dict[str, Any]],
    cut_start: float,
    cut_end: float,
    *,
    min_pause_ms: int = 700,
    keep_pause_ms: int = 150,
) -> list[tuple[float, float]]:
    """Pausas longas demais dentro do corte.

    `min_pause_ms` é o gatilho (abaixo disso, é respiração natural).
    `keep_pause_ms` é o respiro que sobra — cortar até zero soa artificial.
    """
    inside = sorted(
        (float(w["start"]), float(w["end"]))
        for w in words
        if float(w.get("end", 0)) > cut_start and float(w.get("start", 0)) < cut_end
    )
    if len(inside) < 2:
        return []

    min_pause = min_pause_ms / 1000.0
    keep = keep_pause_ms / 1000.0
    silences: list[tuple[float, float]] = []

    for (_, prev_end), (next_start, _) in pairwise(inside):
        gap = next_start - prev_end
        if gap <= min_pause:
            continue
        # Corta o miolo, preserva `keep` de respiro em cada borda.
        cut_from = prev_end + keep
        cut_to = next_start - keep
        if cut_to - cut_from < 0.15:  # sobra pequena demais, não vale cortar
            continue
        silences.append((max(cut_from, cut_start), min(cut_to, cut_end)))

    return silences


def build_keep_ranges(
    cut_start: float,
    cut_end: float,
    silences: list[tuple[float, float]],
) -> list[KeepRange]:
    """Converte silêncios removidos nas faixas que sobram."""
    ranges: list[KeepRange] = []
    cursor = cut_start

    for start, end in sorted(silences):
        if start > cursor:
            ranges.append(KeepRange(cursor, min(start, cut_end)))
        cursor = max(cursor, end)

    if cursor < cut_end:
        ranges.append(KeepRange(cursor, cut_end))

    return [r for r in ranges if r.duration > 0.05]


def plan_silence_cuts(
    words: list[dict[str, Any]],
    cut_start: float,
    cut_end: float,
    *,
    enabled: bool = True,
    min_pause_ms: int = 700,
    keep_pause_ms: int = 150,
    max_removed_ratio: float = 0.35,
) -> SilenceEdit:
    """Pipeline completo: encontra silêncios e devolve faixas a manter.

    `max_removed_ratio` protege contra config agressiva: remover mais de 35%
    do trecho descaracteriza o conteúdo (e o vídeo vira outra coisa).
    """
    original = cut_end - cut_start

    if not enabled:
        return SilenceEdit(
            keep_ranges=[KeepRange(cut_start, cut_end)],
            removed=[],
            removed_total=0.0,
            original_duration=original,
        )

    silences = find_silences(
        words,
        cut_start,
        cut_end,
        min_pause_ms=min_pause_ms,
        keep_pause_ms=keep_pause_ms,
    )

    removed_total = sum(e - s for s, e in silences)
    if original > 0 and removed_total / original > max_removed_ratio:
        # Config agressiva demais: mantém só os maiores, até o limite.
        budget = original * max_removed_ratio
        chosen: list[tuple[float, float]] = []
        spent = 0.0
        for s, e in sorted(silences, key=lambda p: p[1] - p[0], reverse=True):
            size = e - s
            if spent + size > budget:
                continue
            chosen.append((s, e))
            spent += size
        silences = sorted(chosen)
        removed_total = spent

    return SilenceEdit(
        keep_ranges=build_keep_ranges(cut_start, cut_end, silences),
        removed=silences,
        removed_total=removed_total,
        original_duration=original,
    )


def detect_emphasis(
    words: list[dict[str, Any]],
    cut_start: float,
    cut_end: float,
    *,
    min_gap_s: float = 2.0,
    lead_s: float = 0.12,
    hold_s: float = 0.55,
) -> list[dict[str, Any]]:
    """Palavras que merecem punch-in, já em tempo relativo ao corte.

    `min_gap_s` garante no máximo um zoom por 2s — mais que isso o vídeo
    treme e parece erro de render, não ritmo.
    """
    hits: list[dict[str, Any]] = []
    last_t = -999.0

    ordered = sorted(words, key=lambda x: float(x.get("start", 0)))
    tokens = [_normalize(str(w.get("word", ""))) for w in ordered]

    # Expressões de duas palavras ("meu deus", "nao acredito") contam como
    # ênfase única: palavra a palavra, nenhuma das duas dispara sozinha.
    bigram_phrases = ("meu deus", "nao acredito", "nao creio", "olha isso", "que isso", "que absurdo")
    bigram_starts: set[int] = set()
    for i in range(len(tokens) - 1):
        pair = f"{tokens[i]} {tokens[i + 1]}"
        if pair in bigram_phrases:
            bigram_starts.add(i)
    covered: set[int] = {i + 1 for i in bigram_starts}

    for idx, w in enumerate(ordered):
        start = float(w.get("start", 0))
        if start < cut_start or start >= cut_end:
            continue

        token = tokens[idx]
        if not token:
            continue

        strong = idx in bigram_starts or (
            idx not in covered and any(token == e or (len(e) >= 5 and e in token) for e in EMPHASIS_WORDS)
        )
        if not strong:
            continue

        rel = start - cut_start
        if rel - last_t < min_gap_s:
            continue

        hits.append(
            {
                "t": round(max(0.0, rel - lead_s), 3),
                "duration": hold_s,
                "scale": 1.08,
                "word": str(w.get("word", "")),
            }
        )
        last_t = rel

    return hits


def rhythm_variation(
    keep_ranges: list[KeepRange],
    cut_start: float,
    *,
    interval_s: float = 4.5,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Alterna o enquadramento a cada N segundos para segurar a atenção.

    Determinístico por seed: dois shorts do mesmo job não ficam idênticos,
    mas rodar duas vezes com a mesma seed dá o mesmo resultado.
    """
    import random

    modes = ("fill", "fit", "fill")
    rng = random.Random(seed)
    shifts: list[dict[str, Any]] = []

    for r in keep_ranges:
        t = r.start
        while t < r.end:
            rel = t - cut_start
            shifts.append(
                {
                    "t": round(rel, 3),
                    "mode": rng.choice(modes),
                    "pan": round(rng.uniform(-0.04, 0.04), 3),
                }
            )
            t += interval_s

    return shifts
