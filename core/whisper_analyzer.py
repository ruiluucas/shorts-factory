"""Análise de áudio/transcrição do GTA Shorts Factory.

Transcrição word-level em pt-BR via faster-whisper + detecção de eventos
sonoros (RMS, picos, clipping) via librosa/numpy.

Notas de hardware: GPU GTX 1650 com 4 GB de VRAM não comporta large-v3.
O config pede large-v3; aqui o modelo é configurável e rebaixado
automaticamente, com fallback para CPU se o CUDA falhar.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:  # execução como pacote (uso normal)
    from core.schemas import WordSegment
except ImportError:  # execução como script solto
    try:  # importado como módulo vizinho dentro do pacote
        from .schemas import WordSegment  # type: ignore[no-redef]
    except ImportError:
        import sys

        for _candidate in (
            Path(__file__).resolve().parent.parent,
            Path.cwd(),
        ):
            if str(_candidate) not in sys.path:
                sys.path.insert(0, str(_candidate))
        from core.schemas import WordSegment  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

FFMPEG_BIN: str = "/usr/sbin/ffmpeg"
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_PATH: Path = PROJECT_ROOT / "config" / "pipeline_config.yaml"

# Modelos em ordem de qualidade; os que cabem em ~4 GB de VRAM.
VRAM_SAFE_MODELS: tuple[str, ...] = ("tiny", "base", "small", "medium")
DEFAULT_MODEL: str = "small"
DEFAULT_COMPUTE_CUDA: str = "int8_float16"
DEFAULT_COMPUTE_CPU: str = "int8"

DEFAULT_CONFIG: Dict[str, Any] = {
    "transcription": {
        "engine": "faster-whisper",
        "model": DEFAULT_MODEL,
        "language": "pt",
        "align_model": "WAV2VEC2_ASR_LARGE_LV60K_960H",
        "compute_type": DEFAULT_COMPUTE_CUDA,
        "beam_size": 5,
        "vad_filter": True,
    }
}


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
def load_config(path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Carrega o YAML do pipeline; em erro, devolve defaults com aviso no log."""
    cfg_path = Path(path) if path is not None else CONFIG_PATH
    try:
        import yaml

        with cfg_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"YAML raiz não é um mapping: {type(data).__name__}")
        logger.info("Config carregado de %s", cfg_path)
        return data
    except FileNotFoundError:
        logger.warning("Config não encontrado em %s — usando defaults", cfg_path)
    except Exception as exc:  # YAML inválido, permissão, etc.
        logger.warning("Falha ao ler config %s (%s) — usando defaults", cfg_path, exc)
    return json.loads(json.dumps(DEFAULT_CONFIG))  # cópia profunda


def _resolve_whisper_params(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extrai parâmetros de transcrição do config, aplicando travas de VRAM.

    Lê `transcription.{model,language,compute_type}` e permite overrides via
    chaves `whisper_model`, `whisper_compute_type`.
    """
    cfg = config if config is not None else load_config()
    tx = cfg.get("transcription") or {}
    model = str(cfg.get("whisper_model") or tx.get("model") or DEFAULT_MODEL)
    compute_base = str(cfg.get("whisper_compute_type") or tx.get("compute_type") or "float16")
    language = str(tx.get("language") or "pt")
    beam_size = int(tx.get("beam_size", 5))
    vad_filter = bool(tx.get("vad_filter", True))

    if model in VRAM_SAFE_MODELS:
        model_used = model
    else:
        model_used = DEFAULT_MODEL
        logger.warning(
            "Modelo '%s' não cabe em 4 GB de VRAM (GTX 1650) — usando '%s'",
            model,
            model_used,
        )

    return {
        "model": model_used,
        "model_requested": model,
        "compute_type": compute_base,
        "language": language,
        "beam_size": beam_size,
        "vad_filter": vad_filter,
    }


# --------------------------------------------------------------------------- #
# Extração de áudio
# --------------------------------------------------------------------------- #
def extract_audio(video_path: Path | str, out_wav: Path | str) -> Path:
    """Extrai WAV mono 16 kHz do vídeo via ffmpeg. Retorna o caminho do WAV."""
    video = Path(video_path)
    wav = Path(out_wav)
    if not video.is_file():
        raise FileNotFoundError(f"Vídeo não encontrado: {video}")
    wav.parent.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [
        FFMPEG_BIN,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        str(wav),
    ]
    logger.debug("ffmpeg: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError as exc:
        raise RuntimeError(f"ffmpeg não encontrado em {FFMPEG_BIN}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg excedeu o timeout ao processar {video}") from exc

    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg falhou (exit {proc.returncode}) em {video.name}: "
            f"{(proc.stderr or '').strip()[:500]}"
        )
    if not wav.is_file() or wav.stat().st_size <= 44:  # 44 bytes = header WAV vazio
        raise RuntimeError(f"ffmpeg gerou WAV vazio para {video}: {wav}")
    logger.info("Áudio extraído: %s (%.1f KB)", wav, wav.stat().st_size / 1024)
    return wav


def _wav_duration(wav_path: Path, sr: int = 16000) -> float:
    """Duração do WAV em segundos (via soundfile; header-only)."""
    try:
        import soundfile as sf

        info = sf.info(str(wav_path))
        return float(info.duration)
    except Exception:  # soundfile ausente/corrompido -> fallback ffprobe
        try:
            out = subprocess.run(
                [
                    "/usr/sbin/ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(wav_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            ).stdout.strip()
            return float(out)
        except Exception:
            return 0.0


# --------------------------------------------------------------------------- #
# Transcrição
# --------------------------------------------------------------------------- #
_CUDA_SHIM_DIR = Path.home() / ".cache" / "gsf" / "cuda_soname_shim"
_CUDA_LIB_CANDIDATES: tuple[str, ...] = (
    "/opt/cuda/lib64",
    "/usr/local/cuda/lib64",
    "/usr/lib",
    "/usr/lib/x86_64-linux-gnu",
)
# ctranslate2 pede estes SONAMEs; hosts com CUDA 13 só têm a versão .13.
_CUDA_SONAME_MAP: Dict[str, tuple[str, ...]] = {
    "libcublas.so.12": ("libcublas.so.12", "libcublas.so.13", "libcublas.so"),
    "libcublasLt.so.12": ("libcublasLt.so.12", "libcublasLt.so.13", "libcublasLt.so"),
    "libcudart.so.12": ("libcudart.so.12", "libcudart.so.13", "libcudart.so"),
}


def _prepare_cuda_libs() -> Optional[str]:
    """Torna libs cuBLAS/cudart visíveis ao ctranslate2 neste processo.

    Muitos hosts (CUDA 13) não têm os SONAMEs .12 que o ctranslate2 solicita.
    Aqui procuramos a lib real nos diretórios do sistema e a carregamos com
    RTLD_GLOBAL, cobrindo também o caso em que só existe a versão nova.
    Retorna o diretório usado ou None.
    """
    import ctypes
    import os

    found_dir: Optional[str] = None
    for soname, alternatives in _CUDA_SONAME_MAP.items():
        target: Optional[str] = None
        for alt in alternatives:
            for base in _CUDA_LIB_CANDIDATES:
                candidate = os.path.join(base, alt)
                if os.path.exists(candidate):
                    target = candidate
                    break
            if target:
                break
        if not target:
            continue
        found_dir = found_dir or str(Path(target).parent)
        try:
            ctypes.CDLL(target, mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            logger.debug("Não foi possível carregar %s: %s", target, exc)
        # SONAME .12 ausente -> cria alias e registra o dir no loader.
        expected = os.path.join(_CUDA_LIB_CANDIDATES[0], soname)
        if not os.path.exists(expected):
            try:
                alias_dir = _CUDA_SHIM_DIR
                alias_dir.mkdir(parents=True, exist_ok=True)
                alias = alias_dir / soname
                if not alias.exists():
                    alias.symlink_to(target)
                cur = os.environ.get("LD_LIBRARY_PATH", "")
                if str(alias_dir) not in cur.split(":"):
                    os.environ["LD_LIBRARY_PATH"] = (
                        f"{alias_dir}:{cur}" if cur else str(alias_dir)
                    )
                logger.debug("Alias CUDA %s -> %s", soname, target)
            except OSError as exc:
                logger.debug("Falha ao criar alias %s: %s", soname, exc)
    return found_dir


def _load_audio_array(wav: Path, sr: int = 16000) -> np.ndarray:
    """Lê o WAV como float32 mono 16 kHz (via stdlib `wave`, sem PyAV)."""
    import wave

    with wave.open(str(wav), "rb") as fh:
        n_ch = fh.getnchannels()
        width = fh.getsampwidth()
        rate = fh.getframerate()
        raw = fh.readframes(fh.getnframes())
    if width != 2:
        raise RuntimeError(f"WAV inesperado: {width * 8} bits/amostra (esperado 16)")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch > 1:
        audio = audio.reshape(-1, n_ch).mean(axis=1)
    if rate != sr:  # offload para librosa só nesse caso raro
        import librosa

        audio = librosa.resample(audio, orig_sr=rate, target_sr=sr)
    return np.ascontiguousarray(audio, dtype=np.float32)


def _run_whisper(
    wav: Path,
    model_name: str,
    device: str,
    compute_type: str,
    language: str,
    beam_size: int,
    vad_filter: bool,
) -> List[WordSegment]:
    """Uma passada do faster-whisper em (device, compute_type) fixos."""
    from faster_whisper import WhisperModel

    started = time.perf_counter()
    logger.info(
        "faster-whisper: model=%s device=%s compute_type=%s lang=%s",
        model_name,
        device,
        compute_type,
        language,
    )
    # Passa array float32 já decodificado: não depende do decoder PyAV do
    # faster-whisper (versões de `av` divergem e quebram av.open()).
    audio = _load_audio_array(wav)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        audio,
        language=language,
        word_timestamps=True,
        vad_filter=vad_filter,
        beam_size=beam_size,
    )

    words: List[WordSegment] = []
    for seg in segments:  # generator: consome de fato (é aqui que o decode roda)
        for w in getattr(seg, "words", None) or []:
            text = (w.word or "").strip()
            if not text:
                continue
            words.append(
                WordSegment(
                    word=text,
                    start=float(w.start),
                    end=float(w.end),
                    score=float(w.probability if w.probability is not None else 0.0),
                    speaker=None,
                )
            )
    logger.info(
        "Transcrição: %d palavras em %.1fs (lang=%s prob=%.2f)",
        len(words),
        time.perf_counter() - started,
        getattr(info, "language", language),
        float(getattr(info, "language_probability", 0.0) or 0.0),
    )
    return words


def transcribe(
    video_path: Path | str,
    config: Optional[Dict[str, Any]] = None,
    device: str = "auto",
    model_name: Optional[str] = None,
    compute_type: Optional[str] = None,
    wav_path: Optional[Path] = None,
) -> tuple[List[WordSegment], str, str]:
    """Transcreve o vídeo word-level e retorna (words, device_used, model_used).

    `device='auto'` tenta CUDA e cai para CPU automaticamente em falha.
    """
    cfg = config if config is not None else load_config()
    params = _resolve_whisper_params(cfg)
    model_used = model_name or params["model"]
    if model_used not in VRAM_SAFE_MODELS:
        logger.warning("Modelo '%s' rebaixado para '%s' (4 GB VRAM)", model_used, DEFAULT_MODEL)
        model_used = DEFAULT_MODEL

    cuda_compute = compute_type or params["compute_type"] or DEFAULT_COMPUTE_CUDA

    # Converte float16 -> int8_float16 quando o alvo é CUDA (mais leve em 4 GB).
    if cuda_compute == "float16":
        cuda_compute = DEFAULT_COMPUTE_CUDA

    tmp_wav: Optional[Path] = None
    if wav_path is None:
        tmp_wav = Path("/tmp/gsf_test") / f"{Path(video_path).stem}_16k.wav"
        wav_path = extract_audio(video_path, tmp_wav)

    attempts: List[tuple[str, str]] = []
    if device in ("auto", "cuda"):
        # Ordem de compute types na GPU: o configurado primeiro, depois os que
        # não dependem de kernels cuBLAS int8 (hosts com CUDA 13 rejeitam estes).
        cuda_chain: List[str] = []
        for ct in (cuda_compute, "float16", DEFAULT_COMPUTE_CUDA, "int8"):
            if ct and ct not in cuda_chain:
                cuda_chain.append(ct)
        attempts.extend(("cuda", ct) for ct in cuda_chain)
    if device in ("auto", "cpu"):
        attempts.append(("cpu", DEFAULT_COMPUTE_CPU))
    if not attempts:
        attempts = [("cpu", DEFAULT_COMPUTE_CPU)]

    last_exc: Optional[Exception] = None
    for idx, (dev, ctype) in enumerate(attempts):
        try:
            if dev == "cuda":
                _prepare_cuda_libs()
            words = _run_whisper(
                wav_path,
                model_used,
                dev,
                ctype,
                params["language"],
                params["beam_size"],
                params["vad_filter"],
            )
            logger.info("Dispositivo de transcrição: %s (%s)", dev, ctype)
            return words, dev, model_used
        except Exception as exc:
            last_exc = exc
            has_next = idx + 1 < len(attempts)
            log = logger.warning if has_next else logger.error
            log(
                "Transcrição falhou em device=%s compute_type=%s: %s: %s%s",
                dev,
                ctype,
                type(exc).__name__,
                str(exc)[:300],
                " — tentando CPU" if has_next else "",
            )

    raise RuntimeError(f"Transcrição falhou em todos os dispositivos: {last_exc}") from last_exc


# --------------------------------------------------------------------------- #
# Eventos de áudio
# --------------------------------------------------------------------------- #
def detect_audio_events(wav_path: Path | str, sr: int = 16000) -> Dict[str, Any]:
    """RMS, picos de energia, clipping e classificação de silêncio."""
    import librosa

    path = Path(wav_path)
    if not path.is_file():
        raise FileNotFoundError(f"WAV não encontrado: {path}")

    y, sr_real = librosa.load(str(path), sr=sr, mono=True, dtype="float32")
    y = np.asarray(y, dtype=np.float32)
    if y.size == 0:
        logger.warning("WAV sem amostras: %s", path)
        return {
            "rms_series": [],
            "peaks": [],
            "clipping_ratio": 0.0,
            "mean_rms": 0.0,
            "is_quiet": True,
            "max_rms": 0.0,
            "duration": 0.0,
        }

    sr = int(sr_real)
    duration = float(y.size) / float(sr)

    # RMS por frame (~50 ms, hop de 10 ms).
    hop = max(1, int(0.01 * sr))
    frame = max(hop * 2, int(0.05 * sr))
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop, center=True)[0]
    rms = np.asarray(rms, dtype=np.float32)

    # Downsample para ~10 amostras/seg (não inflar o JSON).
    target_len = max(1, int(round(duration * 10)))
    rms_series = _resample_mean(rms, target_len)

    clipping_ratio = float(np.mean((y >= 0.99) | (y <= -0.99))) if y.size else 0.0
    mean_rms = float(np.mean(rms)) if rms.size else 0.0
    max_rms = float(np.max(rms)) if rms.size else 0.0

    peaks: List[Dict[str, float]] = []
    if rms.size:
        top_k = int(min(5, rms.size))
        top_idx = np.argpartition(rms, -top_k)[-top_k:]
        top_idx = top_idx[np.argsort(rms[top_idx])[::-1]]
        for i in top_idx:
            t_start = float(i) * hop / sr
            t_end = min(duration, t_start + frame / sr)
            peaks.append(
                {
                    "start": round(t_start, 3),
                    "end": round(t_end, 3),
                    "energy": round(float(rms[i]), 4),
                }
            )

    # Janela de 0.5 s para decidir "spike" (caos/reação).
    win = max(1, int(0.5 * sr / hop))
    loud_spikes: List[Dict[str, float]] = []
    if rms.size >= win and max_rms > 0:
        win_means = _moving_mean(rms, win)
        loud_ref = float(np.percentile(win_means, 90))
        # Spike = trecho sustentado claramente acima do corpo do áudio.
        # Nível "corpo" (mediana) e corte a 70% do nível alto, com piso de
        # ruído; multiplicadores >1 aqui nunca disparariam em conteúdo contínuo.
        high = max(loud_ref * 0.7, 0.02)
        # `_moving_mean` desliza 1 frame por hop -> cada amostra = hop/sr s.
        step_s = float(hop) / float(sr)
        above = win_means >= high
        i = 0
        n = len(above)
        while i < n and len(loud_spikes) < 20:
            if above[i]:
                j = i
                while j + 1 < n and above[j + 1]:
                    j += 1
                start_s = float(i) * step_s
                end_s = min(duration, float(j + 1) * step_s)
                if end_s - start_s >= 0.2:
                    loud_spikes.append(
                        {
                            "start": round(start_s, 3),
                            "end": round(end_s, 3),
                            "energy": round(float(np.max(win_means[i : j + 1])), 4),
                        }
                    )
                i = j + 1
            else:
                i += 1

    is_quiet = bool(mean_rms < 0.005 and max_rms < 0.02)
    result: Dict[str, Any] = {
        "rms_series": [round(float(v), 4) for v in rms_series],
        "peaks": peaks,
        "clipping_ratio": round(clipping_ratio, 6),
        "mean_rms": round(mean_rms, 4),
        "max_rms": round(max_rms, 4),
        "is_quiet": is_quiet,
        "duration": round(duration, 3),
        "sr": sr,
        "loud_spikes": loud_spikes,
        "has_clipping": bool(clipping_ratio > 0.0),
    }
    logger.info(
        "Eventos de áudio: dur=%.1fs mean_rms=%.4f max_rms=%.4f clip=%.4f%% peaks=%d spikes=%d quiet=%s",
        duration,
        mean_rms,
        max_rms,
        clipping_ratio * 100.0,
        len(peaks),
        len(loud_spikes),
        is_quiet,
    )
    return result


def _resample_mean(arr: np.ndarray, target_len: int) -> np.ndarray:
    """Reduz a série por média de blocos."""
    n = arr.size
    if n == 0 or target_len <= 0:
        return np.zeros(0, dtype=np.float32)
    if n <= target_len:
        idx = np.linspace(0, n - 1, target_len).astype(int)
        return arr[idx]
    edges = np.linspace(0, n, target_len + 1).astype(int)
    out = np.empty(target_len, dtype=np.float32)
    for i in range(target_len):
        a, b = edges[i], max(edges[i] + 1, edges[i + 1])
        out[i] = float(np.mean(arr[a:b]))
    return out


def _moving_mean(arr: np.ndarray, win: int) -> np.ndarray:
    """Média móvel simples (mesmo tamanho), via cumsum."""
    if win <= 1 or arr.size < win:
        return np.asarray(arr, dtype=np.float32)
    c = np.cumsum(np.insert(np.asarray(arr, dtype=np.float64), 0, 0.0))
    out = (c[win:] - c[:-win]) / float(win)
    pad = np.full(win - 1, out[0] if out.size else 0.0, dtype=np.float64)
    return np.concatenate([pad, out]).astype(np.float32)


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def analyze(
    video_path: Path | str,
    out_dir: Optional[Path | str] = None,
    config: Optional[Dict[str, Any]] = None,
    device: str = "auto",
) -> Dict[str, Any]:
    """Pipeline completo: extrai áudio, transcreve e detecta eventos de áudio."""
    started = time.perf_counter()
    video = Path(video_path)
    cfg = config if config is not None else load_config()

    work_dir = Path(out_dir) if out_dir is not None else Path("/tmp/gsf_test") / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / f"{video.stem}_16k.wav"

    audio_events: Dict[str, Any]
    try:
        extract_audio(video, wav)
        duration = _wav_duration(wav)
        audio_events = detect_audio_events(wav)
        if duration <= 0.0:
            duration = float(audio_events.get("duration", 0.0))
        words, device_used, model_used = transcribe(
            video, config=cfg, device=device, wav_path=wav
        )
    except Exception as exc:
        logger.error("analyze falhou em %s: %s: %s", video, type(exc).__name__, exc)
        raise

    result: Dict[str, Any] = {
        "video_path": str(video),
        "duration": round(float(duration), 3),
        "device_used": device_used,
        "model_used": model_used,
        "words": [w.model_dump() for w in words],
        "audio_events": audio_events,
    }

    if out_dir is not None:
        out_path = work_dir / "transcript.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        logger.info("transcript.json gravado em %s", out_path)
        result["transcript_path"] = str(out_path)

    logger.info(
        "analyze concluído em %.1fs: %d palavras, device=%s, model=%s",
        time.perf_counter() - started,
        len(words),
        device_used,
        model_used,
    )
    return result


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if len(sys.argv) < 2:
        print("uso: python -m core.whisper_analyzer <video.mp4> [out_dir]")
        raise SystemExit(2)
    out = analyze(sys.argv[1], out_dir=sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps({k: v for k, v in out.items() if k != "words"}, ensure_ascii=False, indent=2))
    print(f"words: {len(out['words'])}")
