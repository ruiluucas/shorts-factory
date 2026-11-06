# GTA Shorts Factory

Pipeline autônomo que transforma vídeos longos de GTA VI em shorts verticais (1080x1920 @ 30fps) prontos para publicar, com detecção de momentos virais e **trava jurídica obrigatória**.

## O que faz

```
vídeo longo
   ↓
[1] Gatekeeper de direitos  ──❌──► REJECTED_RIGHTS (aborta)
   ↓ ✅
[2] Transcrição word-level PT-BR (faster-whisper) + picos de áudio (librosa)
[3] Análise visual (OpenCV): facecam, movimento, cutscene passiva
[4] Scoring viral 0-100 (6 dimensões ponderadas + penalidades)
[5] Edit plan JSON → componentes Remotion
[6] Render MP4 vertical
[7] QC automatizado (resolução, clipping, frames pretos, áudio)
```

## Requisitos

- **Python 3.12** (não use 3.14 — faster-whisper/torch não têm wheel estável)
- **Node 18+**
- **ffmpeg** no PATH
- GPU NVIDIA opcional (testado em GTX 1650 4GB — usa `small`/`int8_float16` por causa da VRAM; fallback automático para CPU)

## Instalação

```bash
cd gta-shorts-factory
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

cd remotion && npm install && cd ..
```

## Uso

### 1. Criar o job e declarar os direitos

```bash
.venv/bin/python main.py init-job \
  --job-id job_001 \
  --video assets/source_videos/entrada.mp4 \
  --creator "@canal_original" \
  --evidence "/caminho/permissao.png"
```

Isso cria `workspace/jobs/job_001/rights.json` com status **PENDING**.
O pipeline **não roda** até que `status` seja `APPROVED` **e** `commercial_permission` seja `true`.

### 2. Rodar o pipeline

```bash
.venv/bin/python main.py run --video assets/source_videos/entrada.mp4 --job-id job_001
```

Opções úteis:

| Flag | Efeito |
|---|---|
| `--top-k N` | quantos candidatos virais avaliar (default 10) |
| `--no-render` | para antes do Remotion (gera só os planos) |
| `--dry-run` | valida o job sem executar as etapas pesadas |

Exit codes: `0` sucesso · `3` REJECTED_RIGHTS · `1` falha.

### 3. Preview no Remotion Studio

```bash
cd remotion && npx remotion studio
```

## Estrutura

```
gta-shorts-factory/
├── config/pipeline_config.yaml   # pesos, penalidades, política de áudio, crop modes
├── core/
│   ├── schemas.py                # contratos Pydantic (fonte única de verdade)
│   ├── whisper_analyzer.py       # transcrição + eventos de áudio
│   ├── vision_tracker.py         # facecam, movimento, cena passiva
│   └── viral_detector.py         # scoring, categorias, edit plans
├── remotion/
│   └── src/compositions/         # GTASplitScreen, GTACinematic
├── workspace/jobs/{job_id}/      # metadata, rights, transcript, vision,
│                                 # candidates, edit_plan, qc, job_status
├── assets/
│   ├── sound_effects/            # SFX (livre de Content ID)
│   ├── nocopyright_music/        # trilhas NoCopyright APENAS
│   └── source_videos/
└── main.py                       # orquestrador CLI
```

## Travas invioláveis

1. **Jurídica** — `rights.json` precisa de `status: "APPROVED"` + `commercial_permission: true`. Qualquer outro estado aborta em `REJECTED_RIGHTS` antes de tocar em transcrição ou render.
2. **Áudio** — nenhuma faixa comercial. Só `assets/nocopyright_music/` e `assets/sound_effects/`. O scorer não penaliza por isso, o render não recebe trilha comercial: a política é aplicada no `edit_plan` (`background_music_track` só aponta para a biblioteca local).
3. **Conteúdo** — cutscenes passivas recebem `-40`; falta de contexto `-30`; áudio ruim `-25`; repetição `-20`.

## Scoring

Nota final = `Σ (componente × peso) × 100 + penalidades`, limitada a 0–100.

| Componente | Peso |
|---|---|
| reaction_intensity | 0.25 |
| surprise_and_novelty | 0.20 |
| narrative_completion | 0.20 |
| event_clarity | 0.15 |
| editing_potential | 0.10 |
| audiovisual_quality | 0.10 |

Ajuste em `config/pipeline_config.yaml` — os módulos leem daí, não do código.

## Saídas por job

| Arquivo | Conteúdo |
|---|---|
| `metadata.json` | job_id, sha256 do vídeo, duração, timestamps |
| `rights.json` | RightsMetadata (entrada + validação) |
| `transcript.json` | palavras word-level + eventos de áudio |
| `vision.json` | amostras de movimento/rosto, crop recomendado |
| `candidates.json` | top-K ViralCandidate ordenados por score |
| `edit_plan_{id}.json` | EditPlan específico por candidato |
| `qc.json` | QCReport |
| `job_status.json` | status final + duração de cada etapa |
