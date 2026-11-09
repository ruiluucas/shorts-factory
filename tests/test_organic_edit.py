"""Testes da edição orgânica: corte de silêncio, ritmo e B-roll.

Nenhum teste toca a rede: o B-roll é exercitado com pasta local e com um
duplo de teste que simula o Openverse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.broll import (
    BrollLibrary,
    extract_topics,
    normalize,
    plan_placements,
)
from core.organic_edit import (
    KeepRange,
    build_keep_ranges,
    detect_emphasis,
    find_silences,
    plan_silence_cuts,
    rhythm_variation,
)

# --------------------------------------------------------------- silêncio


def _words(*triples: tuple[str, float, float]) -> list[dict]:
    return [{"word": w, "start": s, "end": e} for w, s, e in triples]


@pytest.mark.scoring
def test_find_silences_detects_long_pause():
    words = _words(("oi", 0.0, 0.4), ("tudo", 3.0, 3.4))
    silences = find_silences(words, 0.0, 4.0, min_pause_ms=700, keep_pause_ms=150)
    assert len(silences) == 1
    start, end = silences[0]
    # corta de 0.4+0.15 até 3.0-0.15
    assert start == pytest.approx(0.55)
    assert end == pytest.approx(2.85)


@pytest.mark.scoring
def test_short_pause_is_not_cut():
    """Respiração natural não é silêncio."""
    words = _words(("oi", 0.0, 0.4), ("tudo", 0.8, 1.2))
    assert find_silences(words, 0.0, 2.0, min_pause_ms=700) == []


@pytest.mark.scoring
def test_silence_preserves_breath():
    """O respiro de 150ms sobrevive nas duas bordas."""
    words = _words(("a", 0.0, 0.4), ("b", 3.0, 3.4))
    start, end = find_silences(words, 0.0, 4.0, keep_pause_ms=150)[0]
    assert start - 0.4 == pytest.approx(0.15, abs=0.01)
    assert 3.0 - end == pytest.approx(0.15, abs=0.01)


@pytest.mark.scoring
def test_never_cuts_inside_a_word():
    """O corte só acontece entre o fim de uma palavra e o início da próxima."""
    words = _words(("palavra", 1.0, 2.5), ("outra", 5.0, 6.0))
    for start, end in find_silences(words, 0.0, 7.0):
        # nenhum corte invade [1.0, 2.5] nem [5.0, 6.0]
        assert end <= 5.0 or start >= 2.5
        assert not (start < 2.5 and end > 1.0)


@pytest.mark.scoring
def test_build_keep_ranges_inverts_cuts():
    ranges = build_keep_ranges(0.0, 10.0, [(2.0, 4.0), (6.0, 7.0)])
    spans = [(r.start, r.end) for r in ranges]
    assert spans == [(0.0, 2.0), (4.0, 6.0), (7.0, 10.0)]


@pytest.mark.scoring
def test_removed_ratio_is_capped():
    """Config agressiva não pode descaracterizar o trecho."""
    words = _words(
        ("a", 0.0, 0.3),
        ("b", 5.0, 5.3),
        ("c", 10.0, 10.3),
        ("d", 15.0, 15.3),
    )
    edit = plan_silence_cuts(words, 0.0, 16.0, max_removed_ratio=0.20)
    assert edit.removed_total / edit.original_duration <= 0.21


@pytest.mark.scoring
def test_disabled_silence_keeps_everything():
    edit = plan_silence_cuts([], 0.0, 10.0, enabled=False)
    assert len(edit.keep_ranges) == 1
    assert edit.keep_ranges[0].start == 0.0
    assert edit.keep_ranges[0].end == 10.0
    assert edit.removed == []


@pytest.mark.scoring
def test_kept_duration_matches_ranges():
    edit = plan_silence_cuts(_words(("a", 0.0, 1.0), ("b", 5.0, 6.0)), 0.0, 6.0)
    assert edit.kept_duration == pytest.approx(sum(r.duration for r in edit.keep_ranges))


# ---------------------------------------------------------------- ritmo


@pytest.mark.scoring
def test_emphasis_detects_strong_words():
    words = _words(
        ("ele", 0.0, 0.2),
        ("Caralho", 1.0, 1.4),
        ("que", 2.0, 2.2),
        ("queda", 3.0, 3.4),
        ("meu", 4.5, 4.7),
        ("deus", 4.7, 5.0),
    )
    hits = detect_emphasis(words, 0.0, 6.0)
    assert len(hits) >= 2
    assert all("t" in h and "scale" in h for h in hits)


@pytest.mark.scoring
def test_emphasis_respects_min_gap():
    """Nunca dois zooms a menos de 2s — senão o vídeo treme."""
    words = _words(
        ("caralho", 0.0, 0.3),
        ("porra", 0.5, 0.8),
        ("merda", 1.0, 1.3),
    )
    hits = detect_emphasis(words, 0.0, 2.0, min_gap_s=2.0)
    assert len(hits) == 1


@pytest.mark.scoring
def test_emphasis_is_relative_to_cut():
    words = _words(("caralho", 10.0, 10.4))
    hits = detect_emphasis(words, 10.0, 12.0)
    assert hits[0]["t"] < 1.0


@pytest.mark.scoring
def test_rhythm_variation_is_deterministic_by_seed():
    ranges = [KeepRange(0.0, 20.0)]
    a = rhythm_variation(ranges, 0.0, interval_s=4.5, seed=42)
    b = rhythm_variation(ranges, 0.0, interval_s=4.5, seed=42)
    c = rhythm_variation(ranges, 0.0, interval_s=4.5, seed=7)
    assert a == b
    assert a != c


# ---------------------------------------------------------------- b-roll


@pytest.mark.scoring
def test_normalize_strips_accents():
    assert normalize("Carro") == "carro"
    assert normalize("VEÍCULO") == "veiculo"
    assert normalize("Avião!") == "aviao!"


@pytest.mark.scoring
def test_extract_topics_finds_mentions():
    labels = {"carro": ["carro", "car"], "pessoa": ["cara", "pessoa"]}
    words = _words(("olha", 0.0, 0.3), ("o", 0.3, 0.4), ("carro", 0.4, 0.9))
    topics = extract_topics(words, labels)
    assert len(topics) == 1
    assert topics[0]["label"] == "carro"


@pytest.mark.scoring
def test_extract_topics_collapses_repetition():
    """'olha o carro, olha o carro' não pode gerar duas imagens iguais."""
    labels = {"carro": ["carro"]}
    words = _words(
        ("carro", 0.0, 0.4),
        ("carro", 1.0, 1.4),
        ("carro", 2.0, 2.4),
    )
    topics = extract_topics(words, labels, min_gap=6.0)
    assert len(topics) == 1


@pytest.mark.scoring
def test_long_synonym_wins_over_short():
    """'helicoptero' não deve casar como 'ele'."""
    labels = {"pessoa": ["ele"], "aviao": ["helicoptero"]}
    words = _words(("helicoptero", 0.0, 0.9))
    topics = extract_topics(words, labels)
    assert topics[0]["label"] == "aviao"


@pytest.mark.scoring
def test_local_folder_wins_over_network(tmp_path: Path):
    """Acervo do operador tem prioridade sobre busca automática."""
    folder = tmp_path / "carro"
    folder.mkdir(parents=True)
    img = folder / "meu_carro.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"0" * 2048)

    lib = BrollLibrary(local_dir=tmp_path, cache_dir=tmp_path / "cache", allow_network=True)
    asset = lib.get("carro")
    assert asset is not None
    assert asset.source == "local"
    assert asset.license == "user-supplied"


@pytest.mark.scoring
def test_no_network_means_no_fetch(tmp_path: Path):
    lib = BrollLibrary(local_dir=tmp_path, cache_dir=tmp_path / "c", allow_network=False)
    assert lib.get("inexistente") is None


@pytest.mark.scoring
def test_plan_placements_respects_interval(tmp_path: Path):
    labels = {"carro": ["carro"], "pessoa": ["cara"]}
    for name in ("carro", "pessoa"):
        d = tmp_path / name
        d.mkdir()
        (d / "x.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 2048)

    lib = BrollLibrary(local_dir=tmp_path, cache_dir=tmp_path / "c", allow_network=False)
    words = _words(
        ("carro", 1.0, 1.4),
        ("cara", 2.0, 2.4),
        ("carro", 12.0, 12.4),
    )
    placements = plan_placements(words, 0.0, 20.0, labels, lib, min_interval=5.0)
    # a de 2.0s é próxima demais da de 1.0s
    assert len(placements) == 2
    assert placements[0].asset.label == "carro"
    assert placements[1].asset.label == "carro"
    assert placements[1].t_start >= 10.0


@pytest.mark.scoring
def test_placements_stay_inside_cut(tmp_path: Path):
    folder = tmp_path / "carro"
    folder.mkdir()
    (folder / "x.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 2048)
    lib = BrollLibrary(local_dir=tmp_path, cache_dir=tmp_path / "c")

    words = _words(("carro", 19.0, 19.4))
    placements = plan_placements(words, 0.0, 20.0, {"carro": ["carro"]}, lib, duration=2.5)
    assert placements
    assert placements[0].t_end <= 20.0


@pytest.mark.scoring
def test_max_placements_cap(tmp_path: Path):
    folder = tmp_path / "carro"
    folder.mkdir()
    (folder / "x.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 2048)
    lib = BrollLibrary(local_dir=tmp_path, cache_dir=tmp_path / "c")

    words = [{"word": "carro", "start": i * 10.0, "end": i * 10.0 + 0.3} for i in range(10)]
    placements = plan_placements(
        words,
        0.0,
        100.0,
        {"carro": ["carro"]},
        lib,
        max_placements=3,
        min_interval=5.0,
    )
    assert len(placements) <= 3


@pytest.mark.scoring
def test_broll_never_breaks_plan_without_labels(tmp_path: Path):
    lib = BrollLibrary(local_dir=tmp_path, cache_dir=tmp_path / "c", allow_network=False)
    assert plan_placements(_words(("carro", 1.0, 1.4)), 0.0, 10.0, {}, lib) == []
