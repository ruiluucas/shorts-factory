"""Fixtures compartilhadas para os testes do GTA Shorts Factory."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    """Gera vídeos de teste reais com ffmpeg (uma vez por sessão)."""
    d = tmp_path_factory.mktemp("media")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg não disponível")

    # vídeo com movimento + tom constante
    motion = d / "motion.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(motion),
        ],
        check=True,
    )

    # vídeo quase estático, sem áudio
    static = d / "static.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:size=1080x1920:rate=30:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(static),
        ],
        check=True,
    )

    # wav puro com silêncio e depois tone alto (para clipping/peaks)
    audio = d / "audio_mix.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
            "-t", "2", str(d / "silence.wav"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
            "-af", "volume=8dB", "-ar", "16000", "-ac", "1",
            str(audio),
        ],
        check=True,
    )
    return d


@pytest.fixture
def transcript_fixture(tmp_path) -> Path:
    """Transcript sintético PT-BR com reações fortes e timestamps coerentes."""
    words = []
    t = 0.0
    script = [
        ("mano", 0.0), ("olha", 0.3), ("isso", 0.55), ("caralho", 0.8),
        ("que", 1.2), ("queda", 1.45), ("foi", 1.7), ("essa", 1.95),
        ("meu", 2.3), ("deus", 2.55), ("não", 3.0), ("acredito", 3.25),
        ("ele", 3.7), ("voou", 3.95), ("longe", 4.2),
    ]
    for w, start in script:
        words.append(
            {"word": w, "start": start, "end": start + 0.25, "score": 0.95, "speaker": "SPEAKER_00"}
        )

    data = {
        "video_path": str(tmp_path / "motion.mp4"),
        "duration": 30.0,
        "device_used": "cpu",
        "model_used": "small",
        "words": words,
        "audio_events": {
            "rms_series": [0.02] * 20 + [0.9] * 5 + [0.02] * 20,
            "peaks": [{"start": 0.8, "end": 1.1, "energy": 0.92}],
            "clipping_ratio": 0.0,
            "mean_rms": 0.18,
            "is_quiet": False,
        },
    }
    p = tmp_path / "transcript.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.fixture
def vision_fixture(tmp_path) -> Path:
    """Visão com gameplay ativo e facecam presente."""
    samples = [
        {"t": i * 0.5, "faces": 1, "face_box": {"x": 700, "y": 1200, "w": 300, "h": 300},
         "motion": 0.6, "scene": "facecam"}
        for i in range(20)
    ]
    data = {
        "video_path": str(tmp_path / "motion.mp4"),
        "fps": 30.0,
        "frame_count": 300,
        "width": 1080,
        "height": 1920,
        "duration": 10.0,
        "samples": samples,
        "facecam_ratio": 1.0,
        "mean_motion": 0.6,
        "passive_ratio": 0.0,
        "recommended_crop_mode": "split_screen",
    }
    p = tmp_path / "vision.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.fixture
def rights_approved(tmp_path) -> Path:
    data = {
        "source_id": "src_001",
        "creator_handle": "@canal_original",
        "status": "APPROVED",
        "commercial_permission": True,
        "evidence_url_or_path": "/tmp/permissao.png",
        "expiration_date": None,
    }
    p = tmp_path / "rights.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.fixture
def rights_pending(tmp_path) -> Path:
    data = {
        "source_id": "src_001",
        "creator_handle": "@canal_original",
        "status": "PENDING",
        "commercial_permission": False,
        "evidence_url_or_path": "",
        "expiration_date": None,
    }
    p = tmp_path / "rights.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p
