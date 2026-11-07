"""Testes dos módulos de análise: áudio/ASR, visão e scoring viral."""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------- áudio


@pytest.mark.audio
def test_whisper_module_importable(project_root):
    """O módulo deve importar sem carregar o modelo (lazy loading)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("whisper_analyzer", project_root / "core" / "whisper_analyzer.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, "analyze")
    assert hasattr(mod, "extract_audio")
    assert hasattr(mod, "detect_audio_events")


@pytest.mark.audio
def test_extract_audio_produces_wav(project_root, media_dir, tmp_path):
    from core.whisper_analyzer import extract_audio

    out = tmp_path / "out.wav"
    result = extract_audio(media_dir / "motion.mp4", out)

    assert Path(result).exists(), "wav não foi criado"
    assert Path(result).stat().st_size > 1000, "wav vazio"


@pytest.mark.audio
def test_detect_audio_events_on_silence(project_root, media_dir):
    from core.whisper_analyzer import detect_audio_events

    silence = media_dir / "silence.wav"
    if not silence.exists():
        pytest.skip("fixture silence.wav ausente")

    events = detect_audio_events(silence)
    assert isinstance(events, dict)
    assert events["mean_rms"] < 0.05, f"silêncio deveria ter rms baixo, veio {events['mean_rms']}"
    assert events["is_quiet"] is True


@pytest.mark.audio
def test_detect_audio_events_detects_peak(project_root, media_dir):
    from core.whisper_analyzer import detect_audio_events

    loud = media_dir / "audio_mix.wav"
    if not loud.exists():
        pytest.skip("fixture audio_mix.wav ausente")

    events = detect_audio_events(loud)
    assert len(events["peaks"]) >= 1, "tone alto deveria gerar ao menos 1 pico"
    assert events["mean_rms"] > 0.05


# ---------------------------------------------------------------- visão


@pytest.mark.vision
def test_vision_module_importable(project_root):
    from core import vision_tracker

    for fn in ("analyze", "sample_frames", "detect_faces", "motion_intensity"):
        assert hasattr(vision_tracker, fn), f"falta {fn}"


@pytest.mark.vision
def test_motion_differs_between_active_and_static(project_root, media_dir):
    """O teste que prova que o motion não é decorativo."""
    from core.vision_tracker import analyze

    active = analyze(media_dir / "motion.mp4")
    static = analyze(media_dir / "static.mp4")

    assert active["mean_motion"] > static["mean_motion"] * 2, (
        f"motion do vídeo ativo ({active['mean_motion']:.4f}) deveria ser muito maior "
        f"que o estático ({static['mean_motion']:.4f})"
    )


@pytest.mark.vision
def test_vision_analyze_returns_expected_keys(project_root, media_dir):
    from core.vision_tracker import analyze

    result = analyze(media_dir / "motion.mp4")
    for key in (
        "width",
        "height",
        "duration",
        "samples",
        "facecam_ratio",
        "mean_motion",
        "passive_ratio",
        "recommended_crop_mode",
    ):
        assert key in result, f"falta chave {key} no retorno de analyze()"

    assert result["width"] == 1080
    assert result["height"] == 1920
    assert result["recommended_crop_mode"] in ("split_screen", "gameplay_centered")


# ---------------------------------------------------------------- scoring


@pytest.mark.scoring
def test_viral_detector_importable(project_root):
    from core import viral_detector

    for fn in ("detect_candidates", "build_edit_plan", "score_window", "build_windows"):
        assert hasattr(viral_detector, fn), f"falta {fn}"


@pytest.mark.scoring
def test_candidates_score_range(project_root):
    """Scores devem estar sempre em 0-100."""
    from core.viral_detector import detect_candidates

    transcript = {
        "duration": 20.0,
        "words": [
            {"word": w, "start": i * 0.35, "end": i * 0.35 + 0.3, "score": 0.9, "speaker": None}
            for i, w in enumerate(
                [
                    "mano",
                    "olha",
                    "isso",
                    "caralho",
                    "que",
                    "queda",
                    "foi",
                    "essa",
                    "meu",
                    "deus",
                    "não",
                    "acredito",
                    "ele",
                    "voou",
                    "longe",
                ]
            )
        ],
        "audio_events": {
            "peaks": [{"start": 0.7, "end": 1.0, "energy": 0.9}],
            "clipping_ratio": 0.0,
            "mean_rms": 0.2,
            "is_quiet": False,
        },
    }
    candidates = detect_candidates(transcript, vision=None)

    assert isinstance(candidates, list)
    for c in candidates:
        assert 0 <= c.score <= 100, f"score fora do range: {c.score}"


@pytest.mark.scoring
def test_passive_penalty_reduces_score(project_root):
    """A penalidade de cutscene passiva (-40) precisa ter efeito mensurável."""
    from core.viral_detector import detect_candidates

    transcript = {
        "duration": 30.0,
        "words": [
            {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3, "score": 0.9, "speaker": None}
            for i, w in enumerate(("olha isso caralho " * 8).split())
        ],
        "audio_events": {
            "peaks": [{"start": 1.0, "end": 1.3, "energy": 0.9}],
            "clipping_ratio": 0.0,
            "mean_rms": 0.2,
            "is_quiet": False,
        },
    }

    active_vision = {
        "duration": 30.0,
        "samples": [
            {
                "t": i,
                "faces": 1,
                "face_box": {"x": 0, "y": 0, "w": 300, "h": 300},
                "motion": 0.6,
                "scene": "gameplay_active",
            }
            for i in range(30)
        ],
        "facecam_ratio": 0.5,
        "mean_motion": 0.6,
        "passive_ratio": 0.0,
        "recommended_crop_mode": "gameplay_centered",
    }
    passive_vision = {
        **active_vision,
        "samples": [
            {"t": i, "faces": 0, "face_box": None, "motion": 0.02, "scene": "cutscene_passive"} for i in range(30)
        ],
        "passive_ratio": 1.0,
        "mean_motion": 0.02,
    }

    active = detect_candidates(transcript, vision=active_vision)
    passive = detect_candidates(transcript, vision=passive_vision)

    assert active and passive, "ambos os cenários deveriam gerar candidatos"
    top_active = max(c.score for c in active)
    top_passive = max(c.score for c in passive)

    assert top_passive < top_active, (
        f"cutscene passiva ({top_passive:.1f}) deveria pontuar menos que gameplay ativo ({top_active:.1f})"
    )
    # a penalidade é -40; diferença deve ser substancial, não cosmética
    assert (top_active - top_passive) >= 20, (
        f"diferença pequena demais: {top_active - top_passive:.1f} (penalidade deveria ser ~40)"
    )


@pytest.mark.scoring
def test_edit_plan_validates_against_schema(project_root, tmp_path):
    """EditPlan gerado deve validar no contrato Pydantic."""
    from core.schemas import EditPlan
    from core.viral_detector import build_edit_plan, detect_candidates

    transcript = {
        "duration": 30.0,
        "words": [
            {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3, "score": 0.9, "speaker": "S0"}
            for i, w in enumerate(
                [
                    "olha",
                    "isso",
                    "mano",
                    "caralho",
                    "que",
                    "queda",
                    "foi",
                    "essa",
                    "meu",
                    "deus",
                    "não",
                    "acredito",
                    "nele",
                ]
            )
        ],
        "audio_events": {
            "peaks": [{"start": 1.2, "end": 1.5, "energy": 0.9}],
            "clipping_ratio": 0.0,
            "mean_rms": 0.2,
            "is_quiet": False,
        },
    }
    vision = {
        "duration": 30.0,
        "samples": [
            {"t": i, "faces": 1, "face_box": {"x": 0, "y": 0, "w": 300, "h": 300}, "motion": 0.5, "scene": "facecam"}
            for i in range(30)
        ],
        "facecam_ratio": 1.0,
        "mean_motion": 0.5,
        "passive_ratio": 0.0,
        "recommended_crop_mode": "split_screen",
    }

    candidates = detect_candidates(transcript, vision=vision)
    assert candidates, "deveria gerar ao menos 1 candidato"

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")

    plan = build_edit_plan(
        job_id="job_test",
        candidate=candidates[0],
        transcript=transcript,
        vision=vision,
        config=None,
        source_video_path=str(video),
    )

    # validação estrita: se o contrato mudar, o teste quebra
    EditPlan.model_validate(plan.model_dump(mode="json"))
    assert plan.job_id == "job_test"
    assert plan.crop_mode == "split_screen"
    assert plan.composition_template == "ShortSplitScreen"
    assert plan.words, "edit plan precisa carregar as palavras do corte"


@pytest.mark.scoring
def test_detector_survives_empty_input(project_root):
    """Robustez: entradas vazias não devem explodir."""
    from core.viral_detector import detect_candidates

    empty = {"duration": 0.0, "words": [], "audio_events": {}}
    result = detect_candidates(empty, vision=None)
    assert isinstance(result, list)
    assert result == []
