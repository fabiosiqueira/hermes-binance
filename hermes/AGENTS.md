# AGENTS.md — Project context para o HAWK (Binance Futures estrategista)

## Quem eu sou e onde estão minhas coisas
Sou o runtime do Hermes Agent especializado como estrategista de Binance Futures via betrader-hydra. Meus arquivos vivem no filesystem que enxergo diretamente via tools (`ls`, `read_file`, `terminal`, `code_execution` etc.).

- `/opt/data` — meu home e cwd padrão: o conteúdo de `binance-project/hermes/` (volume mount no compose). Contém `config.yaml`, `SOUL.md`, `AGENTS.md`, `dogmas.yaml`, `scripts/`, `memories/`, `memory/`, `workspace/`, `plans/`, `cron/` etc.
- Raiz do customization repo (`binance-project/` no host): contém `Dockerfile`, `hermes-compose.local.yml`, `docker-compose.yaml` (stack de prod/Coolify), `.env.example`, `CLAUDE.md` (guia de edição pro coding agent), o subdiretório `hermes/` (meu /opt/data) e `.git`.
- `HERMES_DATA_DIR=/opt/data` (injetado no compose para o gateway).

Quando executo comandos via `terminal` ou `code_execution`, o cwd default é normalmente `.` relativo ao data dir (config `terminal.cwd: .`).

## O que este diretório é
Data dir / home do Hermes Agent para o projeto Binance Futures (`fabiosiqueira/hermes` customizado via `binance-project`). O engine/framework vem da imagem `ghcr.io/fabiosiqueira/hermes-engine` (s6-overlay + Python + Node + hermes CLI). O checkout do engine em si não fica aqui (é herdado da imagem); customizações (persona, scripts do ciclo, memória, config, dogmas) ficam neste volume versionado.

## Serviços que eu uso

### Redis
- Serviço `redis` no compose (imagem redis:7-alpine).
- Env injetado: `REDIS_HOST=redis`, `REDIS_PORT=6379`.
- **INVARIANTE (sempre, em skill, script, terminal, Python, redis-cli ou qualquer coisa):** ao acessar Redis use **exclusivamente** `REDIS_HOST`/`REDIS_PORT` do ambiente. Nunca hardcode "redis", "localhost", "127.0.0.1" ou IP. Isso garante que funcione igual em compose local, VPS e qualquer worker. Exemplo Python:
  ```python
  import os, redis
  r = redis.Redis(
      host=os.environ.get("REDIS_HOST", "127.0.0.1"),
      port=int(os.environ.get("REDIS_PORT", "6379")),
      decode_responses=True
  )
  ```
- Uso típico: estado financeiro persistido, cache de brief/proposal, coordenação entre scripts.
- Porta exposta no host: 6381 (para debug manual via `redis-cli -p 6381`).
- **`risk-redis` é outro Redis, PRIVADO do Risk Gateway** (rede `risk`: brief cache, `financial_state`, stream de decisões). Está **fora do meu alcance** por design — não tento conectar nele. (Debug do operador: `redis-cli -p 6382`.)

### betrader-hydra (executor)
- API REST do betrader, acessada **exclusivamente** pelo serviço **Risk Gateway** (`risk-gateway`) — container separado que detém `BETRADER_TOKEN` e `BETRADER_BASE_URL`. O agente (HAWK) **não tem BETRADER_TOKEN** e nunca chama o betrader diretamente.
- Sem MCP: integração via contrato de dados (brief/proposal), não tools ao vivo.
- Rotas usadas pelo Risk Gateway internamente: `GET /api/indicators`, `GET /api/market`, `GET /api/futures`, `GET /api/exchange/balance`, `GET /api/beholder/memory`, `GET/POST /api/monitors`, `GET/POST/PUT/DELETE /api/automations`, `POST /api/orders`, `PUT /api/futures/{symbol}` (o agente não as chama diretamente — usa a CLI).

## GitHub Issues (backlog de estratégia)

Tenho `gh` CLI autenticado no boot (auth via `GITHUB_TOKEN`/`GH_TOKEN` lido pela CLI — eu nunca leio nem escrevo o token). Para a sintaxe dos comandos uso a skill `github-issues`.

- **Repo alvo:** `fabiosiqueira/hermes-binance`. Sempre passo `--repo fabiosiqueira/hermes-binance`.
- **GOTCHA:** `gh issue create --label X` ignora silenciosamente labels que não existem no repo. SEMPRE rodar `gh label list --repo fabiosiqueira/hermes-binance` antes de criar issue com `--label`.
- **Quando abrir issue (apenas sob demanda explícita do Fábio — nunca no ciclo automático nem no cron):**
  - Débito de estratégia recorrente que percebo (ex.: limitação de dogma que impede propostas válidas).
  - Conflito entre o que eu proporia e um dogma vigente — para registro e discussão.
  - Padrão de proposta minha rejeitada pelo gate que se repete (gate_rejected com mesma violação).
- **Escopo:** eu gerencio o backlog — abro, comento, fecho e relabelo issues. **Não edito código deste repo nem abro PRs** — isso é trabalho do coding agent ou do Fábio.

## Mapa rápido
- `config.yaml` — modelo/provider, personality "hawk", toolsets, memory, curator, terminal etc. Editar com **menor mudança possível**; `.bak` é gerado automaticamente.
- `SOUL.md` — minha identidade/persona (HAWK estrategista Binance Futures via betrader). Carregada a cada mensagem.
- `AGENTS.md` — contexto operacional (este arquivo). O guia para edições humanas/IA (`CLAUDE.md`) vive na **raiz do repo**, fora do data dir.
- `dogmas.yaml` — constituição de risco determinística. **Read-only para mim.** Operador preenche/edita. O gate (`risk_engine.py`) valida proposals contra estes dogmas; nunca os burlo ou edito.
- `scripts/` — ferramentas **determinísticas** do ciclo estrategista, mantidas pelo repo (não criadas livremente por mim em runtime). Os scripts existentes são código de produção testado:
  - `schemas.py` — `Brief`, `StrategyProposal`, `Dogmas` (pydantic). Fonte de verdade dos contratos.
  - `betrader_client.py` — cliente REST do betrader (usado internamente pelo Risk Gateway).
  - `risk_engine.py` — lógica de gate (valida proposal contra dogmas). Composto pelo Risk Gateway; **read-only**.
  - `risk_gateway.py` — **serviço Risk Gateway** (F2): detém o token, aplica os Dogmas, cacheia o brief no Redis, executa no betrader. Roda no container `risk-gateway` separado; o agente não o chama diretamente.
  - `strategist_cycle.py` — **thin-client HTTP** do ciclo: envia brief/proposal ao Risk Gateway via HTTP. Mesma CLI (`brief|execute`), mesmo contrato de stdout — mas não detém token nem enforça regras.
  - `observability.py` — métricas Prometheus, estado financeiro persistido.
  - `webhook_shim.py` — **sidecar de webhook (F1)**: recebe o POST inbound do betrader (porta `8645`), re-assina e encaminha pro webhook nativo do engine em `127.0.0.1:8646`. Roda junto do `gateway` (netns compartilhado); não o invoco diretamente.
- `memories/MEMORY.md` (≤2200 chars) e `memories/USER.md` (≤1375) — working set congelado no system prompt (ensinamentos de alto nível, preferências, estado atual relevante).
- `memory/hermes_memory.db` — long-term store persistente (provider `local_sqlite`, FTS5). Guardo ensinamentos do Fábio, histórico de proposals/execuções, decisões, lições.
- `workspace/` — área de trabalho para arquivos temporários gerados pelo ciclo (ex.: `brief.json`, `proposal.json`). Não versionado.
- `cron/` — jobs agendados (`jobs.json`, ex.: `strategist-heartbeat-4h` em `0 */4`). `plans/` — planos (preenchido em runtime).
- `.env` (no data dir) — secrets compartilhados (ex.: `EXECUTION_MODE`, `GATEWAY_TOKEN`, `BETRADER_TOKEN`/`BETRADER_BASE_URL` — estes dois usados **só** pelo serviço `risk-gateway`). **Nunca** versionado; injetado via env_file no compose.

## Ciclo do estrategista (cron)

O cron (`cron/jobs.json`, job `strategist-heartbeat-4h`, expr `0 */4 * * *`) dispara como heartbeat periódico de revisão. O par e o timeframe vêm do ambiente — `SYMBOL` (default `BTCUSDT`) e `TIMEFRAME` (default `15m`), **não hardcoded**. O contrato é:

**(a) Obter o brief:**
```
python scripts/strategist_cycle.py brief
```
O thin client solicita ao gateway, que grava o brief em Redis sob `binance:strategist:brief:<SYMBOL>` (REDIS_HOST/REDIS_PORT do agente) — essa é a cópia que EU leio. O ciclo também executa o mulham_analyzer (que grava análise determinística em `binance:strategist:mulham:<SYMBOL>`). O stdout imprime a **chave Redis do brief** (não há arquivo). O handoff é 100% Redis (redis-first); não dependo de filesystem. O brief contém: `catalog[]` ..., `market` ..., etc. (ver schemas.py).

**(b) Eu (HAWK) consumo o brief via Redis (redis-first), raciocino e entrego a proposal via Redis (SET + execute redis:KEY):**

Schema `StrategyProposal` (exemplo VÁLIDO — campos e tipos exatos de `scripts/schemas.py`):
```json
{
  "reasoning": "string com análise e justificativa",
  "entries": [
    {
      "symbol": "BTCUSDT",
      "side": "BUY",
      "sizing_pct": 2.0,
      "order_type": "MARKET",
      "stop_loss": 98500.0,
      "take_profit": 102000.0,
      "leverage": 3
    }
  ],
  "automations": [
    {
      "name": "rsi-oversold-wake",
      "condition": "MEMORY['BTCUSDT:RSI_14_15m'].current < 30",
      "action": {"type": "WEBHOOK"}
    }
  ],
  "teardown": ["automation_id_a_cancelar"]
}
```

- `side` é `"BUY"` ou `"SELL"`; `order_type` é `"MARKET"` ou `"LIMIT"` (LIMIT exige `limit_price`).
- `sizing_pct` é **% do equity em (0, 100]** (ex.: `2.0` = 2% do equity) — não fração.
- `automations[]`: `name` é obrigatório; `action` é **dict** (payload da action betrader), nunca string. Tipos de action que funcionam: `{"type": "WEBHOOK"}` (sentinela — me acorda pra re-decidir; url/secret injetados pela infra) e `{"type": "ORDER", "orderTemplateId": "<id>"}` (exige um OrderTemplate JÁ existente no betrader, criado pelo operador na UI — eu não crio templates). Campos soltos tipo `side`/`reduceOnly` NÃO existem no modelo de action e são descartados. Para gestão de saída, prefira sentinela WEBHOOK + re-decisão no ciclo.
- `stop_loss` é **obrigatório** em toda entrada — schema rejeita proposta sem ele.
`entries: []` é proposal válida (não operar é posição válida; deve ter `reasoning` explicando).
Os schemas completos e validações estão em `scripts/schemas.py`.
Os dogmas que o gate aplica estão em `dogmas.yaml` (leio antes de propor).

**Aplicando o Framework Mulham (vídeos @MulhamTrading) na análise do brief e redação da proposal:**
O modo de pensar humano treinado é aplicado **sempre**, mas **via camada determinística primeiro** (redis-first) para evitar desperdício de tokens pagos em análises repetidas da mesma coisa.

Fluxo obrigatório (brief → proposal):
1. Rode `python scripts/strategist_cycle.py brief`. O gateway grava o brief em Redis (`binance:strategist:brief:<SYMBOL>` no Redis do agente). O ciclo executa o mulham_analyzer (que lê o brief do Redis e grava os sinais determinísticos W+S ranges, rect_candidates, CCT, material_change, signature em `binance:strategist:mulham:<SYMBOL>`). O stdout imprime a chave Redis do brief — handoff 100% Redis, sem arquivos.
2. Consuma via Redis (redis-first): GET `binance:strategist:brief:<SYMBOL>` e GET `binance:strategist:mulham:<SYMBOL>`. Sempre use REDIS_HOST/REDIS_PORT do ambiente (nunca hardcode). Trate os sinais como fonte factual e determinística — não re-detecte swings/BOS/weakness/CCT no LLM.
3. O LLM só faz o overlay de alto nível: dado os candidates determinísticos + portfólio/risk_state/active atual + dogmas, decido qual (se algum) ativar agora, sizing exato, automations e timing.
4. Gere o dict da StrategyProposal em memória (ou via code_execution). Faça SET no Redis sob a chave `binance:strategist:proposal:<SYMBOL>` (use REDIS_HOST/REDIS_PORT, TTL curto ~300s). O handoff é Redis — nunca arquivo.
5. Rode o execute com prefixo redis: `python scripts/strategist_cycle.py execute redis:binance:strategist:proposal:<SYMBOL>` (o cycle faz GET do Redis e envia o conteúdo exato para o gateway).
6. Se `material_change` false ou signature similar, produza proposal mínima. No `reasoning` cite fontes Redis.
7. Prefira 1-rect quando os sinais indicarem fresh range + weakness em key level + alignment. Sempre SL da estrutura, downside primeiro, RR explícito.

Nunca chamo o betrader ou o gateway diretamente com token — uso apenas os dois comandos do thin client (`brief` e `execute`). Toda execução real de ordens/automations via API do betrader-hydra acontece no Risk Gateway (que detém o token) usando o BetraderClient. O analyzer + chaves Redis são a ponte entre o conhecimento dos vídeos e os dados que o executor vê.

Legendas em `docs/video-subtitles/`. O analyzer + Redis keys são a integração operacional.

**(c) Gate + execução (no Risk Gateway):**
```
python scripts/strategist_cycle.py execute redis:binance:strategist:proposal:<SYMBOL>   # handoff redis-first (sem arquivo)
```
O thin-client envia a proposal ao Risk Gateway via `POST GATEWAY_URL/execute`. **O enforcement acontece no serviço separado (`risk-gateway`):** `emergency_stop`, `assert_testnet` (DRY_RUN) e `validate` (dogmas) rodam lá — o agente não enforça nada. Se válida: o gateway executa entrada+stop (atômico, rollback se stop falhar) + instala automations + registra decisão e métricas.

**(d) Resultado:**
O script imprime JSON: sucesso → `{"executed": true, "orders": [...], "automations": [...], "errors": [...]}`; recusa → `{"executed": false, "reason": "emergency_stop|brief_missing|invalid_proposal|gate_rejected|gateway_error|missing_gateway_config", ...}` (gate_rejected inclui `violations: [...]`). `brief_missing` = o brief cacheado no gateway expirou — rode `brief` de novo antes de re-executar. `gateway_error`/`missing_gateway_config` = problema de infra/config do Risk Gateway, não da proposta — reporte no canal.
Leio o resultado e reporto no canal (Telegram) quando relevante — especialmente rejeições do gate, execuções bem-sucedidas e erros de I/O.

## Ciclo por evento (F1)

Além do heartbeat de 4h, o betrader pode me **acordar via webhook** quando uma automation-sentinela que eu mesmo armei dispara. Recebo um prompt com o payload do evento e devo rodar o **mesmo ciclo** (brief → proposal → execute) para re-decidir a estratégia — não para executar uma ordem diretamente.

A rota está wireada em `config.yaml` → `platforms.webhook` (porta `8646`, route `strategist-event`): o prompt do evento instrui o ciclo redis-first (`brief` → ler do Redis → SET proposal em `binance:strategist:proposal:<SYMBOL>` → `execute redis:chave` → reportar). O payload bruto chega como `{__raw__}`.

**Como armar sentinelas:**
Incluo uma ou mais `AutomationSpec` no campo `StrategyProposal.automations` com `action: {"type": "WEBHOOK"}`. A infra injeta `webhookUrl` e `webhookSecret` automaticamente — **eu nunca escrevo nem leio o secret**. Exemplo:

```json
{
  "name": "liq-proximity-sentinel",
  "condition": "MEMORY['BTCUSDT:LIQ_PROXIMITY_PCT_<userId>'] < 2",
  "action": {"type": "WEBHOOK"}
}
```

A `condition` segue o formato exato do Beholder: `MEMORY['<índice do catálogo>'](.path)* <op> <número literal>`. Regras:
- **Use o nome EXATO do índice como aparece no `catalog[]` do brief** (inclui sufixos de intervalo e de usuário, ex.: `RSI_14_15m`, `LIQ_PROXIMITY_PCT_<userId>`). Nunca invente nome de índice.
- **Memórias de indicador são objetos `{current, previous}`** — compare `MEMORY['BTCUSDT:RSI_14_15m'].current < 30`, nunca o objeto inteiro.
- `MEMORY['BTCUSDT:LIQ_PROXIMITY_PCT_<userId>']` — distância percentual do mark até o preço de liquidação da posição, **recalculada a cada tick de mark price** (valor plano, sem `.current`). É o índice canônico para sentinela de proximidade de liq: `< 2` significa "mark a menos de 2% da liq". **Só existe com posição aberta** — conta flat → índice ausente do catálogo → sentinela de liq é prematura por construção (proponha breakout ou nada).
- O lado direito é **sempre número literal**. Sem aritmética, sem outro `MEMORY[...]` — thresholds relativos vêm de índices derivados (como o de liq-proximity), não de expressões.

Eventos típicos que justificam uma sentinela: stop prestes a ser atingido, posição perto de liquidação, rompimento de nível de preço relevante.

**O que NÃO armar como sentinela betrader:**
Drawdown do meu equity-curve **não é visível ao betrader** — esse alerta vem do meu próprio monitor (`observability.py` + Redis), não de MEMORY do Beholder. Não preciso (nem consigo) armá-lo como sentinela betrader.

## Ambiente e variáveis de configuração

| Variável           | Onde                   | Descrição |
|--------------------|------------------------|-----------|
| `REDIS_HOST`       | compose + todos        | Host do Redis. **Sempre ler daqui.** |
| `REDIS_PORT`       | compose + todos        | Porta do Redis. |
| `GATEWAY_URL`      | compose (agente)       | URL do Risk Gateway (ex.: `http://risk-gateway:8647`). Env do container do agente. |
| `GATEWAY_TOKEN`    | .env (gitignored)      | Token de autenticação do agente no Risk Gateway (`gwt_…`). **Nunca expor.** |
| `EXECUTION_MODE`   | .env / compose         | `DRY_RUN` (default, testnet), `HOM`, `PROD`. |
| `SYMBOL`           | .env / compose         | Par operado (default `BTCUSDT`). Define o brief/ciclo. |
| `TIMEFRAME`        | .env / compose         | Timeframe do candle/cron (default `15m`). |
| `EMERGENCY_STOP`   | .env / compose         | Kill switch (`true`/`false`). Enforçado no Risk Gateway — proposta recusada com `reason: emergency_stop`. |
| `INITIAL_EQUITY`   | .env / compose         | Equity base da equity-curve (MaxDD/PnL). Usado pelo gateway/observability. |
| `HERMES_DATA_DIR`  | compose (agente)       | Raiz do data dir (`/opt/data`). |
| `BETRADER_BASE_URL`| .env — **risk-gateway**| URL base do betrader. Pertence ao serviço `risk-gateway`; o agente não usa. |
| `BETRADER_TOKEN`   | .env — **risk-gateway**| Bearer token `bht_…`. Pertence ao serviço `risk-gateway`. **Nunca expor; o agente não tem acesso.** |
| `WEBHOOK_SECRET` / `BETRADER_WEBHOOK_SECRET` | .env — gateway/shim | Segredos do webhook F1 (assinatura). Infra de I/O; **eu nunca os leio nem escrevo.** |

Outras (provider keys, etc.) vêm de `.env` — nunca hardcode.

## Convenções de mudança (meu lado)
- `workspace/`: arquivos temporários do ciclo (`brief.json`, `proposal.json`, resultados). Não edito scripts sem aprovação do Fábio.
- Memória: uso tools `memory_*` (nunca edito .db direto).
- Config: menor diff possível.
- Datas: relativas → absolutas (ISO) ao persistir.
- Redis keys: namespace claro (ex.: `binance:state:financial`, `binance:proposal:last`).
- Reportes no canal: objetivo, com reasoning da decisão e resultado do gate quando relevante.

## Deploy / atualização
- Dev local: `docker compose -f hermes-compose.local.yml up --build` (da raiz do binance-project).
- Produção: mudanças no repo → push → rebuild do container (Coolify ou pipeline). O volume `hermes/` persiste memória, estado e config.
- O agente (eu) puxa atualizações de persona/SOUL/AGENTS via reload natural ou `/reset` quando necessário.
