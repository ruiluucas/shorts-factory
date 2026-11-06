#!/usr/bin/env python3
"""
GTA Shorts Factory — orquestrador CLI do pipeline.

Uso:
    python main.py init-job --job-id JOB --video PATH --creator @handle --evidence PATH [--approved]

    python main.py run --video PATH --job-id JOB [--creator @handle] [--top-k N]
                       [--render/--no-render] [--dry-run]

Saídas (JSON estruturado) em ``workspace/jobs/{job_id}/``:
    metadata.json          etapa 0
    rights.json            gerenciado pelo gatekeeper (etapa 1)
    transcript.json        etapa 2
    vision.json            etapa 3
    audio_events.json      etapa 3
    candidates.json        etapa 4
    edit_plan_{cid}.json   etapa 5
    edit_plan.json         etapa 5 (plano principal)
    render_report.json     etapa 6
    qc.json                etapa 7
    job_status.json        status final + trace de etapas
    pipeline.log           log humano (o log estruturado vai no job_status.json)

TRAVA INVIOLÁVEL (etapa 1): o pipeline ABORTA com status REJECTED_RIGHTS
(exit code 3) se ``rights.json`` não tiver ``status == "APPROVED"`` E
``commercial_permission == true``. Nenhuma flag de CLI contorna isso.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# caminhos e constantes
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
# GSF_WORKSPACE_ROOT permite isolar o workspace (usado pelos testes, para não
# sujar jobs reais). Sem a variável, o padrão continua sendo <repo>/workspace.
WORKSPACE = Path(os.environ.get("GSF_WORKSPACE_ROOT") or (ROOT / "workspace")).resolve()
JOBS_DIR = WORKSPACE / "jobs"
CONFIG_PATH = ROOT / "config" / "pipeline_config.yaml"
REMOTION_DIR = ROOT / "remotion"

sys.path.insert(0, str(ROOT))

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REJECTED_RIGHTS = 3
EXIT_CONFIG = 4

STATUS_COMPLETED = "COMPLETED"
STATUS_REJECTED_RIGHTS = "REJECTED_RIGHTS"
STATUS_SKIPPED_RENDER = "SKIPPED_RENDER"
STATUS_FAILED = "FAILED"

FALLBACK_CONFIG: Dict[str, Any] = {
    "project": {
        "resolution": {"width": 1080, "height": 1920},
        "framerate": 30,
        "aspect_ratio": "9:16",
    },
    "legal_gatekeeper": {
        "default_status": "PENDING",
        "require_explicit_monetization_rights": True,
        "allowed_statuses": ["APPROVED"],
    },
    "audio_policy": {
        "allow_commercial_music": False,
        "local_sfx_library": "assets/sound_effects/",
        "local_music_library": "assets/nocopyright_music/",
        "ducking_db": -12.0,
    },
    "crop_modes": {
        "default": "split_screen",
        "fallback": "gameplay_centered",
        "facecam_threshold": 0.70,
    },
}


# --------------------------------------------------------------------------
# utilitários
# --------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """Carrega o YAML do projeto; cai no fallback se PyYAML não estiver instalado."""
    cfg: Dict[str, Any] = json.loads(json.dumps(FALLBACK_CONFIG))
    if not path.exists():
        return cfg
    try:
        import yaml  # type: ignore

        with path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(value)
                else:
                    cfg[key] = value
    except Exception:  # pragma: no cover - config degradado é aceitável
        pass
    return cfg


def cfg_get(cfg: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, payload: Any) -> Path:
    """Escrita atômica: nunca deixa um JSON truncado no diretório do job."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("_")
    return slug or "job"


class JobLogger:
    """Log humano em pipeline.log + log estruturado acumulado em memória."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: List[Dict[str, Any]] = []

    def _emit(self, level: str, stage: Optional[str], message: str, **extra: Any) -> None:
        entry = {"ts": now_iso(), "level": level, "stage": stage, "message": message}
        if extra:
            entry.update(extra)
        self.entries.append(entry)
        line = f"[{entry['ts']}] {level:<7} {f'({stage})' if stage else '':<16} {message}"
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def info(self, message: str, stage: Optional[str] = None, **extra: Any) -> None:
        self._emit("INFO", stage, message, **extra)

    def warn(self, message: str, stage: Optional[str] = None, **extra: Any) -> None:
        self._emit("WARN", stage, message, **extra)

    def error(self, message: str, stage: Optional[str] = None, **extra: Any) -> None:
        self._emit("ERROR", stage, message, **extra)


class StageTrace:
    """Registra a duração de cada etapa do pipeline."""

    def __init__(self) -> None:
        self.stages: List[Dict[str, Any]] = []
        self._current: Optional[Dict[str, Any]] = None
        self._t0: float = 0.0

    def start(self, name: str) -> None:
        self._current = {"stage": name, "started_at": now_iso()}
        self._t0 = time.perf_counter()

    def finish(self, status: str = "OK", **extra: Any) -> Dict[str, Any]:
        record = self._current or {"stage": "unknown", "started_at": now_iso()}
        record["duration_seconds"] = round(time.perf_counter() - self._t0, 3)
        record["finished_at"] = now_iso()
        record["status"] = status
        record.update(extra)
        self.stages.append(record)
        self._current = None
        return record


class PipelineError(RuntimeError):
    """Falha de etapa que deve virar status FAILED (exit 1)."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


# --------------------------------------------------------------------------
# ffprobe / ffmpeg
# --------------------------------------------------------------------------


def require_binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise PipelineError("env", f"binário '{name}' não encontrado no PATH")
    return found


def ffprobe_json(path: Path) -> Dict[str, Any]:
    ffprobe = require_binary("ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise PipelineError(
            "ffprobe", f"ffprobe falhou em {path}: {proc.stderr.strip()[:500]}"
        )
    return json.loads(proc.stdout or "{}")


def probe_summary(info: Dict[str, Any]) -> Dict[str, Any]:
    streams = info.get("streams", []) or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = info.get("format", {}) or {}

    def _num(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    fps = None
    if video and video.get("avg_frame_rate") and video["avg_frame_rate"] != "0/0":
        try:
            num, den = video["avg_frame_rate"].split("/")
            fps = round(float(num) / float(den), 3) if float(den) else None
        except (ValueError, ZeroDivisionError):
            fps = None

    return {
        "duration_seconds": _num(fmt.get("duration"))
        or _num((video or {}).get("duration")),
        "width": (video or {}).get("width"),
        "height": (video or {}).get("height"),
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "has_audio": audio is not None,
        "has_video": video is not None,
        "fps": fps,
        "bitrate": _num(fmt.get("bit_rate")),
        "size_bytes": _num(fmt.get("size")),
    }


# --------------------------------------------------------------------------
# import tolerante dos módulos escritos em paralelo
# --------------------------------------------------------------------------


def load_pipeline_modules() -> Dict[str, Any]:
    """
    Importa os módulos de análise de forma tolerante. Ausências são reportadas
    como PipelineError com mensagem clara na etapa correspondente — nunca
    inventamos resultados.
    """
    mods: Dict[str, Any] = {
        "analyze_audio": None,
        "analyze_vision": None,
        "detect_candidates": None,
        "build_edit_plan": None,
        "errors": {},
    }

    try:
        from core.whisper_analyzer import analyze as analyze_audio  # type: ignore

        mods["analyze_audio"] = analyze_audio
    except Exception as exc:  # noqa: BLE001
        mods["errors"]["whisper_analyzer"] = f"{type(exc).__name__}: {exc}"

    try:
        from core.vision_tracker import analyze as analyze_vision  # type: ignore

        mods["analyze_vision"] = analyze_vision
    except Exception as exc:  # noqa: BLE001
        mods["errors"]["vision_tracker"] = f"{type(exc).__name__}: {exc}"

    try:
        from core.viral_detector import (  # type: ignore
            build_edit_plan,
            detect_candidates,
        )

        mods["detect_candidates"] = detect_candidates
        mods["build_edit_plan"] = build_edit_plan
    except Exception as exc:  # noqa: BLE001
        mods["errors"]["viral_detector"] = f"{type(exc).__name__}: {exc}"

    return mods


def require_module(mods: Dict[str, Any], key: str, stage: str, module_name: str):
    fn = mods.get(key)
    if fn is None:
        detail = mods.get("errors", {}).get(module_name, "módulo ausente")
        raise PipelineError(
            stage,
            f"módulo '{module_name}' indisponível ({detail}). "
            f"A etapa '{stage}' não pode rodar; o pipeline foi abortado sem "
            f"produzir resultados fabricados.",
        )
    return fn


# --------------------------------------------------------------------------
# etapa 0 — metadados
# --------------------------------------------------------------------------


def stage_metadata(
    job_dir: Path, job_id: str, video: Path, creator: Optional[str], cfg: Dict[str, Any]
) -> Dict[str, Any]:
    if not video.exists():
        raise PipelineError("metadata", f"vídeo de entrada não existe: {video}")

    info = ffprobe_json(video)
    summary = probe_summary(info)
    if not summary["has_video"]:
        raise PipelineError("metadata", f"o arquivo não tem stream de vídeo: {video}")
    if not summary["duration_seconds"]:
        raise PipelineError("metadata", f"não foi possível ler a duração de {video}")

    metadata = {
        "job_id": job_id,
        "video_path": str(video),
        "video_filename": video.name,
        "sha256": sha256_file(video),
        "creator_handle": creator,
        "duration_seconds": summary["duration_seconds"],
        "width": summary["width"],
        "height": summary["height"],
        "fps": summary["fps"],
        "video_codec": summary["video_codec"],
        "audio_codec": summary["audio_codec"],
        "has_audio": summary["has_audio"],
        "size_bytes": summary["size_bytes"],
        "bitrate": summary["bitrate"],
        "target_resolution": {
            "width": cfg_get(cfg, "project.resolution.width", 1080),
            "height": cfg_get(cfg, "project.resolution.height", 1920),
        },
        "target_framerate": cfg_get(cfg, "project.framerate", 30),
        "source_is_vertical": summary["height"] is not None
        and summary["width"] is not None
        and summary["height"] > summary["width"],
        "created_at": now_iso(),
    }
    write_json(job_dir / "metadata.json", metadata)
    return metadata


# --------------------------------------------------------------------------
# etapa 1 — GATEKEEPER (TRAVA INVIOLÁVEL)
# --------------------------------------------------------------------------


def ensure_rights_file(
    job_dir: Path, job_id: str, video: Path, creator: Optional[str], cfg: Dict[str, Any]
) -> Path:
    rights_path = job_dir / "rights.json"
    if rights_path.exists():
        return rights_path

    default_status = cfg_get(cfg, "legal_gatekeeper.default_status", "PENDING")
    payload = {
        "source_id": f"{job_id}:{video.name}",
        "creator_handle": creator or "",
        "status": default_status,
        "commercial_permission": False,
        "evidence_url_or_path": "",
        "expiration_date": None,
        "created_at": now_iso(),
        "note": (
            "Arquivo criado automaticamente com default_status do "
            "pipeline_config.yaml. Aprove explicitamente com 'init-job --approved'."
        ),
    }
    write_json(rights_path, payload)
    return rights_path


def check_rights(rights_path: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Avalia o rights.json. Retorna um dict com 'ok' (bool) e o diagnóstico.

    Aprovado exige SIMULTANEAMENTE:
      - status ∈ allowed_statuses (default: ["APPROVED"])
      - commercial_permission is True (quando require_explicit_monetization_rights)
      - nenhum campo obrigatório em branco
    """
    allowed = cfg_get(cfg, "legal_gatekeeper.allowed_statuses", ["APPROVED"])
    if isinstance(allowed, str):
        allowed = [allowed]
    require_commercial = bool(
        cfg_get(cfg, "legal_gatekeeper.require_explicit_monetization_rights", True)
    )

    reasons: List[str] = []
    raw: Dict[str, Any] = {}

    if not rights_path.exists():
        reasons.append(f"rights.json ausente em {rights_path}")
    else:
        try:
            raw = read_json(rights_path)
        except (json.JSONDecodeError, OSError) as exc:
            reasons.append(f"rights.json ilegível: {exc}")
            raw = {}

    if raw:
        status = str(raw.get("status", "")).strip().upper()
        status_ok = status in {str(a).strip().upper() for a in allowed}
        if not status_ok:
            reasons.append(
                f"status='{raw.get('status')}' não está em allowed_statuses={allowed}"
            )

        commercial = raw.get("commercial_permission")
        if require_commercial and commercial is not True:
            reasons.append(
                f"commercial_permission={commercial!r} (exige explicitamente true)"
            )

        if not str(raw.get("evidence_url_or_path", "")).strip():
            reasons.append("evidence_url_or_path vazio (sem prova de autorização)")

        if not str(raw.get("source_id", "")).strip():
            reasons.append("source_id vazio")

        exp = raw.get("expiration_date")
        if exp:
            try:
                exp_date = datetime.fromisoformat(str(exp)[:10])
                if exp_date < datetime.now().replace(hour=0, minute=0, second=0, microsecond=0):
                    reasons.append(f"direitos expirados em {exp}")
            except ValueError:
                reasons.append(f"expiration_date inválida: {exp!r}")

    return {
        "ok": len(reasons) == 0,
        "allowed_statuses": allowed,
        "require_explicit_monetization_rights": require_commercial,
        "reasons": reasons,
        "rights": raw,
    }


# --------------------------------------------------------------------------
# etapas 2-5 — análise
# --------------------------------------------------------------------------


def _coerce_dict(result: Any, stage: str) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    raise PipelineError(stage, f"resultado inesperado do analisador: {type(result)!r}")


def stage_transcript(
    job_dir: Path, video: Path, mods: Dict[str, Any], logger: JobLogger
) -> Dict[str, Any]:
    analyze_audio = require_module(mods, "analyze_audio", "transcript", "whisper_analyzer")
    logger.info(f"transcrevendo {video.name} (word-level, PT-BR)", stage="transcript")
    raw = analyze_audio(str(video), out_dir=str(job_dir))
    transcript = _coerce_dict(raw, "transcript")

    words = transcript.get("words") or []
    transcript.setdefault("duration", None)
    transcript["word_count"] = len(words)
    transcript["audio_event_count"] = len(transcript.get("audio_events") or [])
    transcript["analyzed_at"] = now_iso()

    write_json(job_dir / "transcript.json", transcript)
    logger.info(
        f"{len(words)} palavras, device={transcript.get('device_used')}, "
        f"model={transcript.get('model_used')}",
        stage="transcript",
    )
    if not words:
        logger.warn(
            "transcrição sem palavras — candidatos dependerão só de visão/áudio",
            stage="transcript",
        )
    return transcript


def stage_vision(
    job_dir: Path, video: Path, cfg: Dict[str, Any], mods: Dict[str, Any],
    transcript: Dict[str, Any], logger: JobLogger,
) -> Dict[str, Any]:
    analyze_vision = require_module(mods, "analyze_vision", "vision", "vision_tracker")
    logger.info("amostragem de visão (facecam + movimento)", stage="vision")
    raw = analyze_vision(str(video), config=cfg)
    vision = _coerce_dict(raw, "vision")
    vision["analyzed_at"] = now_iso()
    write_json(job_dir / "vision.json", vision)

    events = transcript.get("audio_events") or []
    audio_events = {
        "job_id": job_dir.name,
        "source": "whisper_analyzer.audio_events",
        "count": len(events),
        "events": events,
        "generated_at": now_iso(),
    }
    write_json(job_dir / "audio_events.json", audio_events)

    logger.info(
        f"crop_mode recomendado={vision.get('recommended_crop_mode')} "
        f"passive_ratio={vision.get('passive_ratio')}",
        stage="vision",
    )
    logger.info(f"{len(events)} eventos de áudio", stage="vision")
    return vision


def serialize_candidate(candidate: Any) -> Dict[str, Any]:
    if isinstance(candidate, dict):
        return candidate
    if hasattr(candidate, "model_dump"):
        return candidate.model_dump(mode="json")
    return dict(vars(candidate))


def serialize_plan(plan: Any) -> Dict[str, Any]:
    if isinstance(plan, dict):
        return plan
    if hasattr(plan, "model_dump"):
        return plan.model_dump(mode="json")
    return dict(vars(plan))


def stage_candidates(
    job_dir: Path, job_id: str, transcript: Dict[str, Any], vision: Dict[str, Any],
    cfg: Dict[str, Any], mods: Dict[str, Any], top_k: int, logger: JobLogger,
) -> List[Any]:
    detect_candidates = require_module(
        mods, "detect_candidates", "candidates", "viral_detector"
    )
    logger.info(f"pontuando candidatos (top_k={top_k})", stage="candidates")
    found = detect_candidates(transcript, vision=vision, config=cfg, top_k=top_k)
    candidates = list(found or [])
    if not candidates:
        raise PipelineError(
            "candidates", "detector não retornou nenhum candidato viral"
        )

    payload = {
        "job_id": job_id,
        "count": len(candidates),
        "top_k": top_k,
        "candidates": [serialize_candidate(c) for c in candidates],
        "generated_at": now_iso(),
    }
    write_json(job_dir / "candidates.json", payload)
    for cand in payload["candidates"]:
        logger.info(
            f"#{cand.get('candidate_id')} score={cand.get('score')} "
            f"{cand.get('category')} {cand.get('start_time')}s→{cand.get('end_time')}s",
            stage="candidates",
        )
    return candidates


def stage_edit_plans(
    job_dir: Path, job_id: str, video: Path, candidates: List[Any],
    transcript: Dict[str, Any], vision: Dict[str, Any], cfg: Dict[str, Any],
    mods: Dict[str, Any], logger: JobLogger,
) -> List[Dict[str, Any]]:
    build_edit_plan = require_module(
        mods, "build_edit_plan", "edit_plan", "viral_detector"
    )
    plans: List[Dict[str, Any]] = []
    for candidate in candidates:
        cand_dict = serialize_candidate(candidate)
        cand_id = safe_slug(str(cand_dict.get("candidate_id", "cand")))
        raw_plan = build_edit_plan(
            job_id,
            candidate,
            transcript,
            vision,
            cfg,
            str(video.resolve()),
        )
        plan = serialize_plan(raw_plan)

        # garantias mínimas do contrato EditPlan
        plan.setdefault("job_id", job_id)
        plan.setdefault("candidate_id", cand_id)
        plan["source_video_path"] = str(video.resolve())
        plan.setdefault("composition_template", "GTACinematic")
        plan.setdefault("crop_mode", cfg_get(cfg, "crop_modes.default", "split_screen"))
        plan.setdefault("words", transcript.get("words") or [])
        plan.setdefault("sfx_triggers", [])
        plan.setdefault("background_music_track", None)

        duration = plan.get("output_duration_seconds")
        if not duration:
            start = float(cand_dict.get("start_time", 0.0) or 0.0)
            end = float(cand_dict.get("end_time", start + 15.0) or (start + 15.0))
            duration = round(max(1.0, end - start), 3)
        plan["output_duration_seconds"] = round(float(duration), 3)

        # política de áudio: nunca aceitar faixa comercial
        if cfg_get(cfg, "audio_policy.allow_commercial_music", False) is not True:
            music = plan.get("background_music_track")
            music_lib = str(cfg_get(cfg, "audio_policy.local_music_library", ""))
            if music and music_lib and not str(music).startswith(("assets/", music_lib)):
                logger.warn(
                    f"faixa '{music}' não está na biblioteca NoCopyright — removida "
                    f"por audio_policy",
                    stage="edit_plan",
                )
                plan["background_music_track"] = None

        write_json(job_dir / f"edit_plan_{cand_id}.json", plan)
        plans.append(plan)
        logger.info(
            f"plano {cand_id}: {plan['composition_template']} "
            f"{plan['output_duration_seconds']}s ({len(plan['words'])} palavras)",
            stage="edit_plan",
        )

    write_json(job_dir / "edit_plan.json", plans[0])
    return plans


# --------------------------------------------------------------------------
# etapa 6 — render Remotion
# --------------------------------------------------------------------------


def stage_source_for_remotion(
    video: Path, job_id: str, cand_id: str
) -> Dict[str, Any]:
    """
    Copia (ou linka) o vídeo de origem para ``remotion/public/`` com um nome
    determinístico e devolve o caminho que a composição deve usar.

    O servidor estático do Remotion só serve arquivos dentro de
    ``<bundle>/public`` e não consegue baixar URLs ``file://``; por isso o
    componente recebe um caminho relativo à pasta public.
    """
    if not video.exists():
        return {"status": "failed", "reason": f"vídeo inexistente: {video}"}

    public_dir = REMOTION_DIR / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    name = f"gsf_{safe_slug(job_id)}_{safe_slug(cand_id)}{video.suffix or '.mp4'}"
    dest = public_dir / name

    try:
        need_copy = True
        if dest.exists() and dest.stat().st_size == video.stat().st_size:
            need_copy = False
        if need_copy:
            try:
                # hardlink economiza disco no mesmo filesystem
                if dest.exists():
                    dest.unlink()
                os.link(video, dest)
            except OSError:
                shutil.copy2(video, dest)
    except OSError as exc:
        return {"status": "failed", "reason": f"copy falhou: {exc}"}

    return {
        "status": "staged",
        "remotion_src": name,
        "public_path": str(dest),
        "size_bytes": dest.stat().st_size,
    }


def stage_render(
    job_dir: Path, job_id: str, plan: Dict[str, Any], do_render: bool,
    logger: JobLogger,
) -> Dict[str, Any]:
    composition = str(plan.get("composition_template") or "GTACinematic")
    cand_id = safe_slug(str(plan.get("candidate_id", "cand")))
    out_dir = job_dir / "render"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{job_id}_{cand_id}.mp4"

    if not do_render:
        logger.warn("render desabilitado (--no-render)", stage="render")
        return {
            "status": "skipped",
            "reason": "render desabilitado por --no-render",
            "composition_id": composition,
            "output_path": None,
        }

    if not REMOTION_DIR.exists():
        logger.warn(f"diretório {REMOTION_DIR} ausente — render pulado", stage="render")
        return {
            "status": "skipped",
            "reason": f"{REMOTION_DIR} não existe",
            "composition_id": composition,
            "output_path": None,
        }

    if not (REMOTION_DIR / "node_modules").exists():
        logger.warn(
            "remotion/node_modules ausente (rode 'npm install') — render pulado",
            stage="render",
        )
        return {
            "status": "skipped",
            "reason": "remotion/node_modules ausente; execute 'cd remotion && npm install'",
            "composition_id": composition,
            "output_path": None,
        }

    if not shutil.which("npx"):
        logger.warn("npx não encontrado no PATH — render pulado", stage="render")
        return {
            "status": "skipped",
            "reason": "npx não encontrado no PATH",
            "composition_id": composition,
            "output_path": None,
        }

    entry = REMOTION_DIR / "src" / "index.ts"

    # Remotion's bundle server only serves files inside <bundle>/public, and it
    # cannot download file:// URLs. Stage the source video into public/ under a
    # deterministic job-scoped name and hand the composition that name.
    staging = stage_source_for_remotion(Path(plan["source_video_path"]), job_id, cand_id)
    if staging["status"] != "staged":
        logger.warn(
            f"não foi possível preparar o vídeo para o Remotion: {staging.get('reason')}",
            stage="render",
        )
        return {
            "status": "skipped",
            "reason": f"staging do vídeo falhou: {staging.get('reason')}",
            "composition_id": composition,
            "output_path": None,
        }

    staged_props = dict(plan)
    staged_props["source_video_path"] = staging["remotion_src"]
    staged_props_path = out_dir / f"props_{cand_id}.json"
    write_json(staged_props_path, staged_props)

    cmd = [
        "npx",
        "remotion",
        "render",
        str(entry),
        composition,
        str(out_file),
        f"--props={staged_props_path}",
        "--codec=h264",
        "--log=error",
    ]
    logger.info(" ".join(cmd), stage="render")
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=str(REMOTION_DIR),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    elapsed = round(time.perf_counter() - t0, 2)

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
        logger.error(f"render falhou (exit {proc.returncode}): {tail}", stage="render")
        return {
            "status": "failed",
            "reason": f"remotion exit {proc.returncode}",
            "stderr_tail": tail,
            "composition_id": composition,
            "output_path": None,
            "elapsed_seconds": elapsed,
        }

    if not out_file.exists():
        logger.error("remotion retornou 0 mas o MP4 não existe", stage="render")
        return {
            "status": "failed",
            "reason": "MP4 não foi criado apesar do exit 0",
            "composition_id": composition,
            "output_path": None,
            "elapsed_seconds": elapsed,
        }

    logger.info(f"render OK: {out_file} ({out_file.stat().st_size} bytes)", stage="render")
    return {
        "status": "rendered",
        "reason": None,
        "composition_id": composition,
        "output_path": str(out_file),
        "size_bytes": out_file.stat().st_size,
        "elapsed_seconds": elapsed,
    }


# --------------------------------------------------------------------------
# etapa 7 — QC
# --------------------------------------------------------------------------

_BLACKDETECT_RE = re.compile(r"black_start:(?P<start>[\d.]+)\s+black_end:(?P<end>[\d.]+)")
_SILENCE_RE = re.compile(r"silence_start:\s*(?P<start>[\d.]+)")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(?P<db>-?[\d.]+)")
_PEAK_RE = re.compile(r"peak_level:\s*(?P<db>-?[\d.]+)")


def _ffmpeg_log(args: List[str], timeout: int = 600) -> str:
    ffmpeg = require_binary("ffmpeg")
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return (proc.stderr or "") + (proc.stdout or "")


def stage_qc(
    job_dir: Path, job_id: str, rights_ok: bool, target: Dict[str, int],
    music: Optional[str], render: Dict[str, Any], logger: JobLogger,
) -> Dict[str, Any]:
    failure_reasons: List[str] = []
    audio_clipping = False
    black_frames = False
    aspect_ok = False
    duration_ok = False

    out_path = render.get("output_path")
    if render.get("status") != "rendered" or not out_path:
        failure_reasons.append(
            f"render skipped ({render.get('reason') or 'sem saída'}): QC não pôde ser executado"
        )
        report = {
            "job_id": job_id,
            "passed": False,
            "audio_clipping_detected": False,
            "black_frames_detected": False,
            "aspect_ratio_valid": False,
            "rights_check_passed": rights_ok,
            "failure_reasons": failure_reasons,
            "checked_at": now_iso(),
            "artifact": None,
            "render_status": render.get("status"),
        }
        write_json(job_dir / "qc.json", report)
        logger.warn("QC reprovado: sem render válido", stage="qc")
        return report

    path = Path(out_path)
    info = ffprobe_json(path)
    summary = probe_summary(info)

    width = summary.get("width") or 0
    height = summary.get("height") or 0
    aspect_ok = width == target["width"] and height == target["height"]
    if not aspect_ok:
        failure_reasons.append(
            f"resolução {width}x{height} != {target['width']}x{target['height']}"
        )

    if not summary.get("has_audio"):
        failure_reasons.append("MP4 renderizado sem stream de áudio")

    if summary.get("height") and summary.get("width") and height <= width:
        failure_reasons.append(f"aspecto não vertical ({width}x{height})")

    duration = summary.get("duration_seconds") or 0.0
    duration_ok = duration >= 1.0
    if not duration_ok:
        failure_reasons.append(f"duração suspeita: {duration}s")

    # --- clipping de áudio ------------------------------------------------
    if summary.get("has_audio"):
        vol_log = _ffmpeg_log(
            ["-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"]
        )
        max_match = _MAX_VOLUME_RE.search(vol_log)
        if max_match:
            max_db = float(max_match.group("db"))
            audio_clipping = max_db >= -0.1
            if audio_clipping:
                failure_reasons.append(f"clipping de áudio (max_volume={max_db} dB)")
        else:
            # fallback: astats peaking
            stats_log = _ffmpeg_log(
                ["-i", str(path), "-af", "astats=metadata=1:reset=0", "-vn", "-f", "null", "-"]
            )
            peak_match = _PEAK_RE.search(stats_log)
            if peak_match:
                audio_clipping = float(peak_match.group("db")) >= -0.1
                if audio_clipping:
                    failure_reasons.append(
                        f"clipping de áudio (peak={peak_match.group('db')} dB)"
                    )

    # --- quadros pretos ---------------------------------------------------
    black_log = _ffmpeg_log(
        [
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=0.10:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    black_hits = [
        (float(m.group("start")), float(m.group("end")))
        for m in _BLACKDETECT_RE.finditer(black_log)
    ]
    # barras cinematográficas aparecem como "preto" só se cobrirem o quadro
    # inteiro; blackdetect já considera a luminância média da região toda.
    black_duration = sum(end - start for start, end in black_hits)
    black_frames = black_duration > max(1.0, 0.25 * duration)
    if black_frames:
        failure_reasons.append(
            f"quadros pretos excessivos ({black_duration:.2f}s de {duration:.2f}s)"
        )

    # --- política de áudio ------------------------------------------------
    if music and str(music).strip():
        failure_reasons.append(f"faixa de música presente no plano: {music}")

    passed = not failure_reasons
    report = {
        "job_id": job_id,
        "passed": passed,
        "audio_clipping_detected": audio_clipping,
        "black_frames_detected": black_frames,
        "aspect_ratio_valid": aspect_ok,
        "rights_check_passed": rights_ok,
        "failure_reasons": failure_reasons,
        "artifact": str(path),
        "resolution": {"width": width, "height": height},
        "duration_seconds": duration,
        "fps": summary.get("fps"),
        "has_audio": summary.get("has_audio"),
        "size_bytes": summary.get("size_bytes"),
        "black_segments": black_hits[:20],
        "checked_at": now_iso(),
    }

    # valida também contra o schema Pydantic quando disponível
    try:
        from core.schemas import QCReport  # type: ignore

        QCReport(**{k: report[k] for k in QCReport.model_fields if k in report})
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        report["schema_warning"] = f"{type(exc).__name__}: {exc}"

    write_json(job_dir / "qc.json", report)
    level = "INFO" if passed else "WARN"
    getattr(logger, level.lower() if level == "WARN" else "info")(
        f"QC {'PASSOU' if passed else 'FALHOU'}"
        + ("" if passed else f": {'; '.join(failure_reasons)}"),
        stage="qc",
    )
    return report


# --------------------------------------------------------------------------
# job_status
# --------------------------------------------------------------------------


def write_job_status(
    job_dir: Path, job_id: str, status: str, exit_code: int, trace: StageTrace,
    logger: JobLogger, **extra: Any,
) -> Dict[str, Any]:
    payload = {
        "job_id": job_id,
        "status": status,
        "exit_code": exit_code,
        "updated_at": now_iso(),
        "stages": trace.stages,
        "log_entries": logger.entries[-200:],
    }
    payload.update(extra)
    write_json(job_dir / "job_status.json", payload)
    return payload


# --------------------------------------------------------------------------
# comando: run
# --------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config()
    job_id = safe_slug(args.job_id)
    video = Path(args.video).expanduser().resolve()
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    logger = JobLogger(job_dir / "pipeline.log")
    trace = StageTrace()
    trace_started = now_iso()

    logger.info(
        f"=== início do job {job_id} (video={video}, render={args.render}, "
        f"dry_run={args.dry_run}) ==="
    )

    def fail(status: str, exit_code: int, stage: str, message: str, **extra: Any) -> int:
        logger.error(message, stage=stage)
        write_job_status(
            job_dir,
            job_id,
            status,
            exit_code,
            trace,
            logger,
            started_at=trace_started,
            stage=stage,
            error=message,
            **extra,
        )
        print(f"\n[{status}] {message}", file=sys.stderr)
        return exit_code

    try:
        # ---------------- etapa 0: metadados ----------------------------
        trace.start("metadata")
        try:
            metadata = stage_metadata(job_dir, job_id, video, args.creator, cfg)
        except PipelineError as exc:
            trace.finish("FAILED", error=exc.message)
            return fail(STATUS_FAILED, EXIT_FAILED, exc.stage, exc.message)
        trace.finish("OK", sha256=metadata["sha256"][:12],
                     duration=metadata["duration_seconds"])
        logger.info(
            f"metadata: {metadata['width']}x{metadata['height']} "
            f"{metadata['duration_seconds']}s fps={metadata['fps']} "
            f"sha256={metadata['sha256'][:12]}…",
            stage="metadata",
        )

        # ---------------- etapa 1: GATEKEEPER ---------------------------
        trace.start("gatekeeper")
        rights_path = ensure_rights_file(job_dir, job_id, video, args.creator, cfg)
        rights = check_rights(rights_path, cfg)

        gatekeeper_report = {
            "job_id": job_id,
            "checked_at": now_iso(),
            "rights_file": str(rights_path),
            "allowed_statuses": rights["allowed_statuses"],
            "require_explicit_monetization_rights": rights[
                "require_explicit_monetization_rights"
            ],
            "observed_status": rights["rights"].get("status"),
            "observed_commercial_permission": rights["rights"].get(
                "commercial_permission"
            ),
            "approved": rights["ok"],
            "reasons": rights["reasons"],
            "overridable_by_cli": False,
        }
        write_json(job_dir / "gatekeeper_check.json", gatekeeper_report)

        if not rights["ok"]:
            trace.finish("REJECTED", reasons=rights["reasons"])
            print("\n╔══════════════════════════════════════════════════════╗")
            print("║  GATEKEEPER: DIREITOS NÃO APROVADOS — ABORTANDO      ║")
            print("╚══════════════════════════════════════════════════════╝")
            for reason in rights["reasons"]:
                print(f"  • {reason}", file=sys.stderr)
            return fail(
                STATUS_REJECTED_RIGHTS,
                EXIT_REJECTED_RIGHTS,
                "gatekeeper",
                "rights check failed: " + "; ".join(rights["reasons"]),
                gatekeeper=gatekeeper_report,
            )
        trace.finish("OK", rights_status=rights["rights"].get("status"))
        logger.info(
            f"direitos APROVADOS (status={rights['rights'].get('status')}, "
            f"commercial_permission=true)",
            stage="gatekeeper",
        )

        # ---------------- módulos de análise ----------------------------
        mods = load_pipeline_modules()
        for name, err in mods["errors"].items():
            logger.warn(f"módulo '{name}' indisponível: {err}")
        write_json(
            job_dir / "modules.json",
            {
                "loaded": {
                    "analyze_audio": mods["analyze_audio"] is not None,
                    "analyze_vision": mods["analyze_vision"] is not None,
                    "detect_candidates": mods["detect_candidates"] is not None,
                    "build_edit_plan": mods["build_edit_plan"] is not None,
                },
                "errors": mods["errors"],
                "generated_at": now_iso(),
            },
        )

        # ---------------- etapa 2: transcrição --------------------------
        trace.start("transcript")
        try:
            transcript = stage_transcript(job_dir, video, mods, logger)
        except PipelineError as exc:
            trace.finish("FAILED", error=exc.message)
            return fail(STATUS_FAILED, EXIT_FAILED, exc.stage, exc.message,
                        modules=mods["errors"])
        trace.finish("OK", words=transcript.get("word_count"))

        # ---------------- etapa 3: visão + eventos de áudio -------------
        trace.start("vision")
        try:
            vision = stage_vision(job_dir, video, cfg, mods, transcript, logger)
        except PipelineError as exc:
            trace.finish("FAILED", error=exc.message)
            return fail(STATUS_FAILED, EXIT_FAILED, exc.stage, exc.message,
                        modules=mods["errors"])
        trace.finish("OK", crop_mode=vision.get("recommended_crop_mode"))

        # ---------------- etapa 4: candidatos ---------------------------
        trace.start("candidates")
        try:
            candidates = stage_candidates(
                job_dir, job_id, transcript, vision, cfg, mods, args.top_k, logger
            )
        except PipelineError as exc:
            trace.finish("FAILED", error=exc.message)
            return fail(STATUS_FAILED, EXIT_FAILED, exc.stage, exc.message,
                        modules=mods["errors"])
        trace.finish("OK", count=len(candidates))

        # ---------------- etapa 5: edit plans ---------------------------
        trace.start("edit_plan")
        try:
            plans = stage_edit_plans(
                job_dir, job_id, video, candidates, transcript, vision, cfg, mods, logger
            )
        except PipelineError as exc:
            trace.finish("FAILED", error=exc.message)
            return fail(STATUS_FAILED, EXIT_FAILED, exc.stage, exc.message)
        except Exception as exc:  # noqa: BLE001
            trace.finish("FAILED", error=str(exc))
            return fail(
                STATUS_FAILED,
                EXIT_FAILED,
                "edit_plan",
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            )
        trace.finish("OK", count=len(plans))

        if args.dry_run:
            logger.warn("--dry-run: encerrando antes do render", stage="render")
            trace.start("render")
            trace.finish("SKIPPED", reason="dry-run")
            return fail(
                STATUS_SKIPPED_RENDER,
                EXIT_OK,
                "render",
                "dry-run: análise concluída, render e QC não executados",
                dry_run=True,
            )

        # ---------------- etapa 6: render -------------------------------
        target = {
            "width": int(cfg_get(cfg, "project.resolution.width", 1080)),
            "height": int(cfg_get(cfg, "project.resolution.height", 1920)),
        }
        all_renders: List[Dict[str, Any]] = []
        trace.start("render")
        for plan in plans:
            result = stage_render(job_dir, job_id, plan, args.render, logger)
            result["candidate_id"] = plan.get("candidate_id")
            all_renders.append(result)

        write_json(
            job_dir / "render_report.json",
            {"job_id": job_id, "renders": all_renders, "generated_at": now_iso()},
        )
        trace.finish(
            "OK" if all(r["status"] == "rendered" for r in all_renders) else "SKIPPED",
            rendered=sum(1 for r in all_renders if r["status"] == "rendered"),
            total=len(all_renders),
        )

        # ---------------- etapa 7: QC -----------------------------------
        primary_plan = plans[0]
        primary_render = all_renders[0] if all_renders else {
            "status": "skipped",
            "reason": "nenhum render",
            "output_path": None,
        }
        trace.start("qc")
        qc = stage_qc(
            job_dir,
            job_id,
            rights_ok=True,
            target=target,
            music=primary_plan.get("background_music_track"),
            render=primary_render,
            logger=logger,
        )
        trace.finish("OK" if qc["passed"] else "WARN", passed=qc["passed"])

        # ---------------- status final ----------------------------------
        any_rendered = any(r["status"] == "rendered" for r in all_renders)
        if not any_rendered:
            final_status = STATUS_SKIPPED_RENDER
            exit_code = EXIT_OK
            note = "; ".join(
                r.get("reason") or "" for r in all_renders if r.get("reason")
            ) or "nenhum render executado"
        elif qc["passed"]:
            final_status = STATUS_COMPLETED
            exit_code = EXIT_OK
            note = f"{len(plans)} plano(s), {sum(1 for r in all_renders if r['status']=='rendered')} render(s), QC OK"
        else:
            final_status = STATUS_FAILED
            exit_code = EXIT_FAILED
            note = "QC reprovado: " + "; ".join(qc.get("failure_reasons", []))

        logger.info(f"=== job {job_id} finalizado: {final_status} ===")
        write_job_status(
            job_dir,
            job_id,
            final_status,
            exit_code,
            trace,
            logger,
            started_at=trace_started,
            stage="done",
            note=note,
            candidates=len(plans),
            renders=all_renders,
            qc_passed=qc["passed"],
            metadata={"sha256": metadata["sha256"], "duration": metadata["duration_seconds"]},
        )
        print(f"\n[{final_status}] job={job_id} — {note}")
        print(f"Artefatos: {job_dir}")
        return exit_code

    except KeyboardInterrupt:
        trace.finish("INTERRUPTED")
        write_job_status(
            job_dir, job_id, STATUS_FAILED, EXIT_FAILED, trace, logger,
            started_at=trace_started, stage="interrupt",
            error="KeyboardInterrupt — execução cancelada pelo usuário",
        )
        print(f"\n[FAILED] job {job_id} interrompido pelo usuário", file=sys.stderr)
        return EXIT_FAILED

    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        write_job_status(
            job_dir, job_id, STATUS_FAILED, EXIT_FAILED, trace, logger,
            started_at=trace_started, stage="unhandled",
            error=f"{type(exc).__name__}: {exc}", traceback=tb,
        )
        print(f"\n[FAILED] erro inesperado: {exc}\n{tb}", file=sys.stderr)
        return EXIT_FAILED


# --------------------------------------------------------------------------
# comando: init-job
# --------------------------------------------------------------------------


def cmd_init_job(args: argparse.Namespace) -> int:
    cfg = load_config()
    job_id = safe_slug(args.job_id)
    video = Path(args.video).expanduser().resolve()
    evidence = str(args.evidence or "").strip()
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    if not video.exists():
        print(f"erro: vídeo não encontrado: {video}", file=sys.stderr)
        return EXIT_FAILED

    status = "APPROVED" if args.approved else cfg_get(
        cfg, "legal_gatekeeper.default_status", "PENDING"
    )
    rights = {
        "source_id": f"{job_id}:{video.name}",
        "creator_handle": args.creator or "",
        "status": status,
        "commercial_permission": bool(args.approved),
        "evidence_url_or_path": evidence,
        "expiration_date": args.expiration,
        "created_at": now_iso(),
        "sha256": sha256_file(video),
        "initialized_by": "main.py init-job",
    }
    write_json(job_dir / "rights.json", rights)

    verdict = check_rights(job_dir / "rights.json", cfg)
    print(f"job: {job_id}")
    print(f"dir: {job_dir}")
    print(f"rights.json: status={status} commercial_permission={rights['commercial_permission']}")
    if verdict["ok"]:
        print("gatekeeper: APROVADO — 'python main.py run' pode prosseguir")
        return EXIT_OK
    print("gatekeeper: BLOQUEADO")
    for reason in verdict["reasons"]:
        print(f"  - {reason}")
    print("Aprove com: python main.py init-job ... --approved --evidence <url|path>")
    return EXIT_OK  # init-job sempre sucede; quem bloqueia é o 'run'


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="GTA Shorts Factory — orquestrador do pipeline (viral → edit plan → render → QC)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="executa o pipeline completo")
    run.add_argument("--video", required=True, help="caminho do vídeo longo de origem")
    run.add_argument("--job-id", required=True, help="identificador do job")
    run.add_argument("--creator", default=None, help="handle do criador, ex: @rui")
    run.add_argument("--top-k", type=int, default=10, help="número de candidatos (default 10)")
    run.add_argument("--render", dest="render", action="store_true", default=True,
                     help="executa o render Remotion (default)")
    run.add_argument("--no-render", dest="render", action="store_false",
                     help="não renderiza (para em SKIPPED_RENDER)")
    run.add_argument("--dry-run", action="store_true",
                     help="só análise (metadata, gatekeeper, transcrição, visão, planos)")
    run.set_defaults(func=cmd_run)

    init = sub.add_parser("init-job", help="cria o rights.json de um job")
    init.add_argument("--job-id", required=True)
    init.add_argument("--video", required=True)
    init.add_argument("--creator", default=None)
    init.add_argument("--evidence", default=None, help="url ou path da autorização")
    init.add_argument("--expiration", default=None, help="data de expiração YYYY-MM-DD")
    init.add_argument("--approved", action="store_true",
                      help="marca APPROVED + commercial_permission=true")
    init.set_defaults(func=cmd_init_job)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrompido", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
