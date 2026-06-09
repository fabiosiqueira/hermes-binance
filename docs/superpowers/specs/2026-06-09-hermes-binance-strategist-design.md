# Spec — Estrategista Hermes para Binance Futuros via betrader-hydra

**Data:** 2026-06-09
**Status:** Design aprovado · M1 detalhado · F1–F3 como issues
**Origem:** `hermes-binance#1` (kickoff de brainstorming)

## Contexto

Agente estrategista (Hermes/LLM) que opera Binance futuros de forma proativa e
autônoma, **fora do loop tick-a-tick**: lê estado de mercado/portfólio, decide e
adapta estratégia dentro de dogmas de risco, e delega a execução determinística ao
**betrader-hydra** (`gitlab.com/fabiosiqueira/betrader-hydra-bot`).

Reaproveita o esqueleto do `forex-project` (interrompido por incompatibilidade
MT5+Linux), trocando o executor MT5/MCP por betrader-hydra via REST. O Hermes é o
**engine LLM da NousResearch consumido como imagem**, com um data dir
(`SOUL.md`, `AGENTS.md`, `config.yaml`, `scripts/`, memória) — não um app custom.

## Decisões travadas (da issue #1, não rediscutir sem motivo)

- **Estrategista (LLM), não trader-no-loop.** O LLM escolhe/adapta estratégia; quem
  executa ordem é o betrader.
- **Executor = betrader-hydra** via REST. Não binance-api, não plugin novo no Hermes.
- **Liberdade da LLM = catálogo tipado + composição.** Escolhe/parametriza indicadores
  e compõe regras; não inventa indicador em runtime.
- **Dogmas = constituição de risco determinística**, guardrails invioláveis.
- **Ritmo = agendado + por evento.**
- **Sem MCP.** Integração via contrato de dados (brief/proposal), não tools ao vivo.
- **Milestone 1 = DRY_RUN** (testnet), gradação DRY_RUN → HOM → PROD.
- Vocabulário: "executor" e "estrategista", nunca "L1/L2".

## Decisões deste brainstorming

- **Modelo de operação = híbrido com gate duro.** O Hermes pode emitir ordem de
  entrada direta **E** compor automations de gestão. **Toda ordem de entrada carrega
  Stop Loss obrigatório**, garantido por gate determinístico — entrada sem SL nunca
  é um caminho possível.
- **Enforcement = Risk Gateway faseado.** M1 usa gate **in-process** (`risk_engine.py`);
  o gateway separado (Hermes sem token de trading) é entrega da **F2**, pré-requisito
  de PROD. O contrato `StrategyProposal` não muda entre as fases — promover o módulo a
  serviço é a migração.
- **Cadência M1:** revisão no **fechamento de candle do timeframe primário**.
- **Par M1:** **BTCUSDT perp** único.
- **Linguagem do adapter/gate:** **Python** (segue skeleton forex + ecossistema Hermes).

## Decomposição em fases

| Fase | Entrega | Artefato |
|---|---|---|
| **M1 — DRY_RUN** | Loop estrategista agendado end-to-end em testnet: brief → LLM propõe → gate valida → executa no betrader → métricas. Gate in-process. Híbrido c/ SL obrigatório. | spec (este doc) + plano |
| **F1 — Event-driven** | Receiver do `WEBHOOK` do betrader acorda o Hermes em evento severo (stop disparou, drawdown 80%, liquidação). Automations-sentinela. | issue |
| **F2 — Risk Gateway** | Extrai o enforcement pra serviço separado; Hermes perde o token de trading. Enforcement inviolável. Pré-requisito de PROD. | issue |
| **F3 — HOM→PROD** | Gradação: testnet+gateway (HOM) → validação de edge/métricas → mainnet capital pequeno (PROD). Grafana hom+prod. | issue |

## Arquitetura macro (estado final, F2+)

```
                  agendado (cron)  +  webhook (F1)
                         │
                         ▼
   ┌─────────────────────────────────────┐
   │  HERMES (LLM estrategista)            │   engine NousResearch, data dir
   │  lê Brief, escreve StrategyProposal   │   SEM token de trading (a partir de F2)
   └─────────────────────────────────────┘
              │ StrategyProposal (contrato de dados, sem MCP)
              ▼
   ┌─────────────────────────────────────┐
   │  RISK GATEWAY (determinístico)        │   detém token betrader, aplica DOGMAS
   │  rejeita: sem-SL, leverage>teto,      │   emergency_stop, %equity, drawdown
   │  traduz proposta → REST               │   expõe /health + /metrics (Prometheus)
   └─────────────────────────────────────┘
              │ REST (Bearer bht_…)
              ▼
   ┌─────────────────────────────────────┐
   │  betrader-hydra (Beholder)            │   executa tick-a-tick, spot+futures
   │  monitors, automations, orders        │   testnet ou mainnet (flag isTestnet)
   └─────────────────────────────────────┘
              │
              ▼  Binance Futures
```

Em **M1** o Risk Gateway é o módulo `risk_engine.py` in-process (mesmo contrato, sem
rede). A migração F2 promove o módulo a serviço; o contrato `StrategyProposal` é
estável entre as fases. É por isso que o design nasce correto começando simples.

## Contratos de dados

Três schemas tipados (pydantic). São o que torna a liberdade do LLM "catálogo +
composição", não texto livre.

### `Brief` (o Hermes lê)

Montado pelo adapter a partir de `/api/market`, `/api/futures`,
`/api/exchange/balance`, `/api/beholder/memory`, `/api/indicators`:

```
Brief {
  timestamp, mode: DRY_RUN|HOM|PROD
  catalog:    [indicadores disponíveis + params]    # de /api/indicators
  market:     { symbol, timeframe, candles[], indicators{} }
  portfolio:  { equity, balance, positions[], used_leverage }
  risk_state: { daily_pnl, drawdown_pct, equity_curve_ref }
  active:     [automations/orders vigentes + performance]
}
```

### `StrategyProposal` (o Hermes escreve, o gate valida)

```
StrategyProposal {
  reasoning: str
  entries: [ { symbol, side, sizing(%equity), order_type,
               stop_loss: REQUIRED, take_profit?, leverage } ]
  automations: [ { condition(MEMORY['sym:IND_params'] op val), action } ]
  teardown: [ ids a cancelar ]
}
```

`stop_loss` é obrigatório no schema — uma proposta de entrada sem SL é inválida por
construção, antes mesmo do gate.

### `Dogmas` (constituição, o operador preenche em `dogmas.yaml`)

```
Dogmas {
  max_leverage, max_position_pct_equity, max_daily_drawdown_pct,
  mandatory_stop_loss: true, emergency_stop: env,
  min_stop_distance_pct, allowed_symbols[]
}
```

### Invariante central

**Entrada e seu stop são emitidos como unidade — se o stop não confirmar, a entrada é
revertida (close imediato).** A atomicidade entrada+stop não é nativa na Binance
Futures (são 2 ordens: MARKET + STOP_MARKET reduceOnly); o gate executa
emit → confirm → rollback.

## Superfície da API betrader (verificado por amostragem de código)

| Capacidade | Rota | Uso pelo Hermes |
|---|---|---|
| Catálogo de indicadores | `GET /api/indicators` (sem auth) | descoberta de vocabulário |
| Valores correntes | `GET /api/automations/indexes`, `GET /api/beholder/memory` | brief |
| Mercado + portfólio | `GET /api/market`, `/api/exchange/balance`, `/api/futures` | brief |
| Monitors | `GET/POST/PUT/DELETE /api/monitors` + `/start` `/stop` | garante stream do par |
| Automations | `GET/POST/PUT/DELETE /api/automations` + `/start` `/stop` | gestão/saída |
| Ordens | `GET/POST /api/orders` (`?isFuture=true`) | entrada direta (híbrido) |
| Futuros | `PUT/DELETE /api/futures/{symbol}` | leverage/marginType, close |
| Auth de serviço | Bearer `bht_…` (criado via `POST /api/tokens`) | todas as chamadas de escrita |
| Testnet | flag `user.isTestnet` | DRY_RUN / paper |
| Webhook action | tipo `WEBHOOK` (`webhookUrl`, `webhookSecret` HMAC) | F1, acordar o Hermes |

Condições de automation: formato `MEMORY['SYMBOL:INDICATOR_params'] <op> valor`
(operadores `> < === != >= <=`). Ações: `ORDER`, `GRID`, `WEBHOOK`, `TRAILING`,
`ALERT_*`, `WITHDRAW`.

## M1 — escopo detalhado

### Estrutura do repo (espelha forex-project, sem `mt5-mcp`)

```
binance-project/
├── Dockerfile · hermes-compose.local.yml · hermes-coolify.yml
├── .env.example          token betrader, BETRADER_BASE_URL, EXECUTION_MODE
└── hermes/
    ├── SOUL.md           persona estrategista Binance (reescrita)
    ├── AGENTS.md · CLAUDE.md · config.yaml (sem mcp_servers)
    ├── dogmas.yaml       constituição de risco (operador preenche)
    └── scripts/
        ├── schemas.py            Brief · StrategyProposal · Dogmas (pydantic)
        ├── betrader_client.py    REST: fetch_brief + writes
        ├── risk_engine.py        gate in-process: valida proposal vs dogmas
        ├── strategist_cycle.py   brief→propose→gate→execute→record
        └── observability.py      métricas Prometheus + estado financeiro persistido
```

### Ciclo do estrategista (no fechamento de candle)

1. Garante monitor do par/timeframe (idempotente).
2. `fetch_brief()` — monta `Brief` a partir dos endpoints betrader.
3. LLM (Hermes) lê `Brief` → escreve `StrategyProposal`.
4. `risk_engine.validate(proposal, dogmas, brief)` → `(ok, motivo)`. Rejeita:
   sem-SL, leverage>teto, %equity estourado, drawdown diário estourado, emergency_stop.
5. Se ok: `betrader_client` executa — entrada+stop atômico (rollback se stop falhar) +
   instala automations de gestão.
6. `observability`: registra decisão, atualiza métricas (wins/losses/PnL/MaxDD/WinRate
   sobre equity-curve), persiste estado financeiro.

### Conformidade com `bot.md`

- **Modo via env:** `EXECUTION_MODE=DRY_RUN` default → betrader em testnet
  (`isTestnet=true`). Gradação DRY_RUN → HOM → PROD.
- **Resiliência:** estado financeiro sobrevive restart (carregado de Redis/DB em init);
  métricas Prometheus restauradas do estado persistido; reconexão sem crash manual.
- **Integridade financeira:** PnL/MaxDD sobre equity-curve (`initialEquity + cumPnL`),
  nunca PnL isolado; estado persistido antes de encerrar ciclo.
- **Kill switch:** `emergency_stop` via env, verificado no início de cada execução —
  sem throw, retorna `{executed:false, reason:"emergency_stop"}`.
- **Observabilidade:** `/health` + `/metrics` Prometheus desde M1. Dashboards Grafana
  hom+prod entram na F3.
- **Errors:** `errorOccurred({type})` com type descritivo em cada catch de I/O externo.

### Testes

- **Unit:** `risk_engine` (cada dogma isolado), `schemas`, `betrader_client` (mocks só
  na fronteira HTTP betrader).
- **Integração:** ciclo completo com betrader mock/testnet.
- **E2E:** lifecycle DRY_RUN (entrada → ciclo → saída → métricas), modo separado.
- **DI real**; mocks só nas fronteiras de I/O (HTTP betrader, Redis), nunca services
  internos.

## Riscos a verificar (no plano de implementação, contra a API rodando)

- **Shape exato de `/api/market`** — confirmar o bloco `portfolio` retornado.
- **`OrderTemplate` carrega `stopPrice` nativo?** — define se entrada+stop é 1 ou 2
  chamadas REST e como o rollback é implementado.
- **Webhook action realmente implementado?** — issue betrader #2 dizia "em
  implementação"; a leitura de código indicou "existe". Confirmar antes da F1.
- **Escopo do token `bht_`** — confirmar que dá acesso de escrita
  (automations/orders), não só leitura.

## Dependências externas (lado betrader, operador)

- betrader **#1** — auth de serviço (Bearer/API-key). Leitura de código indica
  implementado (`ServiceToken`, prefixo `bht_`). Operador já possui o token.
- betrader **#2** — action `WEBHOOK`/`NOTIFY_HTTP` no Beholder. Necessário para a F1.

(GitLab: `gitlab.com/fabiosiqueira/betrader-hydra-bot/-/work_items/1` e `/2`.)

## Fora de escopo (YAGNI no M1)

- Multi-par / portfolio rotation (F1+).
- Risk Gateway como serviço separado (F2).
- Dashboards Grafana (F3).
- Modo AUTO/PROD com mainnet (F3).
- Apostas direcionais sem edge validado (proibido por `bot.md` até backtest com
  edge >0 e >100 trades).
