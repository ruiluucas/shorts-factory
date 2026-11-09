# Shorts Factory

**Transforma vídeos longos em shorts virais verticais 1080x1920 — com trava jurídica que se recusa a renderizar conteúdo sem permissão.**

O pipeline assiste horas de gravação, encontra os 20 segundos que fazem alguém parar de rolar, e entrega o MP4 legendado em PT-BR. Sem LLM no meio: cada decisão vem de sinal medido — energia de áudio, densidade de reação na fala, movimento de quadro, presença de rosto.

Funciona com **qualquer vídeo**: gameplay, live, podcast, aula, vlog. O que adapta o pipeline ao seu conteúdo é o `config/pipeline_config.yaml`.

> `[REJECTED_RIGHTS]` — o pipeline aborta com exit code 3 antes de transcrever um único segundo quando `rights.json` não está explicitamente aprovado. **Não existe flag para burlar.**

```
 vídeo longo (20–40 min)
        │
        ├─ [1] GATEKEEPER ......... rights.json == APPROVED + commercial_permission?
        │                              └── ❌ exit 3 → REJECTED_RIGHTS (nada roda)
        │                              └── ✅
        ├─ [2] TRANSCRIÇÃO ........ faster-whisper word-level PT-BR + picos RMS/clipping
        ├─ [3] VISÃO .............. detector de rosto + optical flow → facecam, cena passiva
        ├─ [4] SCORING ............ 6 componentes ponderadas + 4 penalidades → nota 0–100
        ├─ [5] EDIT PLAN .......... palavras, SFX, B-roll, cortes de silêncio, punch-ins
        ├─ [6] RENDER ............. Remotion → MP4 1080x1920 @ 30fps h264
        └─ [7] QC ................. resolução, clipping, frames pretos, áudio, re-check de direitos
```

---

## Formatos de saída

Três composições, todas 1080x1920 @ 30 fps:

| Composição | Layout | Quando usar |
|---|---|---|
| **`StreamerSplit`** | narrador em cima (45%) + gameplay embaixo (55%), divididos por linha branca | o formato "gameplay commentary": facecam + jogo no mesmo frame |
| **`ShortSplitScreen`** | gameplay grande + banda de facecam | vídeo com facecam pequena no canto |
| **`ShortCinematic`** | vídeo centralizado com barras pretas | vídeo sem facecam |

Ligue o formato streamer no YAML:

```yaml
streamer_split:
  enabled: true
  narrator_split: 0.45   # 45% narrador / 55% gameplay
```

As duas bandas vêm do **mesmo vídeo de origem**, em recortes distintos: a de cima enquadra `center 12%` (facecam), a de baixo `center 62%` (ação). O áudio toca **só na banda de cima** — duas trilhas iguais geram eco.

---

## Edição orgânica

O que separa "corte mecânico" de edição humana é sobretudo o que é **removido**. Três recursos, todos configuráveis e desligados por padrão:

### Corte de silêncio

Remove pausas longas usando os timestamps word-level. Nunca corta no meio de uma palavra — só age entre o fim de uma e o início da seguinte, preservando 150 ms de respiro para não soar artificial.

```yaml
organic_edit:
  silence:
    min_pause_ms: 700      # abaixo disso é respiração, não silêncio
    keep_pause_ms: 150
    max_removed_ratio: 0.35  # nunca remove mais que 35% do trecho
```

### Ritmo

Punch-in suave nas palavras de ênfase, no máximo um por 2 s. Reconhece bigramas ("meu deus", "não acredito"), que palavra a palavra passariam despercebidos.

### B-roll

Imagens ilustrando o que está sendo falado, com fade in/out, no topo (longe da legenda), uma por vez.

```yaml
organic_edit:
  broll:
    enabled: true
    allow_network: true    # busca no Openverse; senão, só pasta local
```

**Licença é trava, não sugestão.** Duas fontes, nesta ordem:

1. **`assets/broll/<assunto>/`** — seu acervo, vence sempre. Jogue a imagem em `assets/broll/carro/` e ela é usada quando a fala mencionar carro.
2. **Openverse** (Creative Commons) — só quando `allow_network: true`.

Só entram licenças `by`, `by-sa`, `cc0`, `pdm`, `zero`. **`by-nc` fica de fora** mesmo sendo Creative Commons, porque mata a monetização. Cada imagem carrega `license` e `attribution` no `edit_plan`, então a proveniência fica auditável.

Google Imagens e Pexels sem chave estão fora de propósito: um não informa licença, o outro exige chave.

---

## Scoring

Não é caixa preta. Cada componente mede uma coisa específica, e os pesos vivem em `config/pipeline_config.yaml`:

| Componente | Sinal medido | Peso |
|---|---|---|
| `reaction_intensity` | densidade de "caralho / olha isso / meu deus" + energia de áudio | 0.25 |
| `surprise_and_novelty` | picos sonoros + marcadores de surpresa + movimento visual | 0.20 |
| `narrative_completion` | abre/fecha em fronteira de sentença, duração no alvo | 0.20 |
| `event_clarity` | densidade de fala (nem silêncio nem tumulto) | 0.15 |
| `editing_potential` | peaks disponíveis para SFX + variação de motion | 0.10 |
| `audiovisual_quality` | inverso de clipping + RMS saudável | 0.10 |

Penalidades medidas, não decorativas:

| Penalidade | Peso | Quando |
|---|---|---|
| `passive_gameplay_cutscene` | **−40** | cena parada, sem rosto em quadro |
| `lacks_context` | −30 | janela começa no meio de uma frase |
| `poor_audio` | −25 | clipping alto ou áudio quase mudo |
| `excessive_repetition` | −20 | mesmas palavras em loop |

Num teste real, um candidato com base **82.9** caiu para **42.9** por uma única penalidade de cena passiva.

Categorias detectadas: `FUNNY`, `SURPRISE`, `FAIL`, `CHAOS`, `REACTION`, `DISCOVERY`, `TENSION`.

---

## Instalação

```bash
git clone https://github.com/ruiluucas/shorts-factory.git
cd shorts-factory
make setup
```

Ou manualmente:

```bash
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

### 3. Preview no Studio

```bash
cd remotion && npx remotion studio
```

---

## Comandos

```bash
make setup      # venv 3.12 + deps Python + node_modules
make test       # suíte completa (65 testes)
make lint       # ruff check + ruff format --check
make lint-fix   # aplica correções seguras
make fixtures   # gera os vídeos sintéticos de teste (ffmpeg + TTS)
make run-demo   # pipeline completo no vídeo de demonstração
make clean      # remove caches (mantém o venv)
```

`make eval` está reservado para o harness de avaliação — **ainda não implementado**; o alvo falha com mensagem explícita em vez de traceback.

---

## Saídas por job

Cada job deixa uma trilha completa em `workspace/jobs/{job_id}/`:

| Arquivo | O que contém |
|---|---|
| `metadata.json` | sha256 do vídeo, duração, timestamps |
| `rights.json` | direitos + resultado da validação |
| `transcript.json` | palavras word-level + eventos de áudio |
| `vision.json` | amostras de movimento/rosto, crop recomendado |
| `candidates.json` | top-K `ViralCandidate` ordenados por score |
| `edit_plan_{id}.json` | plano completo: palavras, SFX, B-roll, silêncios, punch-ins |
| `qc.json` | `QCReport` |
| `job_status.json` | status final + duração de cada etapa |

---

## Travas de segurança

1. **Jurídica.** `rights.json` precisa de `status: "APPROVED"` **e** `commercial_permission: true`. Testado contra 6 tentativas de bypass (status trocado, permissão falsa, evidência vencida) — todas exit 3. O campo `overridable_by_cli` fica registrado como `false`.
2. **Áudio.** Nenhuma faixa comercial. A biblioteca local só aceita arquivos do `catalog.json`, e todas as trilhas e SFX do repo são **sintetizados com ffmpeg** — zero sample de música protegida.
3. **B-roll.** Só imagem com licença comercial comprovada. Sem licença no `edit_plan`, a imagem não entra.
4. **Conteúdo.** Cena passiva, áudio ruim, falta de contexto e repetição são penalizados pelo config, não por código escondido.

---

## Estrutura

```
shorts-factory/
├── config/pipeline_config.yaml    # pesos, penalidades, edição, formatos
├── core/
│   ├── schemas.py                 # contratos Pydantic (fonte única de verdade)
│   ├── whisper_analyzer.py        # transcrição + eventos de áudio
│   ├── vision_tracker.py          # facecam, movimento, cena passiva
│   ├── viral_detector.py          # scoring, categorias, edit plans
│   ├── organic_edit.py            # corte de silêncio + ritmo
│   ├── broll.py                   # imagens ilustrando a fala (com licença)
│   └── decision_config.py         # validação do bloco `decision:` no startup
├── remotion/src/
│   ├── compositions/              # StreamerSplit, ShortSplitScreen, ShortCinematic
│   └── components/                # Captions, BrollOverlays, SfxBadges, SourceVideo
├── assets/
│   ├── sound_effects/             # 5 SFX sintéticos + catalog.json
│   ├── nocopyright_music/         # 4 trilhas sintéticas + catalog.json
│   ├── broll/                     # seu acervo de imagens (por assunto)
│   └── models/                    # modelo de detecção facial
├── tests/                         # 65 testes; 5 só da trava jurídica
├── docs/
│   ├── PIPELINE.md                # contratos por etapa + armadilhas de ambiente
│   ├── DECISIONS.md               # decisões de design e o porquê
│   └── JEV_NOTES.md               # pesquisa da API de decisão (com fonte)
├── AGENTS.md                      # convenções para agentes
└── main.py                        # orquestrador CLI
```

---

## Decisão com Jev (opcional, desligado)

O pipeline tem uma camada de decisão estruturada que usa o **Jev** (TypeSafe System One) — e funciona **100% offline sem ela**.

**O que o Jev faz:** recebe texto e perguntas tipadas (`choice`, `score`, `noul`, `box`) e devolve valores estruturados com probabilidade. Pontua, classifica, escolhe.

**O que ele NÃO faz:** não gera texto. Não escreve legenda, título nem resumo. Legenda continua vindo do Whisper.

**Privacidade:** nenhum texto de transcrição sai da máquina sem `decision.allow_remote: true`. Backend remoto com `allow_remote: false` é **erro de validação** — o pipeline não sobe.

```yaml
decision:
  backend: "heuristic"      # offline, sem rede
  allow_remote: false
  uses:
    semantic_scoring: false
    hook_selection: false
    publish_gate: false
    visual_localization: false
    video_grounding: false
```

Medido com a API real (2026-11-07):

| Uso | Resultado |
|---|---|
| Publish gate — texto sujo | profanity 0.94, violence 0.92, financial 0.89, **block 0.84** |
| Publish gate — texto limpo | flags <0.11, **publish 0.98** |
| Scoring semântico | hook 1.96/2 (conf 0.96), **0.23 s** |
| Localização visual (imagem) | **não confiável** — errou círculo óbvio, conf 0.03 |

O uso de imagem foi desligado por medição, não por precaução. Detalhes com fonte em [`docs/JEV_NOTES.md`](docs/JEV_NOTES.md).

---

## Testes

```bash
make test
# 65 passed

.venv/bin/python -m pytest tests/test_gatekeeper.py -v    # só a trava jurídica
.venv/bin/python -m pytest tests/test_organic_edit.py -v  # silêncio, ritmo, B-roll
.venv/bin/python -m pytest tests/test_decision_schema.py -v  # contratos + config
```

Os testes usam `GSF_WORKSPACE_ROOT` para isolar o workspace — nenhum job real é tocado. Nenhum teste depende de rede ou de chave de API.

---

## Status e limitações honestas

**Validado de ponta a ponta** com vídeo real: MP4 1080x1920 h264, QC `passed: true`, legendas renderizadas, B-roll aparecendo e saindo no tempo certo, `[COMPLETED]` exit 0.

O que **não** está provado:

- **Gravação real de jogo ou live** ainda não passou pelo pipeline. As fixtures são sintéticas (`testsrc2` + TTS). O comportamento em footage real — com ruído, música de fundo e sotaque — é teoria.
- **O recorte `center 12%` assume facecam no terço superior.** Se a sua live tem facecam no canto inferior, o enquadramento precisa mudar em `StreamerSplit.tsx`.
- **Download real do Openverse dentro de um render** não foi exercitado — só a resposta da API.
- **Corte de silêncio com fala natural** não testado: o TTS fala sem pausa, então os vídeos de teste não têm silêncio para cortar.
- **B-roll + StreamerSplit juntos** compõem tecnicamente, mas não rodaram no mesmo render.
- **VRAM.** A GTX 1650 4 GB não sustenta `large-v3`; o módulo rebaixa para `small` e loga o motivo. `float16` funciona; `int8_float16` falha por ABI CUDA e cai para CPU.
- **Diarização** exige pyannote, não instalado — `speaker: null` com warning.
- **VideoJev** não cabe em 4 GB (4B em BF16 = ~8 GB). Só existe como adaptador para endpoint externo, desligado.

Ver [`docs/DECISIONS.md`](docs/DECISIONS.md) para o porquê de cada escolha e [`docs/PIPELINE.md`](docs/PIPELINE.md) para as armadilhas de ambiente.

---

## Licença e uso responsável

Código sob MIT. **O uso do conteúdo é responsabilidade de quem opera.**

O pipeline foi construído em torno da premissa de que só se processa material com permissão explícita do criador original — a trava jurídica existe justamente para tornar difícil fazer o contrário.

Cortar conteúdo de terceiros com edição transformativa (legenda, B-roll, cortes) reduz risco mas **não elimina**: o Content ID do YouTube bloqueia automaticamente e você recorre depois. Trechos curtos, comentário próprio e nunca publicação em massa são o que sustenta um canal assim.
