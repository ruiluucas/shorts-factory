# Decisões de design

Registro do que foi escolhido, e por quê. Cada entrada é uma decisão que alguém
pode querer reverter — se reverter, atualize esta página.

---

## D-001 — Python 3.12, não 3.14

**Contexto.** A máquina tem Python 3.14 como padrão do sistema.

**Decisão.** Usar um venv com Python 3.12 (`uv venv --python 3.12`).

**Motivo.** `faster-whisper` depende de `ctranslate2`/`av`, que ainda não publicam
wheel estável para 3.14. Tentar 3.14 força compilação do zero e quebra em tempo de
import. O `pyproject.toml` declara `requires-python = ">=3.12,<3.13"`.

**Reversível?** Sim, quando as wheels saírem. O código não usa nada exclusivo de 3.12
exceto `StrEnum` (3.11+) e sintaxe `X | None` (3.10+).

---

## D-002 — `StrEnum` em vez de `class X(str, Enum)`

**Contexto.** O ruff (`UP042`) sinaliza que `str, Enum` é redundante no Python 3.11+.

**Decisão.** Migrar `RightsStatus` e `CategoryEnum` para `StrEnum`.

**Motivo.** `str()` agora retorna `"APPROVED"` em vez de `"RightsStatus.APPROVED"`.
Verificado que comparação com string e serialização JSON continuam idênticas — é
correção, não mudança de contrato.

**Cuidado.** `StrEnum` não é `auto()`: os valores continuam explícitos.

---

## D-003 — `G004` desativado apenas para `main.py`

**Contexto.** O ruff acusa 17 f-strings em chamadas de log dentro de `main.py`.

**Decisão.** Ignorar `G004` para `main.py` (via `per-file-ignores`), mantendo ativo
no resto do repo.

**Motivo.** `main.py` não usa o módulo `logging`: usa `JobLogger`, uma classe local com
assinatura `info(message, stage=None, **extra)` que **não interpola argumentos**. Passar
`"... %s", valor` resulta em `TypeError` — foi exatamente o que aconteceu ao converter
automaticamente.

**Lição.** Antes de aplicar correção automática de lint em logging, verifique se o
objeto é de fato um `logging.Logger`. Falso positivo aqui custou 17 conversões e uma
regressão nos testes do gatekeeper.

**Reversível?** Sim — se `JobLogger` passar a herdar de `logging.Logger`, remova a exceção.

---

## D-004 — `BLE001` (except cego) tolerado

**Decisão.** Não sinalizar `except Exception` no código de produção.

**Motivo.** Os fallbacks de CUDA→CPU, modelo→fallback e parse opcional precisam
capturar qualquer falha da biblioteca de terceiros (que levanta tipos heterogêneos:
`RuntimeError`, `OSError`, `AttributeError`). Cada ocorrência loga a exceção logo
abaixo, então o erro não desaparece.

**Cuidado.** Nunca use `except: pass` silencioso. Se adicionar um, é bug.

---

## D-005 — Fixtures geradas, não versionadas

**Decisão.** `tests/fixtures/*.mp4` ficam no `.gitignore`; só o gerador é versionado.

**Motivo.** As três fixtures somam ~41 MB. Repositório pequeno é repositório que clona
rápido. `make fixtures` regenera em menos de 2 minutos.

**Trade-off.** CI precisa chamar `make fixtures` antes de `make eval`. O job de teste
unitário não depende delas.

---

## D-006 — `line-length = 120`

**Decisão.** 120 em vez do padrão 88.

**Motivo.** Os léxicos de palavras-chave (`REACTION_WORDS`, `FAIL_WORDS`…) são dados,
não lógica. Quebrar uma lista de palavras em 4 linhas piora a leitura. Onde a linha
passou de 120, foi quebrado com helper local (`_kw`, `_comp`).

---

## D-007 — Fase 0 mediu a linha de base antes de qualquer mudança

**Decisão.** Antes de tocar em scoring ou edição, medir o top score de cada fixture.

**Medido (2026-11-07, com `audio`+`vision`, sem render):**

| Fixture | Candidatos | Top score |
|---|---|---|
| `fala_continua` | 1 | 64.6 |
| `fala_reativa` | 2 | 85.2 |
| `cena_passiva` | 0 | — |

**Por quê.** Sem número de partida, "melhorou" é opinião. A Fase 1 usa esta tabela como
gabarito: `fala_reativa` deve ficar acima de 85 e `cena_passiva` deve continuar em zero.

**Não provado.** Isso vale para fixtures sintéticas. Gravação real de jogo tem ruído,
música de fundo e sotaque — comportamento lá continua desconhecido.
