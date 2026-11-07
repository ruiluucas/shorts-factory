# JEV — Notas de integração

Registro do que foi **confirmado por fonte primária** antes de escrever código.
Toda afirmação aqui tem origem rastreável. O que não pôde ser confirmado está
marcado **NÃO CONFIRMADO** e recebe apenas implementação mock/fallback.

Pesquisa: 2026-11-07. Modelo em produção no momento do teste: `jev-1.14.0`.

---

## 1. O que o Jev é

**CONFIRMADO** — [docs.typesafe.ai/introduction](https://docs.typesafe.ai/introduction):

> "Jev is TypeSafe's flagship model and the first System One model. System One models
> are built to make fast, structured decisions that software can use directly. Jev
> evaluates typed *questions* against a *state* and returns structured results directly.
> No text generation, no parsing."

**O que faz:**

- Recebe um `state` (string, objeto JSON ou array) e um conjunto de **perguntas tipadas**.
- Retorna **uma resposta por pergunta**, com valores tipados e distribuições de probabilidade.
- Três primitive types textuais + um de imagem:

| Primitive | Pergunta | Resposta |
|---|---|---|
| `choice` | escolher 1 entre N opções nomeadas | `choice` (str), `probabilities` (map), `confidence` (float) |
| `score` | avaliar em níveis ordenados (2–10) | `score` (float), `legend`, `probabilities`, `confidence` |
| `noul` | sim/não com probabilidade | `noul` (float 0–1) |
| `box` | onde está algo numa imagem | `box {top,bottom,left,right}` (frações 0–1), `confidence` |

**O que NÃO faz:**

- **Não gera texto.** Não escreve legenda, título, resumo ou explicação. Confirmado:
  "No text generation".
- Não navega, não chama ferramentas, não executa ações.
- Não é um LLM com contexto grande — é um classificador/pontuador estruturado.

**Consequência para o Shorts Factory:** Jev serve para *decidir* (pontuar janela,
escolher hook, dar veredicto de publish), nunca para *redigir*. Legendas continuam
vindo do Whisper.

---

## 2. API

**CONFIRMADO** por [openapi.json](https://api.typesafe.ai/openapi.json) (v0.2.0):

| Item | Valor |
|---|---|
| Base URL | `https://api.typesafe.ai` |
| Endpoint | `POST /v1/systemone` |
| Auth | `Authorization: Bearer <TYPESAFE_API_KEY>` (HTTPBearer) |
| Modelos | `GET /v1/models` → `jev-latest`, `jev-preview` |

### Formato de request

**CONFIRMADO** (spec + teste real). Atenção: `questions` é **objeto indexado por
nome**, não array. Esta foi a causa de um HTTP 422 durante a validação.

```json
{
  "model": "jev-latest",
  "state": "texto ou objeto de contexto",
  "questions": {
    "hook": {
      "type": "score",
      "criteria": ["abertura fraca", "abertura razoável", "abertura forte"]
    },
    "standalone": {
      "type": "noul",
      "criteria": { "true": "faz sentido sozinho", "false": "precisa de contexto" }
    },
    "categoria": {
      "type": "choice",
      "criteria": { "REACTION": "reação exagerada", "FAIL": "uma falha" }
    }
  }
}
```

Regras de `criteria` (confirmadas na spec):

- `choice`: objeto `{nome: descrição}`, 1–255 propriedades. Sem descrição, o nome
  sozinho é interpretado.
- `score`: **array ordenado**, 1–10 itens. A posição define a nota, começando em 0.
- `noul`: objeto opcional `{true: ..., false: ...}`.
- `box`: sem `criteria`; usa `instructions` (str) descrevendo o alvo.

### Formato de resposta

```json
{
  "model": "jev-1.14.0",
  "answers": {
    "hook":      { "type": "score", "score": 1.96, "confidence": 0.96,
                   "legend": {...}, "probabilities": {...} },
    "standalone":{ "type": "noul",  "noul": 0.26 },
    "categoria": { "type": "choice","choice": "REACTION", "confidence": 0.79,
                   "probabilities": {...} }
  },
  "usage": { "input_tokens": 518, "output_tokens": 95 }
}
```

---

## 3. Imagens

**CONFIRMADO (tecnicamente) / NÃO CONFIÁVEL (qualidade).**

- Envio via `Asset`: `{"data_base64": "...", "format": "png"}` inserido no `state`
  ou nas `instructions`.
- Header **obrigatório**: `TypeSafe-Danger-Zone-Images: true`.
- A doc marca Box e imagens como **"Dreamer-ready"**:
  > "Images and Box questions aren't yet up to our standards to call ready for
  > production... The request format, the answers, and the limits on this page can
  > change between releases, and accuracy will change as we update the models."

### Medição real (não é opinião)

Dois testes com imagem sintética, resultado medido:

| Teste | Esperado | Resposta do Jev |
|---|---|---|
| Retângulo vermelho em x[0.594–0.844], y[0.500–0.778] | "há carro", box na região | `noul=0.14` (não há), box em x[0.44–0.54] y[0.04–0.54], conf **0.03** |
| Círculo amarelo óbvio em x[0.34–0.66], y[0.23–0.44] + texto grande | "há círculo", box correto | `noul=0.10` (não há), box errado, conf **0.03** |

**Conclusão:** a capacidade de imagem responde HTTP 200 mas **não acerta nem objetos
geométricos óbvios**. Não serve para guiar crop em produção. O uso D (localização
visual) fica no caminho heurístico (detector de rosto/movimento já existente) e o
`JevApiBackend` **não** recebe quadros por padrão.

---

## 4. O que o Jev acerta bem (medido)

### a) Scoring semântico de janela (uso A)

State: *"Mano, olha isso! Caralho, que queda foi essa? Meu deus, ele voou longe demais!"*

| Pergunta | Resposta | Interpretação |
|---|---|---|
| hook | score **1.96**/2, conf 0.96 | abertura forte (p2=0.98) |
| payoff | score 1.32/2, conf 0.40 | tem momento, não conclui |
| standalone | noul 0.26 | precisa de contexto |
| categoria | **REACTION** 0.84 | correto |

Latência **0.226 s**, 518 tokens de entrada. **É viável rodar em produção.**

### b) Publish gate (uso C) — o maior ganho

**Texto sujo** (palavrão + violência + conselho financeiro):

| Flag | Prob. | Correto? |
|---|---|---|
| profanity | 0.94 | ✅ |
| violence | 0.92 | ✅ |
| medical_advice | 0.09 | ✅ (não havia) |
| financial_advice | 0.89 | ✅ |
| copyright_risk | 0.18 | ✅ |
| **verdict** | **block** 0.84 | ✅ |

**Texto limpo** (tutorial de crafting):

|Flag|Prob.|
|---|---|
|profanity|0.10|
|violence|0.07|
|financial|0.09|
|**verdict**|**publish** 0.98, conf 0.97|

Discriminação limpa nos dois sentidos. Flags `medical_advice` não dispara sem
conteúdo médico — não é um gate paranoico.

---

## 5. VideoJev (uso E) — hardware

**CONFIRMADO** por [github.com/MCG-NJU/VideoJev](https://github.com/MCG-NJU/VideoJev):

| Modelo | Base | Precisão | Pesos (BF16) |
|---|---|---|---|
| VideoJev-4B | Qwen3.5-4B | BF16 · Safetensors | ~8 GB |
| VideoJev-35B-A3B | Qwen3.6-35B-A3B (MoE) | BF16 · Safetensors | ~70 GB |

**VRAM desta máquina (medida):** GTX 1650, 4096 MiB total, **3723 MiB livres**.

**Não cabe.** 4B em BF16 = 8 GB de pesos + KV cache de vídeo. Estouraria 2× a VRAM.

**Decisão:** `VideoJevBackend` existe **apenas como adaptador para endpoint externo**,
desligado por padrão. Nunca carrega pesos localmente. Ver D-009 em `docs/DECISIONS.md`.

**NÃO CONFIRMADO:**

- Se há quantização INT4/INT8 oficial que caiba em 4 GB. O README cita BF16 para
  ambos os checkpoints; não encontrei variante quantizada publicada.
- VRAM exata em runtime com vídeo longo (depende do número de frames amostrados).

---

## 6. Privacidade

**CONFIRMADO** pelo comportamento da API: o `state` inteiro é enviado ao servidor.
Não há modo "local" no `api.typesafe.ai`.

**Implicação:** sem `decision.allow_remote: true`, **nem texto de transcrição pode
sair**. O backend padrão (`HeuristicBackend`) é puramente local e não faz nenhuma
chamada de rede.

**NÃO CONFIRMADO:**

- Política de retenção de dados da TypeSafe.
- Se há endpoint regional (UE/US) ou on-premise para o Jev.
- Preço por chamada. A página de custos do MCP descreve Jev como "one cheap gate",
  mas não há tabela de preços pública que eu tenha conseguido extrair.

---

## 7. Backends alternativos (OpenJev / Simple JEV)

**NÃO CONFIRMADO.** A galeria `awesome-jev` lista projetos comunitários, mas:

- Não localizei uma especificação estável de API compatível com `/v1/systemone`.
- Não confirmei formato de request/response de nenhum "OpenJev".

**Decisão:** `OpenJevBackend` é implementado como adaptador **configurável**
(`base_url` + `api_key_env`), assumindo formato compatível com o oficial. Se o
servidor real divergir, o cliente falha, cai no `HeuristicBackend` e registra o erro.
Não invento um protocolo que não pude verificar.

---

## 8. Fontes consultadas

| Fonte | Status |
|---|---|
| https://api.typesafe.ai/openapi.json | ✅ spec real, v0.2.0 — **fonte principal** |
| https://docs.typesafe.ai/llms.txt | ✅ índice completo |
| https://docs.typesafe.ai/introduction | ✅ definição de Jev |
| https://docs.typesafe.ai/primitives/box.md | ✅ Box + aviso "dreamer-ready" |
| https://docs.typesafe.ai/primitives/choice.md | ✅ |
| https://docs.typesafe.ai/images.md | ✅ header e Asset |
| https://docs.typesafe.ai/introduction/quickstart.md | ✅ |
| https://github.com/MCG-NJU/VideoJev | ✅ tamanhos e precisão |
| https://glama.ai/mcp/servers/jiawei686/jev-av-analysis-mcp | ✅ flags de sensibilidade = uso em produção |
| https://github.com/BeatAPI/awesome-jev | ⚠️ galeria; só navegação, pouca substância técnica |

**Chamadas reais feitas:** `GET /v1/models` (200), 5× `POST /v1/systemone` (200),
1× `POST` com imagem (200). Todas com `TYPESAFE_API_KEY` do ambiente. Nenhuma
transcrição de cliente real foi enviada — só texto de teste criado para validação.
