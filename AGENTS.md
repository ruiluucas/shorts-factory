# AGENTS.md — convenções de trabalho neste repositório

Guia para agentes (humanos ou LLM) que forem modificar o Shorts Factory.

---

## Comandos

```bash
make setup      # venv 3.12 + deps Python + node_modules
make test       # suíte completa (17 testes hoje)
make lint       # ruff check + ruff format --check
make lint-fix   # aplica correções seguras
make fixtures   # gera os vídeos sintéticos de teste (ffmpeg + TTS)
make eval       # harness de avaliação sobre as fixtures
make run-demo   # pipeline completo no vídeo de demonstração
make clean      # remove caches (mantém o venv)
```

Sem `make`, os equivalentes diretos:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python main.py run --video X --job-id Y
```

---

## Estrutura

```
config/pipeline_config.yaml   # pesos, penalidades, política de áudio, crop modes
core/schemas.py               # contratos Pydantic — fonte única de verdade
core/whisper_analyzer.py      # transcrição word-level + eventos de áudio
core/vision_tracker.py        # facecam, movimento, cena passiva
core/viral_detector.py        # scoring, categorias, edit plans
remotion/src/compositions/    # ShortSplitScreen, ShortCinematic
tests/                        # suíte; test_gatekeeper.py é intocável
tests/fixtures/               # gerador de vídeos sintéticos (binários não versionados)
eval/                         # harness de avaliação (golden, métricas, comparação)
docs/PIPELINE.md              # contratos por etapa + armadilhas de ambiente
docs/DECISIONS.md             # decisões de design e o porquê
main.py                       # orquestrador CLI
```

---

## Regras invioláveis

1. **O gatekeeper não se toca.** `rights.json` precisa de `status: "APPROVED"` e
   `commercial_permission: true`. Exit code 3 em qualquer outro estado. Os 5 testes de
   `tests/test_gatekeeper.py` devem continuar passando. Não existe flag de bypass,
   não crie uma.

2. **Comportamento novo é configurável.** Tudo que muda o resultado de um corte ou
   de uma nota vive em `config/pipeline_config.yaml` e tem fallback determinístico.
   O pipeline roda 100% offline: sem API key, sem rede, sem GPU.

3. **Contratos no `schemas.py`.** Nada de `dict` solto atravessando etapas.
   Entrada e saída de cada estágio passa por modelo Pydantic.

4. **Nada de mídia comercial.** Só `assets/sound_effects/` e
   `assets/nocopyright_music/`, ambos sintetizados por script (`build_sfx.sh`,
   `build_music.sh`). Zero sample de faixa protegida.

5. **VRAM ≤ 4 GB.** Nenhum modelo que não caiba na GTX 1650. Sempre com fallback
   para CPU, e o fallback precisa ser registrado no log.

6. **Commits atômicos** em Conventional Commits, em PT-BR:
   `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.

7. **Não invente resultado.** Se não rodou, diga que não rodou. Se rodou e falhou,
   mostre o erro real.

---

## Armadilhas conhecidas

Detalhamento completo em `docs/PIPELINE.md` (seção "Armadilhas de ambiente").

| Sintoma | Causa |
|---|---|
| `exit 3` sem mensagem clara | `rights.json` não está APPROVED + `commercial_permission: true` |
| `TypeError: metadata_errors` | PyAV ≥ 17 instalado; pin é `av>=14,<17` |
| `CUBLAS_STATUS_NOT_SUPPORTED` | CUDA 13.4 vs SONAMEs `.so.12` do ctranslate2 → cai para CPU |
| `backend=none`, zero rostos | falta `assets/models/blaze_face_short_range.tflite` |
| `transcript sem palavras` | o vídeo não tem fala transcrevível — falha honesta, não bug |
| render 404 no Remotion | `OffthreadVideo` não lê path absoluto; precisa estar em `remotion/public/` |
| `.gitignore` engolindo asset | padrão sem `/` inicial casa em qualquer profundidade |
| `JobLogger.info()` quebra com `%s` | a classe **não** interpola argumentos; use f-string |

### Sobre logs

`main.py` usa a classe `JobLogger` (definida no próprio arquivo), **não** o módulo
`logging`. A assinatura é `info(message: str, stage: str | None = None, **extra)` e
ela não faz interpolação de `%s` — f-string é o único caminho. Por isso a regra
`G004` do ruff é ignorada apenas para `main.py` em `pyproject.toml`.

---

## Como testar uma mudança no scoring

```bash
make fixtures          # uma vez
make eval              # linha de base
# ... altera config/pipeline_config.yaml ou core/viral_detector.py ...
make eval              # compara com a linha de base
```

`eval/run_eval.py` grava `eval/reports/{timestamp}.json`; `eval/compare.py` mostra
os deltas e sai com código 1 se uma métrica-chave regredir além do limiar.

Se o `eval` não rodar (fixtures ausentes, ffmpeg faltando), o honesto é dizer
"não rodou" — não deduzir que a mudança melhorou.

---

## Versões

- Python **3.12** (não 3.13/3.14: faster-whisper e torch não têm wheel estável)
- Node 18+
- ffmpeg no PATH
