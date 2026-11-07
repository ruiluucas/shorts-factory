# Shorts Factory

**Transforma vídeos longos em shorts virais verticais — com trava jurídica que se recusa a renderizar conteúdo sem permissão.**

Pipeline de 7 etapas que assiste horas de gravação, encontra os 20 segundos que fazem alguém parar de rolar, e entrega o MP4 1080x1920 legendado. Sem LLM no meio: cada decisão vem de sinal medido — energia de áudio, densidade de reação na fala, movimento de quadro, presença de rosto.

Funciona com **qualquer vídeo**: gameplay, podcast, live, aula, vlog, stream. O que adapta o pipeline ao seu conteúdo é o `config/pipeline_config.yaml`.

> `[REJECTED_RIGHTS]` — o pipeline aborta com exit code 3 antes de transcrever um único segundo quando `rights.json` não está explicitamente aprovado. **Não existe flag para burlar.**

```
 vídeo longo (20–40 min)
        │
        ├─ [1] GATEKEEPER ......... rights.json == APPROVED + commercial_permission?
        │                              └── ❌ exit 3 → REJECTED_RIGHTS (nada roda)
        │                              └── ✅
        ├─ [2] TRANSCRIÇÃO ........ faster-whisper word-level PT-BR + picos RMS/clipping (librosa)
        ├─ [3] VISÃO .............. detector de rosto + optical flow → facecam, cena passiva
        ├─ [4] SCORING ............ 6 componentes ponderadas + 4 penalidades → nota 0–100
        ├─ [5] EDIT PLAN .......... JSON com palavras, SFX relativos, crop, trilha
        ├─ [6] RENDER ............. Remotion → MP4 1080x1920 @ 30fps h264
        └─ [7] QC ................. resolução, clipping, frames pretos, áudio, re-check de direitos
```

---

## Branches

| Branch | Conteúdo |
|---|---|
| **`main`** | Núcleo genérico. Composições `ShortSplitScreen` e `ShortCinematic`, watermark `SHORTS`, config neutra. |
| **`gta`** | Customização GTA: composições `GTASplitScreen` / `GTACinematic`, branding e ajustes de conteúdo específicos. |

Quer um perfil próprio (Minecraft, Valorant, podcast)? Use a branch `gta` como modelo:

```bash
git checkout gta -b meu-perfil
```

---

## Por que não é só "cortar e legendar"

O scoring não é caixa preta. Cada componente mede uma coisa específica, e os pesos vivem em `config/pipeline_config.yaml`:

| Componente | Sinal medido | Peso |
|---|---|---|
| `reaction_intensity` | densidade de "caralho / olha isso / meu deus" + energia de áudio | 0.25 |
| `surprise_and_novelty` | picos sonoros + marcadores de surpresa + movimento visual | 0.20 |
| `narrative_completion` | abre/fecha em fronteira de sentença, duração no alvo | 0.20 |
| `event_clarity` | densidade de fala (nem silêncio nem tumulto) | 0.15 |
| `editing_potential` | peaks disponíveis para SFX + variação de motion | 0.10 |
| `audiovisual_quality` | inverso de clipping + RMS saudável | 0.10 |

E penalidades que doem de verdade — medidas, não decorativas:

| Penalidade | Peso | Quando |
|---|---|---|
| `passive_gameplay_cutscene` | **−40** | cena parada, sem rosto em quadro |
| `lacks_context` | −30 | janela começa no meio de uma frase |
| `poor_audio` | −25 | clipping alto ou áudio quase mudo |
| `excessive_repetition` | −20 | mesmas palavras em loop |

Num teste real, um candidato com base **82.9** caiu para **42.9** por uma única penalidade de cena passiva.

---

## Instalação

```bash
git clone https://github.com/ruiluucas/shorts-factory.git
cd shorts-factory

# Python 3.12 obrigatório (faster-whisper não tem wheel estável no 3.14)
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

cd remotion && npm install && cd ..
```

**Requisitos:** ffmpeg · Node 18+ · Python 3.12 · GPU NVIDIA opcional (testado em GTX 1650 4 GB, com fallback automático para CPU).

---

## Uso

### 1. Declarar os direitos (obrigatório)

```bash
.venv/bin/python main.py init-job \
  --job-id meu_job \
  --video assets/source_videos/entrada.mp4 \
  --creator "@canal_original" \
  --evidence "/caminho/da/permissao.png"
```

Sem `--approved` o status nasce `PENDING` — e o pipeline **não roda**.

### 2. Rodar

```bash
.venv/bin/python main.py run --video assets/source_videos/entrada.mp4 --job-id meu_job
.venv/bin/python main.py run --help     # todas as flags
```

| Flag | Efeito |
|---|---|
| `--top-k N` | quantos candidatos avaliar (default 10) |
| `--no-render` | para antes do Remotion, gera só os planos |
| `--dry-run` | valida o job sem as etapas pesadas |

Exit codes: `0` sucesso · `3` REJECTED_RIGHTS · `1` falha. Todo job termina com `job_status.json` auditável.

### 3. Preview

```bash
cd remotion && npx remotion studio
```

---

## Saídas

Cada job deixa uma trilha completa em `workspace/jobs/{job_id}/`:

| Arquivo | O que contém |
|---|---|
| `metadata.json` | sha256 do vídeo, duração, timestamps |
| `rights.json` | direitos + resultado da validação |
| `transcript.json` | palavras word-level + eventos de áudio |
| `vision.json` | amostras de movimento/rosto, crop recomendado |
| `candidates.json` | top-K `ViralCandidate` ordenados por score |
| `edit_plan_{id}.json` | plano de edição para o Remotion |
| `qc.json` | `QCReport` |
| `job_status.json` | status final + duração de cada etapa |

---

## Travas de segurança

1. **Jurídica.** `rights.json` precisa de `status: "APPROVED"` **e** `commercial_permission: true`. Testado contra 6 tentativas de bypass (status trocado, permissão falsa, evidência vencida) — todas exit 3. O campo `overridable_by_cli` fica registrado como `false`.
2. **Áudio.** Nenhuma faixa comercial. A biblioteca local só aceita arquivos do `catalog.json`, e todas as trilhas e SFX do repo são **sintetizados com ffmpeg** — zero sample de música protegida.
3. **Conteúdo.** Cena passiva, áudio ruim, falta de contexto e repetição são penalizados pelo config, não por código escondido.

---

## Estrutura

```
shorts-factory/
├── config/pipeline_config.yaml    # pesos, penalidades, política de áudio
├── core/
│   ├── schemas.py                 # contratos Pydantic (fonte única de verdade)
│   ├── whisper_analyzer.py        # transcrição + eventos de áudio
│   ├── vision_tracker.py          # facecam, movimento, cena passiva
│   └── viral_detector.py          # scoring, categorias, edit plans
├── remotion/src/compositions/     # ShortSplitScreen, ShortCinematic
├── assets/                        # SFX e trilhas sintéticas + modelo de rosto
├── tests/                         # 17 testes; 5 só da trava jurídica
├── docs/PIPELINE.md               # contratos por etapa + armadilhas de ambiente
└── main.py                        # orquestrador CLI
```

---

## Testes

```bash
.venv/bin/python -m pytest tests/ -q
# 17 passed

.venv/bin/python -m pytest tests/test_gatekeeper.py -v    # só a trava jurídica
.venv/bin/python -m pytest tests/test_modules.py -k scoring -v
```

Os testes usam `GSF_WORKSPACE_ROOT` para isolar o workspace — nenhum job real é tocado.

---

## Status e limitações honestas

**Validado de ponta a ponta** com narração PT-BR real: MP4 1080x1920 h264, QC `passed: true`, legenda renderizada, `[COMPLETED]` exit 0.

O que ainda **não** está provado:

- **Footage real de jogo** ainda não passou pelo pipeline. Os testes usam vídeo sintético + TTS. O scoring respondeu corretamente, mas comportamento em gravação real é teoria até alguém rodar.
- **VRAM.** A GTX 1650 4 GB não sustenta `large-v3`; o módulo rebaixa para `small` automaticamente e loga o motivo. `float16` funciona; `int8_float16` falha por ABI CUDA e cai para CPU.
- **Diarização** exige pyannote, não instalado — `speaker: null` com warning.
- **Sem rosto em quadro** a visão classifica tudo como `gameplay_centered`. Esperado, não bug.

Ver `docs/PIPELINE.md` para as armadilhas de ambiente (PyAV, CUDA, detector de rosto, staging do Remotion).

---

## Licença e uso responsável

Código sob MIT. **O uso do conteúdo é responsabilidade de quem opera.** O pipeline foi construído em torno da premissa de que só se processa material com permissão explícita do criador original — a trava existe justamente para tornar difícil fazer o contrário.
