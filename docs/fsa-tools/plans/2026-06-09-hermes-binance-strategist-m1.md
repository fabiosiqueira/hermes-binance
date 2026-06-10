# Plan — Estrategista Hermes-Binance M1 (DRY_RUN)

## Metadata

- **Generated:** 2026-06-09
- **Worktree:** required

## Context

Repo `~/dev/projetos/hermes/binance-project` (raiz git, hoje contém apenas o spec
`docs/superpowers/specs/2026-06-09-hermes-binance-strategist-design.md` e o
`hermes/.env` local não versionado). O plano cria o M1 inteiro espelhando o skeleton
do `~/dev/projetos/hermes/forex-project` (overlay Docker do engine
`ghcr.io/fabiosiqueira/hermes-engine` + data dir `hermes/`), com scripts Python em
`hermes/scripts/` e testes pytest na raiz. Executor: betrader-hydra (checkout
read-only em `~/dev/projetos/trading/betrader-hydra`, instância viva em
`http://betrader.fabiosiqueira.dev`).

## Baseline (current state)

```bash
ls hermes/scripts 2>/dev/null || echo "hermes/scripts não existe"
python -m pytest -q 2>&1 | tail -1   # "no tests ran" (exit 5)
ls Dockerfile 2>/dev/null || echo "sem Dockerfile/composes"
```

## Objective

M1 DRY_RUN end-to-end: ciclo estrategista agendado (candle 15m) — brief → LLM propõe
→ gate in-process valida contra dogmas → executa no betrader (entrada+stop atômico)
→ métricas/estado persistido. Ao final, homologar as work items #1 e #2 do
`gitlab.com/fabiosiqueira/betrader-hydra-bot`.

## Definition of Done (global)

Single verifiable command:

```bash
python -m pytest -q && docker compose -f hermes-compose.local.yml config -q >/dev/null && test -s docs/superpowers/specs/2026-06-09-betrader-api-verification.md && test -f hermes/cron/jobs.json && test -f hermes/dogmas.yaml && test -f hermes/SOUL.md
```

**Expected output:** linha final do pytest com `passed` (0 failed) e exit 0 do
comando composto.

## Policy (invariant)

- **betrader-hydra é READ-ONLY.** Nunca criar/editar/deletar qualquer arquivo em
  `~/dev/projetos/trading/betrader-hydra`. Leitura de código é permitida e esperada.
- **Instância viva: somente GET.** Nenhuma chamada de escrita (POST/PUT/DELETE) em
  `http://betrader.fabiosiqueira.dev` durante o plano. Escrita real só via testes
  mockados.
- **Segredos:** `hermes/.env` nunca é commitado nem logado (nem o valor de
  `BETRADER_TOKEN`). O worktree NÃO contém `hermes/.env` (untracked) — tasks que
  precisam do token leem do checkout principal:
  `/Users/fabiosiqueira/dev/projetos/hermes/binance-project/hermes/.env`.
- **Referências de estilo são read-only:** `forex-project/` e `defi-project/` podem
  ser lidos, nunca modificados.
- **Match de estilo forex:** comments PT-BR com termos técnicos em inglês, headers
  explicativos em Dockerfile/composes, mesma estrutura de data dir.
- **DI real; mocks só nas fronteiras de I/O** (HTTP betrader via `respx`, Redis via
  `fakeredis`). Jamais mockar módulos internos (`schemas`, `risk_engine`, etc.).
- **Testes junto com a implementação** em cada task (TDD: escreva o teste antes).
- Python: pydantic v2, httpx, type hints em todas as assinaturas públicas.
- Não tocar em arquivos fora dos listados na própria task.

## Dependency justification

- **Cluster 2 blockedBy Cluster 1:** Task 2.1 consome (a) o doc de verificação da
  Task 1.1 — os campos de `Brief` precisam ser deriváveis dos shapes reais dos
  endpoints; (b) o harness pytest + deps da Task 1.2 para rodar seus unit tests.
- **Cluster 3 blockedBy Cluster 2:** Tasks 3.1/3.2/3.3 importam os tipos pydantic de
  `hermes/scripts/schemas.py` (artefato da 2.1). A 3.2 também consome a decisão
  "entrada+stop em 1 ou 2 calls" do doc da 1.1.
- **Cluster 4 blockedBy Cluster 3:** Task 4.1 importa `risk_engine`,
  `betrader_client` e `observability` (artefatos 3.1/3.2/3.3).
- **Task 4.2 blockedBy 4.1:** o E2E exercita `strategist_cycle.py` pronto.
- **Cluster 5 blockedBy Cluster 4:** a homologação usa como evidência o doc da 1.1
  + a suíte de testes verde + o uso real do token — só existem com o M1 completo.
- Task 1.3 é independente: referencia contratos por NOME (estáveis no spec), sem
  import de código.

## Clusters

### Cluster 1 — Fundação

**Inter-cluster dependency:** none

#### Task 1.1: Spike de verificação da API betrader [sonnet]

**Files:**
- Create: `docs/superpowers/specs/2026-06-09-betrader-api-verification.md`

**Diagnosis:** O spec lista 4 riscos que mudam o design do client (shape de
`/api/market`, stopPrice nativo, escopo do token, webhook). O checkout local +
instância viva respondem todos sem chutar.

**Verification:** `test -s docs/superpowers/specs/2026-06-09-betrader-api-verification.md && [ "$(grep -cE '^## ' docs/superpowers/specs/2026-06-09-betrader-api-verification.md)" -ge 6 ]`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Sua única entrega é o documento
docs/superpowers/specs/2026-06-09-betrader-api-verification.md respondendo, com
evidência de código E de runtime, às perguntas abaixo sobre o betrader-hydra.

FONTES:
1. Código (READ-ONLY, NUNCA modifique nada lá):
   /Users/fabiosiqueira/dev/projetos/trading/betrader-hydra
   App Next.js; rotas em src/app/api/; âncoras já localizadas:
   - prisma/schema.prisma linha ~197: model OrderTemplate (tem stopPrice String?)
   - src/lib/service-token.ts (+ src/lib/__tests__/service-token.test.ts): tokens bht_
   - src/lib/beholder.ts + src/lib/__tests__/beholder-webhook.test.ts: action WEBHOOK
   - src/app/api/{market,indicators,orders,futures,exchange/balance,monitors,
     automations,beholder/memory,settings,tokens}/route.ts
2. Instância viva: http://betrader.fabiosiqueira.dev
   Token: leia BETRADER_TOKEN (e BETRADER_USER) de
   /Users/fabiosiqueira/dev/projetos/hermes/binance-project/hermes/.env
   (checkout principal — o worktree não tem esse arquivo).
   REGRA DURA: apenas requests GET. Jamais POST/PUT/DELETE na instância viva.
   REGRA DURA: nunca imprima/cole o valor do token no doc ou no output — refira-se
   a ele como $BETRADER_TOKEN. Descubra no código como o token é enviado
   (header Authorization: Bearer? outro header?) e use curl -sS.

O DOC deve ter exatamente estas seções (## ), cada uma com veredito + evidência
(trecho de código com path:linha e/ou JSON de resposta real, truncado):

## 1. Shape de /api/market (e fontes do Brief)
Shape real (campos, tipos) das respostas GET de: /api/market (com query params que
a rota aceitar — descubra na rota), /api/exchange/balance, /api/futures,
/api/beholder/memory, /api/automations/indexes, /api/indicators. Diga de quais
campos saem: candles, indicators, equity, balance, positions, used_leverage.

## 2. Entrada+stop: 1 ou 2 calls REST?
POST /api/orders aceita stopPrice/tipo STOP_MARKET + reduceOnly num único create?
Existe combinação OCO/atômica? Conclua: a invariante "entrada+SL como unidade"
exige 1 call ou 2 (MARKET + STOP_MARKET reduceOnly)? Como confirmar fill (campos
de status na resposta / GET /api/orders) e como fazer rollback (close imediato:
qual rota/método — ex.: DELETE /api/futures/{symbol}?)?

## 3. Escopo do token bht_
Pelo código de service-token.ts e middleware de auth: o token dá acesso de ESCRITA
(POST/PUT/DELETE em orders/automations/monitors)? Tem escopo/expiração? Valide na
instância com um GET autenticado (ex.: /api/automations) mostrando 200 vs 401 sem
token.

## 4. Action WEBHOOK do Beholder
beholder.ts + beholder-webhook.test.ts: a action WEBHOOK (webhookUrl,
webhookSecret HMAC) está implementada e coberta por teste? Veredito pra F1 e pra
homologação da work item #2 do GitLab.

## 5. isTestnet e modo DRY_RUN
Onde o flag user.isTestnet é configurado/lido (settings?). Algum endpoint GET
expõe isTestnet pro nosso adapter confirmar que está falando com testnet antes de
escrever? O usuário BETRADER_USER atual está com isTestnet=true?

## 6. Resumo executivo pro implementador
Tabela: decisão → consequência no betrader_client.py (ex.: "2 calls → rollback
emit→confirm→close"). Inclua a base URL confirmada e o header de auth exato.

CONSTRAINTS: não modifique nenhum outro arquivo do binance-project; não crie
scripts; só o doc. PT-BR, termos técnicos em inglês.

RETORNE quando `test -s docs/superpowers/specs/2026-06-09-betrader-api-verification.md && [ "$(grep -cE '^## ' docs/superpowers/specs/2026-06-09-betrader-api-verification.md)" -ge 6 ]` sair 0.
Retorne: resumo de 1 parágrafo por seção + paths/linhas das evidências-chave.
```

#### Task 1.2: Skeleton do repo + harness de testes [sonnet]

**Files:**
- Create: `Dockerfile`, `hermes-compose.local.yml`, `hermes-coolify.yml`,
  `.env.example`, `.gitignore`, `.dockerignore`, `hermes/.gitignore`,
  `pyproject.toml`, `tests/unit/test_smoke.py`

**Diagnosis:** O forex-project é o template aprovado (overlay do engine, dois targets
local/vps, data dir bind-mount local / baked na VPS). Basta remover mt5/mt5-mcp,
realocar portas e acrescentar o harness Python que o forex não tem.

**Verification:** `docker compose -f hermes-compose.local.yml config -q && python -m pytest tests/unit/test_smoke.py -q`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Crie o skeleton de infra + harness de
testes ESPELHANDO o forex-project (leia cada arquivo de referência antes de criar o
correspondente — match de estilo exato, comments PT-BR):

REFERÊNCIAS (READ-ONLY): /Users/fabiosiqueira/dev/projetos/hermes/forex-project/
{Dockerfile, hermes-compose.local.yml, hermes-coolify.yml, .gitignore,
.dockerignore, hermes/.gitignore}

CRIE:
1. Dockerfile — igual ao forex (ARG HERMES_ENGINE_TAG, FROM
   ghcr.io/fabiosiqueira/hermes-engine, target local com uv pip install redis +
   "mcp[cli]" + redis-tools, target vps com COPY hermes/ /opt/data/ + chown -R
   hermes:hermes). ADICIONE ao uv pip install: pydantic httpx prometheus-client
   pyyaml (deps dos scripts do estrategista). Ajuste comments: projeto
   binance-project, executor betrader via REST (sem MCP de trading).
2. hermes-compose.local.yml — serviços gateway, dashboard, redis APENAS (sem
   mt5/mt5-mcp). Network: binance. Volume redis: binance-redis-data. Portas host
   (não colidem com defi 8642/9119/9466/6379 nem forex 8643/9120/9467/6380),
   seguindo o padrão same:same que forex e defi usam: gateway 8644:8644, metrics
   9468:9468, dashboard 9121:9121, redis 6381:6379. Se descobrir que alguma porta
   interna do engine é fixa (ex.: gateway sempre 8643), ajuste só o lado interno
   do mapping e documente no comment. Comment no topo documentando as portas como
   o forex faz. env_file: hermes/.env; environment: HERMES_DATA_DIR=/opt/data,
   REDIS_HOST=redis, REDIS_PORT=6379.
3. hermes-coolify.yml — como o do forex sem mt5: gateway (target vps, volume
   hermes-data:/opt/data) + redis, tudo interno sem portas. Env vars do gateway:
   MINIMAX_API_KEY, OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN,
   TELEGRAM_ALLOWED_USERS, TELEGRAM_HOME_CHANNEL, BETRADER_BASE_URL,
   BETRADER_TOKEN, BETRADER_USER, EXECUTION_MODE, EMERGENCY_STOP, INITIAL_EQUITY.
4. .env.example — placeholders (NUNCA valores reais):
   BETRADER_BASE_URL=http://betrader.fabiosiqueira.dev
   BETRADER_TOKEN=bht_xxx / BETRADER_USER=user@example.com
   EXECUTION_MODE=DRY_RUN / EMERGENCY_STOP=false / INITIAL_EQUITY=1000
   SYMBOL=BTCUSDT / TIMEFRAME=15m
   + MINIMAX_API_KEY, OPENROUTER_API_KEY, TELEGRAM_* com placeholder.
   Comment de cabeçalho: copiar pra hermes/.env (gitignored).
5. .gitignore e .dockerignore — copie do forex adaptando comments
   (forex-project→binance-project; .dockerignore: troque referências hermes/ que
   não existirem ainda mantendo a lista — é defesa em profundidade).
6. hermes/.gitignore — copie do forex e ADICIONE seção "--- runtime do
   estrategista ---" com: workspace/
7. pyproject.toml — [project] name "binance-strategist", requires-python >=3.11,
   dependencies: pydantic>=2, httpx, prometheus-client, redis, pyyaml.
   [project.optional-dependencies] test: pytest, respx, fakeredis.
   [tool.pytest.ini_options]: testpaths=["tests"], pythonpath=["hermes/scripts"].
8. tests/unit/test_smoke.py — único teste: importa pydantic, httpx,
   prometheus_client, redis, yaml, respx, fakeredis e assert True (prova o
   harness).

CONSTRAINTS: não crie hermes/SOUL.md, AGENTS.md, config.yaml, scripts/ (outras
tasks). Não commite. Nunca coloque valores reais de token em arquivo nenhum.

RETORNE quando `docker compose -f hermes-compose.local.yml config -q && python -m
pytest tests/unit/test_smoke.py -q` sair 0 (instale as deps de teste num venv local
se precisar: python3 -m venv .venv && .venv/bin/pip install -e '.[test]' — nesse
caso reporte o caminho do venv usado).
Retorne: lista de arquivos criados + portas alocadas + output do pytest.
```

#### Task 1.3: Persona do estrategista [sonnet]

**Files:**
- Create: `hermes/SOUL.md`, `hermes/AGENTS.md`, `hermes/CLAUDE.md`,
  `hermes/config.yaml`

**Diagnosis:** O data dir do forex é o template (SOUL=persona FOX, AGENTS=contexto
operacional com invariante Redis-via-env, CLAUDE=guia de edição, config=engine).
Reescrita para estrategista Binance: sem MCP, executor betrader via contrato de
dados, dogmas invioláveis.

**Verification:** `test -f hermes/SOUL.md && test -f hermes/AGENTS.md && test -f hermes/CLAUDE.md && python -c "import yaml; yaml.safe_load(open('hermes/config.yaml'))" && grep -qi betrader hermes/SOUL.md && ! grep -A2 '^mcp_servers:' hermes/config.yaml | grep -q 'url:'`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Crie o data dir de persona do
estrategista Hermes-Binance, espelhando o forex-project (LEIA os 4 arquivos de
referência antes — match de tom, estrutura de seções e estilo PT-BR):

REFERÊNCIAS (READ-ONLY): /Users/fabiosiqueira/dev/projetos/hermes/forex-project/
hermes/{SOUL.md, AGENTS.md, CLAUDE.md, config.yaml}

CONTEXTO DE DESIGN (do spec docs/superpowers/specs/
2026-06-09-hermes-binance-strategist-design.md — leia-o): o agente é ESTRATEGISTA,
não trader-no-loop. Lê um Brief (JSON tipado), decide/adapta estratégia dentro de
dogmas de risco invioláveis (hermes/dogmas.yaml) e escreve um StrategyProposal
(JSON tipado). Quem executa ordem tick-a-tick é o betrader-hydra via REST — o
agente NUNCA chama a Binance direto e NUNCA burla o gate (risk_engine.py). Toda
entrada carrega stop loss obrigatório. Cadência: cron a cada fechamento de candle
15m de BTCUSDT (M1). Modo: EXECUTION_MODE=DRY_RUN default (testnet). Sem MCP.

CRIE:
1. hermes/SOUL.md — persona "HAWK — Estrategista Binance Futures via betrader".
   Mesmas seções do FOX (Quem eu sou / Missão / Style / O que evitar / Postura
   técnica / Princípios / Limites duros), reescritas pro estrategista:
   - Limites duros DEVEM incluir: nunca emitir ordem fora do ciclo
     brief→proposal→gate→execute; nunca propor entrada sem stop_loss; nunca editar
     dogmas.yaml, risk_engine.py ou config de infra; proposta rejeitada pelo gate
     → acatar e registrar, jamais re-submeter igual; sem edge claro no brief →
     não propor entrada (não operar é posição válida); nunca expor BETRADER_TOKEN.
   - Princípios: catálogo tipado + composição (escolhe/parametriza indicadores do
     catálogo do brief, não inventa indicador); downside primeiro; decisões com
     reasoning registrado; Redis sempre via REDIS_HOST/REDIS_PORT do env.
2. hermes/AGENTS.md — adapte o do forex: layout do data dir (scripts/ agora é
   ferramenta DETERMINÍSTICA mantida pelo repo, não criada livremente pelo
   agente), invariante Redis idêntico, seção "Serviços que eu uso" trocando MCP
   por betrader REST (BETRADER_BASE_URL/BETRADER_TOKEN via env; sem MCP), e seção
   nova "## Ciclo do estrategista (cron 15m)" documentando o contrato:
   a) `python scripts/strategist_cycle.py brief` → escreve workspace/brief.json e
      imprime o path; b) eu leio o brief, raciocino e escrevo
      workspace/proposal.json conforme o schema StrategyProposal (reasoning,
      entries[] com stop_loss OBRIGATÓRIO, automations[], teardown[]);
   c) `python scripts/strategist_cycle.py execute workspace/proposal.json` →
      gate valida contra dogmas e executa no betrader; d) leio o resultado JSON
      impresso (executed/reason) e reporto no canal se relevante. Os schemas estão
      em scripts/schemas.py; dogmas em dogmas.yaml (read-only pra mim).
3. hermes/CLAUDE.md — adapte o do forex (guia pra IA de coding NO REPO, não
   runtime): o que o repo é, "ler sempre antes" (SOUL, AGENTS, config, spec,
   dogmas), os 4 princípios de edição invioláveis (copie literais do forex),
   guardrails (infra só com aprovação; betrader-hydra read-only; scripts/ do
   estrategista são código de produção testado — não é pasta livre do agente;
   segredos só via .env gitignored).
4. hermes/config.yaml — copie o do forex e aplique o MENOR diff: (a) remova o
   bloco mcp_servers (deixe `mcp_servers: {}`), (b) personality "fox" →
   "hawk" com texto: assistente estrategista de Binance futures do Fábio, PT-BR
   direto, substância sobre filler, admite incerteza com ⚠️, opera só via ciclo
   brief→proposal→gate, (c) display.personality: hawk. NADA mais muda.

CONSTRAINTS: não crie scripts/, dogmas.yaml, cron/ (outras tasks). Não toque em
Dockerfile/composes. Persona "HAWK" pode mencionar dogmas.yaml e scripts ainda não
criados — os nomes são contrato estável do spec.

RETORNE quando `test -f hermes/SOUL.md && test -f hermes/AGENTS.md && test -f
hermes/CLAUDE.md && python -c "import yaml;
yaml.safe_load(open('hermes/config.yaml'))" && grep -qi betrader hermes/SOUL.md`
sair 0.
Retorne: lista de arquivos + resumo de 2 linhas das escolhas de persona.
```

### Cluster 2 — Contratos

**Inter-cluster dependency:** depends on Cluster 1

#### Task 2.1: schemas.py + dogmas.yaml + unit tests [opus]

**Files:**
- Create: `hermes/scripts/schemas.py`, `hermes/dogmas.yaml`,
  `tests/unit/test_schemas.py`

**Diagnosis:** Os 3 contratos pydantic são a pedra angular — `stop_loss` obrigatório
por construção é o primeiro nível do gate. Campos do `Brief` devem mapear aos shapes
verificados na Task 1.1.

**Verification:** `python -m pytest tests/unit/test_schemas.py -q`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Crie os contratos de dados do
estrategista (pydantic v2) + dogmas exemplo + unit tests. TDD: escreva os testes
primeiro.

LEIA ANTES: docs/superpowers/specs/2026-06-09-hermes-binance-strategist-design.md
(seção "Contratos de dados") e docs/superpowers/specs/
2026-06-09-betrader-api-verification.md (shapes reais — seção 1; ajuste os campos
de Brief.market/portfolio ao que é REALMENTE derivável dos endpoints, documentando
no docstring de cada campo de onde ele vem).

CRIE hermes/scripts/schemas.py com (nomes EXATOS — outros módulos importam):
- ExecutionMode (StrEnum): DRY_RUN, HOM, PROD.
- IndicatorSpec: name, params (dict), description opcional — item do catálogo.
- Candle: open_time, open, high, low, close, volume (tipos conforme shape real).
- MarketState: symbol, timeframe, candles list[Candle], indicators dict[str, float|None].
- Position: symbol, side, entry_price, quantity, unrealized_pnl, leverage.
- Portfolio: equity, balance, positions list[Position], used_leverage.
- RiskState: daily_pnl, drawdown_pct, equity_curve_ref (str, chave Redis).
- ActiveItem: id, kind ("automation"|"order"), summary, performance opcional.
- Brief: timestamp, mode ExecutionMode, catalog list[IndicatorSpec],
  market MarketState, portfolio Portfolio, risk_state RiskState,
  active list[ActiveItem].
- EntryOrder: symbol, side ("BUY"|"SELL"), sizing_pct (float >0 ≤100, % do equity),
  order_type ("MARKET"|"LIMIT"), limit_price opcional (obrigatório se LIMIT —
  model_validator), stop_loss float OBRIGATÓRIO (>0), take_profit opcional,
  leverage int ≥1. Validator: side BUY → stop_loss < limit_price quando LIMIT
  (e o inverso pra SELL); para MARKET valida só positividade (preço corrente não
  está no schema).
- AutomationSpec: name, condition (str, validada por regex
  ^MEMORY\['[A-Z0-9]+:[A-Za-z0-9_]+'\]\s*(>|<|>=|<=|===|!=)\s*-?[0-9.]+$ — formato
  do Beholder), action (dict livre, documentado), schedule opcional.
- StrategyProposal: reasoning str (min_length=1), entries list[EntryOrder] = [],
  automations list[AutomationSpec] = [], teardown list[str] = [].
- Dogmas: max_leverage int, max_position_pct_equity float,
  max_daily_drawdown_pct float, mandatory_stop_loss bool = True (Literal[True] —
  não pode ser desligado), min_stop_distance_pct float, allowed_symbols list[str].
- load_dogmas(path) -> Dogmas (yaml.safe_load + validação).

CRIE hermes/dogmas.yaml — constituição exemplo M1, com comment de cabeçalho
"preenchido pelo operador": max_leverage: 5, max_position_pct_equity: 10,
max_daily_drawdown_pct: 3, mandatory_stop_loss: true, min_stop_distance_pct: 0.5,
allowed_symbols: [BTCUSDT].

CRIE tests/unit/test_schemas.py cobrindo no mínimo: proposta sem stop_loss é
ValidationError (o teste central do design); LIMIT sem limit_price falha;
sizing_pct fora de (0,100] falha; condition de automation fora do formato MEMORY
falha; mandatory_stop_loss: false falha; load_dogmas carrega hermes/dogmas.yaml;
round-trip model_dump_json/model_validate_json de um Brief e um StrategyProposal
completos.

CONSTRAINTS: só os 3 arquivos listados. Não crie risk_engine/client (outras
tasks). Estilo: PT-BR nos docstrings/comments, type hints completos.

RETORNE quando `python -m pytest tests/unit/test_schemas.py -q` sair 0.
Retorne: assinaturas públicas criadas (nome: campos) + contagem de testes.
```

### Cluster 3 — Core

**Inter-cluster dependency:** depends on Cluster 2

#### Task 3.1: risk_engine.py + testes por dogma [opus] +reviewer

**Files:**
- Create: `hermes/scripts/risk_engine.py`, `tests/unit/test_risk_engine.py`

**Diagnosis:** Gate determinístico in-process (M1) que vira serviço na F2 — por isso
é função pura sobre os contratos, sem I/O nem rede. É a peça de maior criticidade
financeira junto com o client.

**Verification:** `python -m pytest tests/unit/test_risk_engine.py -q`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Crie o gate de risco determinístico.
TDD: um teste por dogma ANTES da implementação.

LEIA ANTES: hermes/scripts/schemas.py (importe Brief, StrategyProposal, Dogmas,
EntryOrder de `schemas` — pythonpath já aponta pra hermes/scripts),
docs/superpowers/specs/2026-06-09-hermes-binance-strategist-design.md (seções
"Dogmas", "Invariante central", "Ciclo do estrategista" passo 4).

CRIE hermes/scripts/risk_engine.py:
- @dataclass GateResult: ok: bool, reason: str | None = None,
  violations: list[str] = field(default_factory=list).
- check_emergency_stop() -> bool — lê EMERGENCY_STOP do os.environ ("true"/"1"
  case-insensitive = ativado). SEM raise.
- validate(proposal: StrategyProposal, dogmas: Dogmas, brief: Brief) ->
  GateResult. FUNÇÃO PURA: sem I/O, sem env (emergency_stop é checado pelo
  caller no início do ciclo), sem mutação dos inputs. Rejeita acumulando TODAS as
  violações (não fail-fast — o reasoning de rejeição vai pro LLM aprender):
  1. symbol de cada entry fora de dogmas.allowed_symbols;
  2. leverage > dogmas.max_leverage;
  3. sizing_pct > dogmas.max_position_pct_equity — E TAMBÉM exposição agregada:
     soma de sizing_pct das entries + posições abertas do brief.portfolio
     (cada posição aberta convertida pra % do equity corrente) não pode exceder
     max_position_pct_equity;
  4. brief.risk_state.drawdown_pct >= dogmas.max_daily_drawdown_pct → rejeita
     QUALQUER entrada nova (teardown/automations de saída continuam permitidos —
     reduzir risco nunca é bloqueado);
  5. distância do stop: |entry_ref - stop_loss| / entry_ref * 100 <
     dogmas.min_stop_distance_pct → rejeita (entry_ref = limit_price se LIMIT,
     senão último close de brief.market.candles[-1].close); stop do lado errado
     (BUY com stop >= entry_ref, SELL com stop <= entry_ref) → rejeita;
  6. proposal sem entries, só automations/teardown → válida (gestão pura).
  reason = "; ".join(violations) quando rejeitada.

CRIE tests/unit/test_risk_engine.py: fixture de Brief/Dogmas mínimos válidos +
um teste isolado POR dogma acima (caso passa + caso viola, boundary exato: ==
teto passa ou falha? defina: <= teto passa, > viola; drawdown >= viola), exposição
agregada com posição aberta preexistente, proposta de gestão pura passa com
drawdown estourado, emergency_stop via monkeypatch.setenv. Sem mocks de schemas
(use objetos reais).

CONSTRAINTS: só os 2 arquivos. Não modifique schemas.py — se faltar campo, PARE e
reporte em vez de alterar o contrato.

RETORNE quando `python -m pytest tests/unit/test_risk_engine.py -q` sair 0.
Retorne: lista de regras implementadas + decisões de boundary + contagem de testes.
```

#### Task 3.2: betrader_client.py + testes (mock só na fronteira HTTP) [opus] +reviewer

**Files:**
- Create: `hermes/scripts/betrader_client.py`, `tests/unit/test_betrader_client.py`

**Diagnosis:** Adapter REST que materializa a invariante central do spec:
entrada+stop como unidade (emit → confirm → rollback). O número de calls e a rota de
rollback vêm do doc da Task 1.1.

**Verification:** `python -m pytest tests/unit/test_betrader_client.py -q`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Crie o client REST do betrader-hydra.
TDD com respx (mock APENAS na fronteira HTTP — schemas e lógica interna reais).

LEIA ANTES (obrigatório): docs/superpowers/specs/
2026-06-09-betrader-api-verification.md — TODO o design de chamadas (rotas, header
de auth, shape de respostas, 1-vs-2 calls pra entrada+stop, rota de rollback,
endpoint de isTestnet) vem DESTE doc, não de suposição. hermes/scripts/schemas.py
(importe Brief, StrategyProposal, EntryOrder, ExecutionMode etc. de `schemas`).
Spec de design: docs/superpowers/specs/2026-06-09-hermes-binance-strategist-design.md
(tabela "Superfície da API betrader", "Invariante central").

CRIE hermes/scripts/betrader_client.py:
- class BetraderError(Exception) com attr type: str (descritivo, ex.:
  "betrader_http_5xx", "stop_confirm_timeout", "rollback_failed").
- class BetraderClient:
  - __init__(base_url: str, token: str, *, on_error: Callable[[str], None] | None
    = None, timeout: float = 10.0) — httpx.Client interno com header de auth
    EXATO documentado no doc de verificação. on_error(type) é chamado em todo
    catch de I/O antes de re-raise (DI: observability pluga aqui — NÃO importe
    observability).
  - from_env() classmethod — BETRADER_BASE_URL, BETRADER_TOKEN do os.environ
    (raise se ausentes; NUNCA logar o token).
  - ensure_monitor(symbol, timeframe) — GET /api/monitors; se não existe monitor
    ativo do par/timeframe, POST + start (rotas conforme doc). Idempotente.
  - fetch_brief(symbol, timeframe, mode: ExecutionMode, risk_state: RiskState)
    -> Brief — monta dos GETs: /api/indicators (catalog), /api/market (candles/
    indicators), /api/exchange/balance + /api/futures (portfolio),
    /api/beholder/memory + /api/automations/indexes (indicators correntes),
    automations/orders ativos (active). risk_state vem do caller (observability é
    quem conhece a equity-curve).
  - assert_testnet() — em DRY_RUN, confirma user.isTestnet=true ANTES de qualquer
    escrita (endpoint conforme doc seção 5); falha → BetraderError
    type="not_testnet_in_dry_run".
  - place_entry_with_stop(entry: EntryOrder, equity: float) -> dict — INVARIANTE
    CENTRAL: (a) converte sizing_pct → quantity usando equity e preço de
    referência (regra de arredondamento/precision conforme doc); (b) emite
    entrada; (c) emite/confirma o STOP (1 ou 2 calls conforme doc); (d) se o stop
    NÃO confirmar: rollback = close imediato da posição (rota conforme doc) e
    raise BetraderError type="entry_rolled_back_no_stop". Retorna dict com ids +
    status. Configura leverage/marginType antes se a entry pedir (PUT
    /api/futures/{symbol}).
  - install_automations(automations: list[AutomationSpec]) -> list[str] (ids).
  - teardown(ids: list[str]) — cancela automations/ordens por id; idempotente
    (404 = já removido, não é erro).
  - close() / context manager.

CRIE tests/unit/test_betrader_client.py (respx): fetch_brief monta Brief válido a
partir de payloads reais (copie payloads de exemplo do doc de verificação como
fixtures); ensure_monitor idempotente (já existe → nenhum POST); CAMINHO CRÍTICO:
place_entry_with_stop com stop falhando (HTTP 500 ou status de rejeição) → assert
que houve a call de close (rollback) E BetraderError type
"entry_rolled_back_no_stop"; stop ok → sem rollback; rollback TAMBÉM falha →
BetraderError type="rollback_failed" (estado inconsistente é reportado, nunca
engolido); assert_testnet falha em DRY_RUN com isTestnet=false; on_error recebe
type em cada falha de I/O; token JAMAIS aparece em repr/str/logs (teste explícito).

CONSTRAINTS: só os 2 arquivos. NENHUMA chamada de rede real nos testes (respx
intercepta tudo; assert_all_called). Não modifique schemas.py — contrato faltando
→ PARE e reporte. ~/dev/projetos/trading/betrader-hydra é read-only (consulta).

RETORNE quando `python -m pytest tests/unit/test_betrader_client.py -q` sair 0.
Retorne: decisão 1-vs-2 calls adotada (citando a seção do doc), assinaturas
públicas, contagem de testes.
```

#### Task 3.3: observability.py + testes [sonnet]

**Files:**
- Create: `hermes/scripts/observability.py`, `tests/unit/test_observability.py`

**Diagnosis:** Conformidade `bot.md`: métricas Prometheus sobre equity-curve
(`initialEquity + cumPnL`, nunca PnL isolado), estado financeiro que sobrevive
restart (Redis), `/health` + `/metrics` desde M1.

**Verification:** `python -m pytest tests/unit/test_observability.py -q`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Crie o módulo de observabilidade +
estado financeiro persistido. TDD com fakeredis (mock só na fronteira Redis).

LEIA ANTES: hermes/scripts/schemas.py (RiskState, StrategyProposal),
docs/superpowers/specs/2026-06-09-hermes-binance-strategist-design.md (seção
"Conformidade com bot.md").

CRIE hermes/scripts/observability.py:
- Redis SEMPRE via os.environ REDIS_HOST/REDIS_PORT (default 127.0.0.1:6379) —
  invariante do projeto; nunca hardcode host.
- Namespace de chaves: binance:strategist:* .
- class FinancialState: initial_equity (de INITIAL_EQUITY env na primeira
  inicialização, depois SEMPRE do Redis — nunca re-lê env se já persistido),
  cum_pnl, peak_equity, daily_pnl + daily_date (reset em virada de dia UTC),
  wins, losses. Propriedades: equity = initial_equity + cum_pnl; drawdown_pct =
  (peak_equity - equity) / peak_equity * 100 (0 se peak==0); win_rate.
  load(redis) / persist(redis) (hash JSON numa chave; persist é atômico).
  record_trade(pnl: float) atualiza cum_pnl, peak, daily, wins/losses.
  to_risk_state() -> RiskState (pro brief).
- Métricas prometheus_client (registry injetável pra testes): counters
  strategist_wins_total, strategist_losses_total, strategist_cycles_total,
  strategist_proposals_rejected_total{reason}, strategist_errors_total{type};
  gauges strategist_pnl_usd, strategist_equity_usd, strategist_max_drawdown_pct,
  strategist_win_rate. restore_metrics(state) re-popula gauges/counters do estado
  persistido (resiliência bot.md: métricas restauradas pós-restart).
- record_error(type: str) — alvo do on_error do BetraderClient.
- record_cycle() — incrementa strategist_cycles_total (chamado 1x por execução
  do ciclo).
- record_decision(proposal_summary: dict, gate_ok: bool, reason: str | None,
  redis) — XADD num stream binance:strategist:decisions (MAXLEN ~1000) com
  timestamp, reasoning truncado, gate result (auditoria de decisões).
- start_servers(port: int = 9468) — /metrics via prometheus_client
  start_http_server + /health (HTTP 200 {"status":"ok"}) num handler simples na
  porta 9469 (ou rota única se simplificar; documente a escolha).

CRIE tests/unit/test_observability.py (fakeredis.FakeRedis): estado sobrevive
"restart" (persist → novo FinancialState.load → valores iguais); initial_equity
não é re-lido do env quando já persistido (mude env e recarregue);
record_trade atualiza equity-curve, MaxDD calculado sobre equity (não PnL
isolado: sequência +100, -50, -100 → peak e drawdown corretos); daily_pnl reseta
na virada de dia (injete clock/now como parâmetro — não mocke time global);
restore_metrics re-popula; record_decision escreve no stream; win_rate com 0
trades não divide por zero.

CONSTRAINTS: só os 2 arquivos. Não importe betrader_client nem risk_engine. Não
modifique schemas.py.

RETORNE quando `python -m pytest tests/unit/test_observability.py -q` sair 0.
Retorne: chaves Redis usadas, lista de métricas, contagem de testes.
```

### Cluster 4 — Integração

**Inter-cluster dependency:** depends on Cluster 3

#### Task 4.1: strategist_cycle.py + cron 15m + testes de integração [opus] +reviewer

**Files:**
- Create: `hermes/scripts/strategist_cycle.py`, `hermes/cron/jobs.json`,
  `tests/integration/test_cycle.py`

**Diagnosis:** Orquestra os módulos no contrato de duas metades (brief/execute) que o
agente LLM consome via terminal — é assim que "Hermes lê Brief, escreve
StrategyProposal" sem MCP. Cron agentic a cada candle 15m.

**Verification:** `python -m pytest tests/integration -q && python -c "import json; json.load(open('hermes/cron/jobs.json'))"`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Crie o ciclo do estrategista (CLI em
duas metades) + o cron job do Hermes + testes de integração. TDD.

LEIA ANTES: hermes/scripts/{schemas,risk_engine,betrader_client,observability}.py
(você integra os 4 — NÃO os modifique), hermes/AGENTS.md (seção "Ciclo do
estrategista" já documenta o contrato das duas metades — siga-o exatamente),
docs/superpowers/specs/2026-06-09-hermes-binance-strategist-design.md (seção
"Ciclo do estrategista"), e o formato de jobs.json em
/Users/fabiosiqueira/dev/projetos/hermes/defi-project/hermes/cron/jobs.json
(READ-ONLY, referência de schema).

CRIE hermes/scripts/strategist_cycle.py — CLI argparse com 2 subcomandos:
1. `brief`: lê env (SYMBOL default BTCUSDT, TIMEFRAME default 15m,
   EXECUTION_MODE default DRY_RUN); BetraderClient.from_env();
   ensure_monitor(symbol, timeframe); FinancialState.load(redis) →
   to_risk_state(); fetch_brief(...); escreve workspace/brief.json
   (model_dump_json indent=2, mkdir -p workspace) e imprime o path absoluto no
   stdout (única saída).
2. `execute <proposal.json>`: ordem EXATA do spec:
   a) check_emergency_stop() → se ativo, imprime
      {"executed": false, "reason": "emergency_stop"} e exit 0 (SEM throw);
   b) carrega StrategyProposal do arquivo (ValidationError → imprime
      {"executed": false, "reason": "invalid_proposal", "detail": ...} exit 0 —
      o agente lê e corrige; nunca traceback cru);
   c) recarrega o brief de workspace/brief.json + dogmas via load_dogmas
      ("hermes/dogmas.yaml" relativo ao data dir — use path relativo ao próprio
      script: Path(__file__).parent.parent / "dogmas.yaml");
   d) risk_engine.validate(...) → rejeitado: record_decision(gate_ok=False),
      métrica proposals_rejected{reason}, imprime {"executed": false,
      "reason": "gate_rejected", "violations": [...]} exit 0;
   e) aprovado: em DRY_RUN chama client.assert_testnet(); executa na ordem
      teardown → entries (place_entry_with_stop por entry; falha de uma entry NÃO
      aborta as automations, mas é coletada) → install_automations;
   f) observability: record_decision(gate_ok=True), record_cycle, persist do
      FinancialState ANTES de imprimir o resultado (integridade bot.md);
   g) imprime JSON resumo {"executed": true, "orders": [...], "automations":
      [...], "errors": [...]}.
   Todo catch de I/O: record_error(type) e segue o contrato JSON (nunca
   traceback no stdout — o consumidor é o agente LLM).

CRIE hermes/cron/jobs.json — siga o schema do exemplo defi (campos id/name/
prompt/script/no_agent/schedule/...). UM job: id estável qualquer (12 hex), name
"strategist-candle-15m", schedule cron "*/15 * * * *", enabled true, deliver
"local", no_agent false, script null, prompt (PT-BR, conciso) instruindo o
agente: "Ciclo do estrategista (candle 15m fechou): 1) rode `python
scripts/strategist_cycle.py brief`; 2) leia o workspace/brief.json, analise
mercado/posições/risk_state e escreva workspace/proposal.json no schema
StrategyProposal (scripts/schemas.py) — toda entry exige stop_loss; sem edge
claro, proposta vazia com reasoning é válida; 3) rode `python
scripts/strategist_cycle.py execute workspace/proposal.json`; 4) leia o JSON de
resultado; gate_rejected → registre o motivo na memória e NÃO re-submeta igual;
reporte no canal só se houve execução ou erro." Campos de runtime (next_run_at
etc.) deixe null/zerados como no exemplo.

CRIE tests/integration/test_cycle.py — ciclo completo com respx (HTTP betrader) +
fakeredis + monkeypatch de env; invoque os subcomandos via função main(argv)
importável (capsys pro stdout JSON): happy path DRY_RUN (brief gerado válido →
proposal fixture aprovada → ordem+stop emitidos → estado persistido → métricas);
gate_rejected (leverage estourado) → JSON com violations e nenhuma call de
escrita HTTP; emergency_stop → {"executed": false, "reason": "emergency_stop"} e
NENHUMA call HTTP; proposal inválida (sem stop_loss) → reason
"invalid_proposal"; rollback do client propaga como errors[] no resumo com estado
ainda persistido.

CONSTRAINTS: só os 3 arquivos. Não modifique os módulos do Cluster 3 nem
schemas.py — incompatibilidade de interface → PARE e reporte qual assinatura
faltou.

RETORNE quando `python -m pytest tests/integration -q && python -c "import json;
json.load(open('hermes/cron/jobs.json'))"` sair 0.
Retorne: contrato stdout dos 2 subcomandos + contagem de testes.
```

#### Task 4.2: E2E lifecycle DRY_RUN [sonnet]

**Intra-cluster dependency:** 4.1

**Files:**
- Create: `tests/e2e/test_lifecycle_dry_run.py`

**Diagnosis:** Critério `bot.md`: E2E de lifecycle completo (entrada → ciclo → saída
→ métricas) em modo DRY_RUN, mocks só nas fronteiras — prova o M1 ponta a ponta sem
betrader real.

**Verification:** `python -m pytest tests/e2e -q`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Crie o E2E de lifecycle DRY_RUN.

LEIA ANTES: hermes/scripts/strategist_cycle.py (contrato dos subcomandos brief/
execute e shape do JSON de resultado), tests/integration/test_cycle.py (fixtures
respx/fakeredis existentes — REUSE o estilo e helpers; se valer extrair fixture
comum para tests/conftest.py, pode criar/editar esse arquivo),
hermes/scripts/observability.py (chaves Redis e métricas).

CRIE tests/e2e/test_lifecycle_dry_run.py — narrativa de DIAS de operação em
DRY_RUN, mocks SÓ nas fronteiras (respx p/ HTTP betrader, fakeredis, env via
monkeypatch). Cenário mínimo:
1. Ciclo 1: brief → proposal com 1 entrada BTCUSDT (stop_loss válido) → aprovada
   → entrada+stop emitidos (respx valida payloads, incluindo reduceOnly do stop
   conforme o client) → estado persistido.
2. Ciclo 2: brief reflete posição aberta; proposal de gestão (automation de
   trailing/saída) → instalada.
3. Saída: simule fechamento com lucro (payload de posição fechada/ordem filled na
   fronteira HTTP) → record_trade → win contabilizado, equity sobe.
4. Ciclo 3 com perda → equity cai; asserts de integridade financeira: equity ==
   initial + cum_pnl; max_drawdown calculado sobre a equity-curve (verifique o
   valor exato da sequência que você simulou); win_rate correto.
5. RESTART: novo FinancialState.load + restore_metrics do MESMO fakeredis →
   estado e métricas idênticos (resiliência bot.md).
6. Kill switch: EMERGENCY_STOP=true → ciclo retorna {"executed": false, "reason":
   "emergency_stop"} sem NENHUMA call HTTP de escrita.
Asserts sempre no comportamento observável (JSON stdout, calls respx, valores em
fakeredis, gauges do registry) — nunca em internals privados.

CONSTRAINTS: crie só o arquivo do teste (+ tests/conftest.py se extrair fixtures).
NÃO modifique nenhum módulo de hermes/scripts/ — bug encontrado → PARE e reporte
com repro mínimo em vez de "consertar" silenciosamente.

RETORNE quando `python -m pytest tests/e2e -q` sair 0 (e a suíte inteira
`python -m pytest -q` continuar verde).
Retorne: narrativa coberta + contagem de asserts de integridade financeira.
```

### Cluster 5 — Homologação GitLab

**Inter-cluster dependency:** depends on Cluster 4

#### Task 5.1: Homologar work items betrader #1 e #2 [sonnet]

**Files:**
- Create: nenhum (side effect: comments/estado nas work items do GitLab)

**Diagnosis:** O operador pediu homologação formal das duas dependências externas do
spec: #1 (auth de serviço bht_) e #2 (action WEBHOOK). Evidência vem do doc da 1.1 +
testes do M1. Re-fechar se validado, re-abrir com gap se não.

**Verification:** `glab api projects/fabiosiqueira%2Fbetrader-hydra-bot/issues/1/notes 2>/dev/null | grep -q "Homologação M1" && glab api projects/fabiosiqueira%2Fbetrader-hydra-bot/issues/2/notes 2>/dev/null | grep -q "Homologação M1"`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (worktree). Homologue as work items #1 e #2 de
gitlab.com/fabiosiqueira/betrader-hydra-bot (glab CLI já autenticado como
fabiosiqueira).

EVIDÊNCIAS (leia antes):
- docs/superpowers/specs/2026-06-09-betrader-api-verification.md — seção 3 (token
  bht_: escopo de escrita validado em runtime) e seção 4 (action WEBHOOK:
  implementação + cobertura de teste em src/lib/beholder.ts).
- Suíte do M1: rode `python -m pytest -q` e capture o resultado (o
  betrader_client usa o auth de serviço em todos os writes mockados com o header
  real).

PROCEDIMENTO por item (#1 = auth de serviço; #2 = action WEBHOOK):
1. Estado atual: `glab api projects/fabiosiqueira%2Fbetrader-hydra-bot/issues/<N>`
   (se 404 — work item pode não ser issue REST — use GraphQL:
   `glab api graphql -f query='{ workspace: project(fullPath:
   "fabiosiqueira/betrader-hydra-bot") { workItems(iid: "<N>") { nodes { id title
   state } } } }'` e a mutation workItemUpdate/createNote correspondente).
2. Veredito: VALIDADO se a evidência do doc + testes confirma a entrega; senão
   NÃO VALIDADO com o gap específico.
3. Comment "## Homologação M1 (hermes binance-project)" em PT-BR com: veredito,
   evidência objetiva (path:linha do código betrader citado no doc de
   verificação, resultado da chamada autenticada GET, suíte pytest verde), e
   débito residual se houver.
4. Estado: VALIDADO → garanta closed (re-close se aberto). NÃO VALIDADO →
   garanta opened (reopen) com o gap no comment.

CONSTRAINTS: não modifique nada em ~/dev/projetos/trading/betrader-hydra nem
arquivos do binance-project. NUNCA cole o valor do token em comment — refira como
$BETRADER_TOKEN. Não toque em outras issues/work items do projeto GitLab.

RETORNE quando `glab api projects/fabiosiqueira%2Fbetrader-hydra-bot/issues/1/notes | grep -q "Homologação M1"` (idem issue 2, ajustando pra GraphQL se REST não
aplicar — nesse caso reporte o comando de verificação equivalente que passou)
sair 0.
Retorne: veredito por item + URL dos comments + estado final (closed/opened).
```

## Launch order (DAG resolved)

### Phase 0 — parallel

- Cluster 1 / Task 1.1 (spike API)
- Cluster 1 / Task 1.2 (skeleton + harness)
- Cluster 1 / Task 1.3 (persona)

**Fan-out Phase 0: 3 parallel tasks**

### Phase 1 — after Phase 0 completes

- Cluster 2 / Task 2.1 (schemas + dogmas)

### Phase 2 — after Phase 1 completes

- Cluster 3 / Task 3.1 (risk_engine) +reviewer
- Cluster 3 / Task 3.2 (betrader_client) +reviewer
- Cluster 3 / Task 3.3 (observability)

**Fan-out Phase 2: 3 parallel tasks**

### Phase 3 — after Phase 2 completes

- Cluster 4 / Task 4.1 (strategist_cycle + cron) +reviewer

### Phase 4 — after Phase 3 completes

- Cluster 4 / Task 4.2 (E2E lifecycle)

### Phase 5 — after Phase 4 completes

- Cluster 5 / Task 5.1 (homologação GitLab #1 e #2)
