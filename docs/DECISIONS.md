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

---

## D-008 — Jev como camada de decisão, nunca de geração

**Contexto.** O Jev (TypeSafe System One) foi cotado para "deixar os shorts mais
inteligentes". A tentação natural é pedir texto a ele.

**Decisão.** Jev só recebe perguntas tipadas (`choice`/`score`/`noul`/`box`) e devolve
valores estruturados. Nenhuma legenda, título ou resumo passa por ele.

**Motivo.** A doc oficial é explícita: *"No text generation, no parsing."* Ele é um
classificador calibrado. Usá-lo como gerador de texto desperdiça a única coisa que
ele faz melhor (probabilidade calibrada) e introduz risco de alucinação.

**Consequência.** Legendas continuam vindo do Whisper word-level. Jev pontua janela,
escolhe hook e dá veredicto de publish.

---

## D-009 — Uso de imagem desligado por medição, não por precaução

**Contexto.** A API aceita `box` com imagem (`TypeSafe-Danger-Zone-Images: true`).
Seria o caminho ideal para guiar crop 9:16 pelo alvo citado na fala (uso D).

**Medição feita (2 testes reais, HTTP 200 nos dois):**

| Imagem | Alvo óbvio | Resposta |
|---|---|---|
| Retângulo vermelho em x[0.59–0.84] y[0.50–0.78] | "há carro?" | `noul=0.14` (disse não), box errado, conf 0.03 |
| Círculo amarelo em x[0.34–0.66] y[0.23–0.44] + texto grande | "há círculo?" | `noul=0.10` (disse não), box errado, conf 0.03 |

**Decisão.** `visual_localization` fica heurístico (detector de rosto + movimento já
existentes). `JevApiBackend` **não recebe quadros**, mesmo com `allow_remote: true`,
a menos que `visual.send_frames_remote: true` seja ligado explicitamente.

**Motivo.** Errar um círculo amarelo com confiança 0.03 não é ruído — é ausência de
sinal. A própria doc marca Box como "Dreamer-ready … aren't yet up to our standards
to call ready for production".

**Reversível?** Sim. Reescreva os dois testes e compare; se acertar, ligue o flag.

---

## D-010 — VideoJev só como adaptador remoto

**Contexto.** VideoJev-4B promete grounding temporal/espacial em vídeo.

**Medição.** 4B em BF16 = ~8 GB de pesos; esta máquina tem 3.7 GB de VRAM livre.

**Decisão.** `videojev_enabled: false` por padrão. O backend existe como cliente de
endpoint externo, exige `allow_remote: true` **e** `videojev_endpoint` preenchido —
os dois validados no startup. Nunca carrega pesos locais.

**Motivo.** Tentar rodar em 4 GB é falha garantida e lenta. Falhar no startup com
mensagem clara é melhor que OOM no meio do job.

---

## D-011 — `allow_remote: false` como trava dura, não sugestão

**Decisão.** Configurar `backend: jev_api|openjev|videojev` com `allow_remote: false`
é **erro de validação**, não warning. O pipeline não inicia.

**Motivo.** A regra 4 do projeto diz que nenhum texto de transcrição sai da máquina
sem permissão explícita. Uma trava que só emite warning é uma trava que vira log
ignorado. Falhar cedo e alto é a única forma de garantir a propriedade.

**Efeito prático.** Nenhum teste precisa de rede: `heuristic` é o padrão e não abre
socket.

---

## D-012 — Usos A–E desligados até o eval provar ganho

**Decisão.** Todos os toggles em `decision.uses` nascem `false`. A Fase 0 mediu o
baseline; só depois do harness comparar se liga qualquer coisa.

**Medição de não-regressão (feita).** Com `backend: heuristic` e tudo desligado:
`fala_reativa` top score **85.22**, idêntico ao baseline 85.2 da Fase 0. Os 6
componentes originais continuam sendo os únicos; `jev_semantic` não existe ainda.

**Motivo.** "Mais inteligente" sem número é opinião. O gate é medir antes e depois.

---

## D-013 — B-roll com licença obrigatória, local antes de rede

**Contexto.** O pedido é edição transformativa: legenda, corte de áudio e imagens
sobre o que a pessoa está falando. O diferencial de qualidade vem do B-roll.

**Decisão.** Duas fontes, nesta ordem:
  1. `assets/broll/<assunto>/` — acervo do operador, vence sempre;
  2. Openverse (Creative Commons), só se `allow_network: true`.

**Por que Openverse e não Google Imagens/Pexels.** Openverse responde sem chave e
filtra por licença comercial na própria query (`license_type=commercial`). Pexels e
Pixabay exigem chave (401/400 medidos). Google Imagens não informa licença — usá-la
seria exatamente o erro que derruba canais.

**Trava de licença.** Só entram `by`, `by-sa`, `cc0`, `pdm`, `zero`. `by-nc` fica de
fora mesmo sendo CC, porque corta a monetização. Cada imagem carrega
`license` + `attribution` no `edit_plan`, então a proveniência é auditável.

**Medido.** `GET api.openverse.org/v1/images/?q=carro&license_type=commercial` →
HTTP 200, 240 resultados, licenças `by/2.0` e `by-sa/2.0` de provedor `flickr`.

---

## D-014 — B-roll no topo, com fade, e nunca dois ao mesmo tempo

**Decisão.** Overlay centralizado no topo (`top: 150px`), largura máxima 62%,
fade in/out de 8 frames, no máximo um por vez, no máximo 4 por corte, mínimo 5s
entre eles.

**Motivo.** A legenda ocupa o terço inferior. Empilhar duas imagens parece erro de
render, não edição. E sinônimos repetidos ("olha o carro, olha o carro") colapsam
numa imagem só, senão o short fica com três carros idênticos.

---

## D-015 — `JobLogger.warning()` como alias, não renomear os call sites

**Contexto.** O ruff da Fase 0 reescreveu `logger.warn()` → `logger.warning()`
(G010), mas `JobLogger` só tinha `warn`. A quebra ficou **latente** e explodiu no
`stage_qc` (`AttributeError: 'JobLogger' object has no attribute 'warning'`).

**Decisão.** Adicionar `warning()` como alias de `warn()` na própria classe.

**Motivo.** Todo mundo escreve `.warning()`; o lint reescreve para `.warning()`.
Manter só `warn` significa que a próxima autofix reintroduz o bug. Corrigir a classe
elimina a classe de erro, não a ocorrência.

**Lição.** Autofix de lint em código que usa um logger customizado precisa ser
verificada contra a interface real da classe — não contra a do `logging`.

---

## D-016 — Staging obrigatório de assets para o Remotion

**Contexto.** O primeiro render com B-roll falhou com 404 nas imagens.

**Causa.** `OffthreadVideo` e `Img` não leem caminho absoluto: o servidor estático
resolve tudo contra `<bundle>/public`. Duas falhas em sequência:
  1. caminho absoluto → 404 direto;
  2. arquivo estagiado em `public/` mas passado sem prefixo → 404 também.

**Decisão.** `main.py` ganhou `stage_broll_for_remotion()`, que copia as imagens
para `remotion/public/` e reescreve `plan["broll"][].path` para o nome puro; o
componente aplica `resolveSrc()`, o mesmo resolvedor do vídeo, que prefixa
`/public/`.

**Medido.** Depois disso: `[COMPLETED]`, 2 renders, QC OK, e o frame em 4.5s mostra
o box "CARRO" no topo enquanto o frame em 10s mostra só a legenda.
