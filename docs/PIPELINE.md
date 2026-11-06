# Pipeline GTA Shorts Factory

Documento técnico: como cada etapa funciona, quais contratos respeita e onde estender.

---

## Visão de execução

```
main.py run --video X --job-id Y
  │
  ├─ [0] metadata.json .......... sha256 + ffprobe
  ├─ [1] GATEKEEPER ............ rights.json → APPROVED? senão exit 3
  ├─ [2] transcript.json ....... whisper_analyzer.analyze()
  ├─ [3] vision.json ........... vision_tracker.analyze()
  ├─ [4] candidates.json ....... viral_detector.detect_candidates()
  ├─ [5] edit_plan_*.json ...... viral_detector.build_edit_plan()
  ├─ [6] out/*.mp4 ............. npx remotion render
  └─ [7] qc.json ............... ffprobe + ffmpeg filters
```

Cada etapa grava seu JSON em `workspace/jobs/{job_id}/` **antes** de passar para a
próxima. Se uma etapa falha, o `job_status.json` registra `FAILED` + traceback e
as etapas anteriores permanecem auditáveis.

---

## Etapa 2 — Transcrição e áudio

`core/whisper_analyzer.py`

| Função | Contrato |
|---|---|
| `load_config(path=None) -> dict` | carrega YAML com fallback para defaults |
| `extract_audio(video, out_wav) -> Path` | ffmpeg → WAV mono 16 kHz |
| `transcribe(video, config, device) -> list[WordSegment]` | faster-whisper, `word_timestamps=True`, pt-BR |
| `detect_audio_events(wav) -> dict` | RMS, peaks, clipping, is_quiet |
| `analyze(video, out_dir=None) -> dict` | orquestra e grava `transcript.json` |

**VRAM:** o config pede `large-v3`, mas a GTX 1650 tem 4 GB. O módulo resolve
isso automaticamente: tenta CUDA e, se faltar VRAM, cai para CPU com
`int8`. O campo `device_used` em `transcript.json` registra o que rodou de fato —
nunca assuma que foi GPU.

`audio_events.rms_series` é reduzido para ~10 amostras/segundo. Sem isso o JSON
de um vídeo de 40 min passaria de 100 MB.

---

## Etapa 3 — Visão

`core/vision_tracker.py`

| Função | Contrato |
|---|---|
| `sample_frames(video, fps_sample) -> Iterator[(t, frame)]` | amostragem, frames redimensionados para largura 640 |
| `detect_faces(frame) -> list[dict]` | MediaPipe Tasks se disponível, senão Haar |
| `motion_intensity(prev, curr) -> float` | frame diff / optical flow, 0–1 |
| `analyze(video, config, fps_sample) -> dict` | samples + agregados + `recommended_crop_mode` |

**Por que redimensionar:** decodificar 1080x1920 a 30 fps consome ~3 GB de RAM e
minutos de CPU. A 640 de largura com 2 fps de amostragem, um vídeo de 40 min leva
segundos. Movimento e presença de rosto não precisam de resolução cheia.

`recommended_crop_mode` segue o YAML:
`facecam_ratio >= facecam_threshold (0.70)` → `split_screen`, senão `gameplay_centered`.

---

## Etapa 4 — Scoring viral

`core/viral_detector.py`

```
score = Σ (componente_i × peso_i) × 100 + Σ penalidades
score = clamp(score, 0, 100)
```

Os 6 componentes medem coisas distintas e **nenhum deles é um LLM**:

| Componente | Sinal medido |
|---|---|
| reaction_intensity | densidade de palavras de reação PT-BR + energia de áudio |
| surprise_and_novelty | picos sonoros + marcadores de surpresa + movimento alto |
| narrative_completion | janela começa/termina em fronteira de sentença, duração no alvo |
| event_clarity | densidade de palavras/seg (nem silêncio nem tumulto) |
| editing_potential | existência de peaks para SFX + variação de motion |
| audiovisual_quality | inverso de clipping + RMS saudável |

Penalidades (também do YAML, não hardcoded): `lacks_context -30`,
`poor_audio -25`, `passive_gameplay_cutscene -40`, `excessive_repetition -20`.

**NMS temporal:** candidatos com sobreposição IoU alta acima de `top_k` são
descartados — senão o top-10 seria 10 recortes do mesmo momento.

---

## Etapa 5 — Edit plan

Contrato `EditPlan` (em `core/schemas.py`). Os campos que o Remotion consome:

```json
{
  "job_id": "job_001",
  "composition_template": "GTASplitScreen",
  "candidate_id": "cand_003",
  "source_video_path": "/abs/path/video.mp4",
  "output_duration_seconds": 22.4,
  "crop_mode": "split_screen",
  "words": [{"word": "caralho", "start": 142.3, "end": 142.9, "score": 0.94, "speaker": "S0"}],
  "sfx_triggers": [{"t": 1.8, "type": "impact", "intensity": 0.91}],
  "background_music_track": "assets/nocopyright_music/hype_drive.wav"
}
```

`words[].start` é **absoluto no vídeo fonte**. O componente Remotion subtrai o
início do corte (`candidate.start_time`) para posicionar legendas. SFX usam
`t` **relativo ao corte**, em segundos.

---

## Etapa 6 — Remotion

Duas composições, ambas 1080x1920 @ 30 fps:

- **`GTASplitScreen`** — gameplay em cima, facecam/letterbox embaixo, legendas karaokê.
- **`GTACinematic`** — vídeo centralizado com barras pretas, legendas.

Duração é dinâmica via `calculateMetadata`: `output_duration_seconds × framerate`.

```bash
cd remotion && npx remotion compositions     # lista as composições
cd remotion && npx remotion studio           # preview interativo
```

---

## Etapa 7 — QC

`QCReport` verifica:

| Campo | Como |
|---|---|
| `aspect_ratio_valid` | ffprobe: 1080x1920 |
| `audio_clipping_detected` | ffmpeg `astats` → peak level |
| `black_frames_detected` | ffmpeg `blackdetect` |
| `rights_check_passed` | re-checa rights.json |
| `passed` | todos os anteriores OK |

QC reprovado **não apaga** o vídeo — marca `passed: false` com `failure_reasons`
para revisão humana. Decidir sozinho jogar fora um render de 10 minutos é pior que
entregar com aviso.

---

## Estendendo

### Adicionar uma categoria viral

1. `CategoryEnum` em `core/schemas.py`.
2. Keywords em `viral_detector.classify_category()`.
3. Mapeamento de trilha em `assets/nocopyright_music/catalog.json → category_map`.

### Adicionar um SFX

1. Gere o WAV em `assets/sound_effects/`.
2. Registre em `assets/sound_effects/catalog.json`.
3. Use o tipo em `viral_detector.build_edit_plan()` ao montar `sfx_triggers`.

### Trocar o modelo de transcrição

`config/pipeline_config.yaml → transcription.model`. Modelos grandes exigem GPU
com VRAM suficiente; sem isso o módulo cai para CPU automaticamente e fica mais lento,
mas continua correto.

---

## Depuração

```bash
# só a trava jurídica
.venv/bin/python -m pytest tests/test_gatekeeper.py -v

# só o scoring, mostrando por que falhou
.venv/bin/python -m pytest tests/test_modules.py -k scoring -v

# rodar uma etapa isolada
.venv/bin/python -c "
from core.vision_tracker import analyze
import json; print(json.dumps(analyze('assets/source_videos/demo_source.mp4'), indent=2)[:800])"
```

### Falhas comuns

| Sintoma | Causa provável |
|---|---|
| exit code 3 | `rights.json` não está APPROVED + commercial_permission true |
| `transcript.cuda_oom` | VRAM insuficiente → baixe `transcription.model` |
| `recommended_crop_mode` sempre `gameplay_centered` | nenhum rosto detectado — normal em gravação sem facecam |
| render falha com "composition not found" | rode `npm install` em `remotion/` |
| candidatos com score muito baixo | áudio limpo demais ou cutscene passiva (penalidade -40) |
| `transcript sem palavras` / `detector não retornou nenhum candidato` | o vídeo não tem fala transcrevível (tom, silêncio, só música). É falha honesta, não bug |
| `TypeError: metadata_errors` ao importar faster-whisper | PyAV ≥17 instalado → `uv pip install "av>=14,<17"` |
| `backend=none`, zero rostos | `assets/models/blaze_face_short_range.tflite` ausente |

### Armadilhas de ambiente (verificadas nesta máquina)

**PyAV.** `faster-whisper 1.2.1` importa quebrado com `av>=17`. O
`requirements.txt` pina `av>=14,<17`. O `whisper_analyzer` decodifica o WAV com a
stdlib (`wave`) e não usa o decoder do PyAV, mas o import do pacote ainda exige
a versão compatível.

**CUDA int8.** O host tem CUDA 13.4; `ctranslate2 4.8.5` espera SONAMEs `.so.12`.
`int8_float16` na GPU falha com `CUBLAS_STATUS_NOT_SUPPORTED` — é incompatibilidade
de ABI, não de `LD_LIBRARY_PATH`. A cadeia de fallback é:
`int8_float16 → float16 → int8 → CPU`. Na GTX 1650 o `float16` funciona.

**Haar cascade.** OpenCV 5.x não distribui os XMLs (`cv2/data/` só tem
`__init__.py`). O `vision_tracker` usa MediaPipe `mp.tasks.vision.FaceDetector`
como backend principal — `mp.solutions` foi removido no MediaPipe 1.2. O fallback
Haar é código vivo, mas só roda se o XML for vendorizado em `assets/models/`.

**Remotion e vídeo local.** `OffthreadVideo` não lê caminho absoluto nem
`file://` — um `/` inicial resolve contra o **diretório do bundle**, gerando 404.
Todo arquivo tem de estar em `remotion/public/`. `main.py` faz isso sozinho em
`stage_source_for_remotion()` (hardlink com nome determinístico por job); quem
renderiza na mão precisa repetir o staging.

