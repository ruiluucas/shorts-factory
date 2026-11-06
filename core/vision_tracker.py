"""Análise visual de vídeo: facecam, intensidade de movimento e tipo de cena.

Usado pelo scorer do GTA Shorts Factory para decidir o `crop_mode` do EditPlan
(`split_screen` vs `gameplay_centered`) e para penalizar cutscenes passivas.

Pipeline de detecção de rosto (escolhida uma vez por processo, em ordem):
    1. MediaPipe FaceDetector (`mediapipe.tasks.vision`) + modelo BlazeFace local
       em `assets/models/blaze_face_short_range.tflite`.
    2. `cv2.CascadeClassifier` com `haarcascade_frontalface_default.xml`
       (só existe em builds antigos do opencv; aberto sob demanda).
    3. Nenhum detectador disponível -> `detect_faces` devolve [] e loga um aviso.

Desempenho: o vídeo é decodificado pelo OpenCV e cada frame é reduzido para a
largura `ANALYSIS_WIDTH` (640px) ANTES de motion/face. Em 1080x1920 isso é ~3x
menos pixels e mantém a análise em tempo real na GTX 1650/i5. Não use YOLO:
pesado e desnecessário para esta heurística.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from pathlib import Path
from typing import Any, Iterator, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Constantes de análise
# --------------------------------------------------------------------------

ANALYSIS_WIDTH = 640
"""Largura para a qual todo frame é reduzido antes de motion/face."""

DEFAULT_FACE_THRESHOLD = 0.70
"""`crop_modes.facecam_threshold` de config/pipeline_config.yaml."""

MIN_FACE_AREA_RATIO = 0.01
"""Área mínima do rosto (fração do frame) para considerá-lo uma facecam."""

MAX_FACE_AREA_RATIO = 0.80
"""Acima disso é quase certamente falso positivo (textura/UI ocupando a tela)."""

FACE_CONFIDENCE_MIN = 0.50
"""Confiança mínima para aceitar uma detecção."""

MOTION_HIGH = 0.012
"""Motion acima disso = gameplay ativo. Calibrado: testsrc2 1080x1920 a 2fps
deu mediana 0.023 (min 0.008); vídeo congelado dá 0.000."""

MOTION_PASSIVE = 0.004
"""Motion abaixo disso por `PASSIVE_WINDOW` amostras = cutscene passiva.

Encostar no limite (nunca abaixo de `MOTION_PASSIVE` por várias amostras) é o
comportamento em vídeo estático comprimido: o `passive_ratio` só sobe depois de
`PASSIVE_WINDOW` amostras baixas seguidas, por isso um clipe curto pode ficar
em 0.0 mesmo sem movimento.
"""

PASSIVE_WINDOW = 4
"""Nº de amostras consecutivas de motion baixo para marcar cutscene."""

_MODEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "models"
    / "blaze_face_short_range.tflite"
)

_haar_lock = threading.Lock()
_haar_classifier: Optional["cv2.CascadeClassifier"] = None
_haar_failed = False

_mp_lock = threading.Lock()
_mp_detector: Any = None
_mp_failed = False


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def _load_config(config: Optional[dict] = None) -> dict:
    """Retorna config mesclada (YAML do projeto + override) ou defaults."""
    cfg: dict = {
        "facecam_threshold": DEFAULT_FACE_THRESHOLD,
        "default_crop": "split_screen",
        "fallback_crop": "gameplay_centered",
    }
    if config is None:
        yaml_path = Path(__file__).resolve().parent.parent / "config" / "pipeline_config.yaml"
        if yaml_path.exists():
            try:
                import yaml

                with open(yaml_path, "r", encoding="utf-8") as fh:
                    config = (yaml.safe_load(fh) or {}).get("crop_modes", {})
            except Exception as exc:  # pragma: no cover - config opcional
                logger.warning("Falha ao ler %s: %s (usando defaults)", yaml_path, exc)
                config = {}
    if isinstance(config, dict):
        crop = config.get("crop_modes", config)
        if not isinstance(crop, dict):
            crop = {}
        if "facecam_threshold" in crop:
            try:
                cfg["facecam_threshold"] = float(crop["facecam_threshold"])
            except (TypeError, ValueError):
                logger.warning("facecam_threshold inválido: %r", crop["facecam_threshold"])
        cfg["default_crop"] = crop.get("default", cfg["default_crop"])
        cfg["fallback_crop"] = crop.get("fallback", cfg["fallback_crop"])
    return cfg


# --------------------------------------------------------------------------
# Amostragem
# --------------------------------------------------------------------------


def sample_frames(
    video_path: str | os.PathLike, fps_sample: float = 2.0
) -> Iterator[tuple[float, np.ndarray]]:
    """Itera `(timestamp_s, frame_bgr)` a `fps_sample` quadros por segundo.

    Pula quadros via `cap.grab()` (barato) e só decodifica com `retrieve()` os
    frames efetivamente amostrados. Garante liberação do recurso.
    """
    if fps_sample <= 0:
        raise ValueError(f"fps_sample deve ser > 0, recebido {fps_sample}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.error("Não foi possível abrir o vídeo: %s", video_path)
        return

    try:
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if not math.isfinite(src_fps) or src_fps <= 0:
            logger.warning("FPS inválido (%r) em %s; assumindo 30", src_fps, video_path)
            src_fps = 30.0
        step = max(1, int(round(src_fps / fps_sample)))

        idx = 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok or frame is None or frame.size == 0:
                    logger.debug("Frame vazio em idx=%d, ignorado", idx)
                else:
                    yield idx / src_fps, frame
            idx += 1
    finally:
        cap.release()


def _resize_for_analysis(frame: np.ndarray) -> np.ndarray:
    """Reduz o frame para `ANALYSIS_WIDTH`, preservando proporção."""
    h, w = frame.shape[:2]
    if w <= ANALYSIS_WIDTH:
        return frame
    scale = ANALYSIS_WIDTH / float(w)
    return cv2.resize(frame, (ANALYSIS_WIDTH, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)


def _to_gray(frame: np.ndarray) -> np.ndarray:
    """BGR -> gray reduzido e levemente suavizado (menos ruído no diff)."""
    small = _resize_for_analysis(frame)
    if small.ndim == 3:
        small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(small, (5, 5), 0)


# --------------------------------------------------------------------------
# Detecção de rosto
# --------------------------------------------------------------------------


def _get_mp_detector() -> Any:
    """Cria (lazy, uma vez) o FaceDetector do MediaPipe ou None se indisponível."""
    global _mp_detector, _mp_failed
    if _mp_detector is not None or _mp_failed:
        return _mp_detector
    with _mp_lock:
        if _mp_detector is not None or _mp_failed:
            return _mp_detector
        try:
            import mediapipe as mp

            if not _MODEL_PATH.exists():
                raise FileNotFoundError(f"modelo ausente: {_MODEL_PATH}")
            options = mp.tasks.vision.FaceDetectorOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(_MODEL_PATH)),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
                min_detection_confidence=FACE_CONFIDENCE_MIN,
            )
            _mp_detector = mp.tasks.vision.FaceDetector.create_from_options(options)
            logger.info("Face detection: MediaPipe (BlazeFace) ativo")
        except Exception as exc:
            logger.warning("MediaPipe indisponível (%s: %s); tentando Haar cascade", type(exc).__name__, exc)
            _mp_failed = True
    return _mp_detector


def _get_haar() -> Optional["cv2.CascadeClassifier"]:
    """Abre o haarcascade frontalface do cv2.data, se existir. Cacheado."""
    global _haar_classifier, _haar_failed
    if _haar_classifier is not None or _haar_failed:
        return _haar_classifier
    with _haar_lock:
        if _haar_classifier is not None or _haar_failed:
            return _haar_classifier
        try:
            data = getattr(getattr(cv2, "data", None), "haarcascades", None)
            if not data:
                raise FileNotFoundError("cv2.data.haarcascades indisponível")
            xml = os.path.join(data, "haarcascade_frontalface_default.xml")
            if not os.path.exists(xml):
                raise FileNotFoundError(f"{xml} não existe neste build do OpenCV")
            clf = cv2.CascadeClassifier(xml)
            if clf.empty():
                raise RuntimeError("CascadeClassifier vazio")
            _haar_classifier = clf
            logger.info("Face detection: Haar cascade ativo (%s)", xml)
        except Exception as exc:
            logger.warning("Haar cascade indisponível: %s", exc)
            _haar_failed = True
    return _haar_classifier


def detector_backend() -> str:
    """Nome do backend de detecção de rosto efetivamente em uso."""
    if _get_mp_detector() is not None:
        return "mediapipe"
    if _get_haar() is not None:
        return "haar"
    return "none"


def detect_faces(frame: np.ndarray) -> list[dict]:
    """Detecta rostos. Coordenadas retornadas em PIXELS DO FRAME ORIGINAL.

    Returns:
        Lista de `{'x', 'y', 'w', 'h', 'confidence'}`, confidence em 0..1.
    """
    if frame is None or frame.size == 0:
        return []
    h, w = frame.shape[:2]
    small = _resize_for_analysis(frame)
    sh, sw = small.shape[:2]
    sx, sy = w / float(sw), h / float(sh)

    det = _get_mp_detector()
    if det is not None:
        try:
            import mediapipe as mp

            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB) if small.ndim == 3 else small
            rgb = np.ascontiguousarray(rgb)
            result = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            out: list[dict] = []
            for d in result.detections:
                bb = d.bounding_box
                score = float(d.categories[0].score) if d.categories else 0.0
                x = max(0.0, bb.origin_x * sx)
                y = max(0.0, bb.origin_y * sy)
                bw = min(w - x, bb.width * sx)
                bh = min(h - y, bb.height * sy)
                if bw <= 1 or bh <= 1:
                    continue
                out.append(
                    {"x": float(x), "y": float(y), "w": float(bw), "h": float(bh),
                     "confidence": round(min(1.0, max(0.0, score)), 4)}
                )
            return out
        except Exception as exc:
            logger.warning("Falha no MediaPipe FaceDetector: %s (caindo para Haar)", exc)

    haar = _get_haar()
    if haar is not None:
        try:
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
            gray = cv2.equalizeHist(gray)
            faces = haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                          minSize=(30, 30))
            return [
                {"x": float(x * sx), "y": float(y * sy), "w": float(fw * sx),
                 "h": float(fh * sy), "confidence": 0.6}
                for (x, y, fw, fh) in faces
            ]
        except Exception as exc:
            logger.warning("Falha no Haar cascade: %s", exc)

    return []


# --------------------------------------------------------------------------
# Movimento
# --------------------------------------------------------------------------


def motion_intensity(prev_gray: Optional[np.ndarray], curr_gray: Optional[np.ndarray]) -> float:
    """Intensidade de movimento em 0..1 entre dois frames grayscale.

    Combina frame-diff normalizado (robusto a cortes) e magnitude média do
    optical flow de Farneback. Retorna 0.0 se qualquer entrada for inválida.
    """
    if prev_gray is None or curr_gray is None:
        return 0.0
    if prev_gray.size == 0 or curr_gray.size == 0:
        return 0.0
    if prev_gray.shape != curr_gray.shape:
        curr_gray = cv2.resize(curr_gray, (prev_gray.shape[1], prev_gray.shape[0]))
    try:
        diff = cv2.absdiff(prev_gray, curr_gray)
        diff_score = float(np.mean(diff)) / 255.0

        flow = cv2.calcOpticalFlowFarneback(
            prev_gray, curr_gray, None,
            pyr_scale=0.5, levels=2, winsize=15,
            iterations=2, poly_n=5, poly_sigma=1.1, flags=0,
        )
        mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        h, w = prev_gray.shape[:2]
        diag = math.hypot(w, h) or 1.0
        flow_score = float(np.mean(mag)) / diag

        # Flow é mais estável que diff puro; diff pega cortes bruscos.
        score = 0.7 * (flow_score * 8.0) + 0.3 * (diff_score * 3.0)
        if not math.isfinite(score):
            return 0.0
        return float(min(1.0, max(0.0, score)))
    except cv2.error as exc:
        logger.warning("motion_intensity falhou: %s", exc)
        return 0.0


# --------------------------------------------------------------------------
# Classificação de cena
# --------------------------------------------------------------------------


def classify_scene(
    frame: np.ndarray,
    lag_frames: int = PASSIVE_WINDOW,
    prev_gray: Optional[np.ndarray] = None,
    motion: Optional[float] = None,
) -> str:
    """Classifica um frame em `'facecam' | 'gameplay_active' | 'cutscene_passive'`.

    Regra: rosto suficientemente grande domina (facecam). Caso contrário,
    motion alto -> gameplay_active, motion muito baixo -> cutscene_passive.
    Sem referência anterior (motion desconhecido) devolve 'gameplay_active':
    é o default conservador, que não penaliza o candidato.

    Args:
        frame: frame BGR.
        lag_frames: janela de frames passivos usada como referência (documental).
        prev_gray: gray do frame anterior para calcular motion, se `motion` for None.
        motion: motion já calculado; evita recomputar.
    """
    faces = detect_faces(frame)
    if faces:
        h, w = frame.shape[:2]
        area = float(h * w) or 1.0
        biggest = max(faces, key=lambda f: f["w"] * f["h"])
        ratio = (biggest["w"] * biggest["h"]) / area
        if MIN_FACE_AREA_RATIO <= ratio <= MAX_FACE_AREA_RATIO:
            return "facecam"

    if motion is None:
        if prev_gray is not None:
            motion = motion_intensity(prev_gray, _to_gray(frame))
        else:
            return "gameplay_active"

    if motion >= MOTION_HIGH:
        return "gameplay_active"
    if motion <= MOTION_PASSIVE:
        return "cutscene_passive"
    return "gameplay_active"


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------


def analyze(
    video_path: str | os.PathLike,
    config: Optional[dict] = None,
    fps_sample: float = 2.0,
) -> dict:
    """Analisa o vídeo inteiro e recomenda o `crop_mode`.

    Returns:
        dict JSON-serializável com metadados, amostras, ratios e
        `recommended_crop_mode`.
    """
    cfg = _load_config(config)
    video_path = str(video_path)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Não foi possível abrir o vídeo: {video_path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        cap.release()
    if not math.isfinite(fps) or fps <= 0:
        logger.warning("FPS inválido em %s; assumindo 30", video_path)
        fps = 30.0
    duration = frame_count / fps if frame_count else 0.0

    samples: list[dict] = []
    prev_gray: Optional[np.ndarray] = None
    low_run = 0

    for t, frame in sample_frames(video_path, fps_sample=fps_sample):
        gray = _to_gray(frame)
        motion = motion_intensity(prev_gray, gray) if prev_gray is not None else 0.0
        faces = detect_faces(frame)

        face_box = None
        if faces:
            h, w = frame.shape[:2]
            area = float(h * w) or 1.0
            biggest = max(faces, key=lambda f: f["w"] * f["h"])
            if MIN_FACE_AREA_RATIO <= (biggest["w"] * biggest["h"]) / area <= MAX_FACE_AREA_RATIO:
                face_box = {k: round(float(biggest[k]), 2) for k in ("x", "y", "w", "h", "confidence")}

        if face_box is not None:
            scene = "facecam"
        elif prev_gray is None:
            scene = "gameplay_active"
        else:
            low_run = low_run + 1 if motion <= MOTION_PASSIVE else 0
            if motion >= MOTION_HIGH:
                scene = "gameplay_active"
            elif low_run >= PASSIVE_WINDOW:
                scene = "cutscene_passive"
            else:
                scene = "gameplay_active"

        samples.append({
            "t": round(float(t), 3),
            "faces": len(faces),
            "face_box": face_box,
            "motion": round(float(motion), 6),
            "scene": scene,
        })
        prev_gray = gray

    n = len(samples)
    facecam_ratio = (sum(1 for s in samples if s["face_box"] is not None) / n) if n else 0.0
    mean_motion = (sum(s["motion"] for s in samples) / n) if n else 0.0
    passive_ratio = (sum(1 for s in samples if s["scene"] == "cutscene_passive") / n) if n else 0.0

    threshold = cfg["facecam_threshold"]
    recommended = cfg["default_crop"] if facecam_ratio >= threshold else cfg["fallback_crop"]

    logger.info(
        "analyze %s: %d amostras, facecam_ratio=%.2f, mean_motion=%.4f, passive=%.2f -> %s",
        video_path, n, facecam_ratio, mean_motion, passive_ratio, recommended,
    )

    return {
        "video_path": video_path,
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration": round(duration, 3),
        "samples": samples,
        "facecam_ratio": round(facecam_ratio, 4),
        "mean_motion": round(mean_motion, 6),
        "passive_ratio": round(passive_ratio, 4),
        "recommended_crop_mode": recommended,
    }


def is_active_segment(
    samples: list[dict],
    start: float,
    end: float,
    min_motion: float = MOTION_HIGH,
) -> bool:
    """True se `[start, end]` tem gameplay ativo (motion médio >= `min_motion`).

    Um `facecam` sozinho não conta como passivo: reação com webcam é conteúdo.
    Intervalo sem amostras -> False.
    """
    if not samples or end <= start:
        return False
    window = [s for s in samples if start <= s["t"] <= end]
    if not window:
        # Pega a amostra mais próxima de start/end se a janela for estreita.
        midpoint = (start + end) / 2.0
        nearest = min(samples, key=lambda s: abs(s["t"] - midpoint))
        if abs(nearest["t"] - midpoint) > (end - start):
            return False
        window = [nearest]
    active = [s for s in window if s["scene"] == "gameplay_active" or s["faces"] > 0]
    if not active:
        return False
    return (sum(s["motion"] for s in active) / len(active)) >= min_motion or len(active) > len(window) / 2


def get_model_path() -> str:
    """Caminho do modelo BlazeFace usado pela detecção (para diagnóstico)."""
    return str(_MODEL_PATH)
