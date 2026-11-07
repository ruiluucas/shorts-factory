"""Testes da TRAVA JURÍDICA — o gatekeeper de direitos.

Estes testes são a garantia mais importante do projeto: um vídeo sem
permissão explícita JAMAIS pode chegar ao render.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.gatekeeper


def run_main(project_root: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(project_root / ".venv" / "bin" / "python"), str(project_root / "main.py"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(project_root),
    )


@pytest.fixture
def job_dir(tmp_path, monkeypatch) -> Path:
    """Workspace isolado para não sujar o workspace real."""
    ws = tmp_path / "workspace" / "jobs" / "job_test"
    ws.mkdir(parents=True)
    monkeypatch.setenv("GSF_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    return ws


def test_pending_rights_blocks_pipeline(project_root, job_dir, media_dir):
    """Status PENDING deve abortar com exit 3 e NÃO gerar transcript."""
    rights = {
        "source_id": "src_001",
        "creator_handle": "@canal",
        "status": "PENDING",
        "commercial_permission": False,
        "evidence_url_or_path": "",
    }
    (job_dir / "rights.json").write_text(json.dumps(rights), encoding="utf-8")

    proc = run_main(
        project_root,
        "run",
        "--video",
        str(media_dir / "motion.mp4"),
        "--job-id",
        "job_test",
        "--no-render",
    )

    assert proc.returncode == 3, (
        f"esperado exit 3 (REJECTED_RIGHTS), veio {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    )
    assert not (job_dir / "transcript.json").exists(), "transcript NÃO pode ser gerado antes do gate"


def test_approved_without_commercial_permission_blocks(project_root, job_dir, media_dir):
    """APPROVED mas commercial_permission=false ainda deve bloquear."""
    rights = {
        "source_id": "src_001",
        "creator_handle": "@canal",
        "status": "APPROVED",
        "commercial_permission": False,
        "evidence_url_or_path": "/tmp/x.png",
    }
    (job_dir / "rights.json").write_text(json.dumps(rights), encoding="utf-8")

    proc = run_main(
        project_root,
        "run",
        "--video",
        str(media_dir / "motion.mp4"),
        "--job-id",
        "job_test",
        "--no-render",
    )

    assert proc.returncode == 3, f"sem permissão comercial deve bloquear\n{proc.stdout}\n{proc.stderr}"


def test_rejected_rights_blocks(project_root, job_dir, media_dir):
    """Status REJECTED bloqueia."""
    rights = {
        "source_id": "src_001",
        "creator_handle": "@canal",
        "status": "REJECTED",
        "commercial_permission": True,
        "evidence_url_or_path": "/tmp/x.png",
    }
    (job_dir / "rights.json").write_text(json.dumps(rights), encoding="utf-8")

    proc = run_main(
        project_root,
        "run",
        "--video",
        str(media_dir / "motion.mp4"),
        "--job-id",
        "job_test",
        "--no-render",
    )
    assert proc.returncode == 3


def test_init_job_creates_pending_by_default(project_root, job_dir, media_dir):
    """init-job sem --approved deve criar rights.json PENDING."""
    proc = run_main(
        project_root,
        "init-job",
        "--job-id",
        "job_test",
        "--video",
        str(media_dir / "motion.mp4"),
        "--creator",
        "@canal",
        "--evidence",
        "/tmp/permissao.png",
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    rights = json.loads((job_dir / "rights.json").read_text(encoding="utf-8"))
    assert rights["status"] == "PENDING", "init-job sem --approved deve ser PENDING"
    assert rights["commercial_permission"] is False


def test_init_job_with_approved_flag(project_root, job_dir, media_dir):
    """init-job --approved cria APPROVED + commercial_permission=true."""
    proc = run_main(
        project_root,
        "init-job",
        "--job-id",
        "job_test",
        "--video",
        str(media_dir / "motion.mp4"),
        "--creator",
        "@canal",
        "--evidence",
        "/tmp/permissao.png",
        "--approved",
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    rights = json.loads((job_dir / "rights.json").read_text(encoding="utf-8"))
    assert rights["status"] == "APPROVED"
    assert rights["commercial_permission"] is True
