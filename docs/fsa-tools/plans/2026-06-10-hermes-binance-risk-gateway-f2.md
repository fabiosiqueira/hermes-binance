# Plan — Risk Gateway F2 (extrair enforcement para serviço separado)

## Metadata

- **Generated:** 2026-06-10
- **Worktree:** recommended
- **Issue:** `fabiosiqueira/hermes-binance#3` (ref #1)

## Context

Projeto `binance-project` (overlay do engine Hermes para estrategista Binance Futures via betrader-hydra). Python 3.11, pydantic v2, httpx, prometheus-client, redis, pyyaml. Testes: pytest + respx (mock HTTP) + fakeredis. Código do ciclo em `hermes/scripts/`, rodado pela imagem `ghcr.io/fabiosiqueira/hermes-engine`. `pyproject.toml` aponta `pythonpath = ["hermes/scripts"]` e `testpaths = ["tests"]`.

## Baseline (current state)

```bash
python -m pytest tests/ -q
```

Hoje (M1) o agente roda `python scripts/strategist_cycle.py brief|execute` **dentro do próprio container, com `BETRADER_TOKEN` em env** — o gate (`risk_engine.validate`) é in-process e o agente poderia escrever script próprio chamando o betrader direto, furando o gate. Suite atual passa; o objetivo é manter verde após a migração.

## Objective

Promover o enforcement de Dogmas a um **serviço determinístico separado** (Risk Gateway) que detém o token `bht_` do betrader. O Hermes perde a credencial de trading e fala **só** com o gateway via HTTP, enviando `StrategyProposal`. O contrato `StrategyProposal`/`Brief`/`Dogmas` não muda — é promoção, não reescrita.

## Definition of Done (global)

```bash
python -m pytest tests/ -q
```

**Expected output:** `passed` (zero `failed`, zero `error`), incluindo `tests/unit/test_risk_gateway.py`, o `tests/integration/test_cycle.py` reescrito e o e2e adaptado.

## Policy (invariant)

- **Contrato HTTP do gateway (estável; todas as tarefas se referem a ele):**
  | Método | Rota | Auth | Body in | Resposta |
  |---|---|---|---|---|
  | POST | `/brief` | `Bearer GATEWAY_TOKEN` | `{"symbol","timeframe","mode"}` | `Brief` JSON. Efeito colateral: gateway faz `fetch_brief` + carrega `FinancialState`, e **cacheia o Brief no Redis** sob `binance:strategist:brief:<symbol>` (JSON, TTL `BRIEF_CACHE_TTL_SECONDS`, default 900). |
  | POST | `/execute` | `Bearer GATEWAY_TOKEN` | `StrategyProposal` JSON | `{"executed":bool,...}`. Gateway: emergency_stop → carrega o **Brief cacheado** (`binance:strategist:brief:<symbol>`; ausente → `{"executed":false,"reason":"brief_missing"}`) → gate → betrader → observability → persiste estado. |
  | GET | `/health` | — | — | `200 {"status":"ok"}` |
  | GET | `/metrics` | — | — | Prometheus (registry da `Observability`) |
- **O `BETRADER_TOKEN` vive SÓ no serviço `risk-gateway`.** O container do Hermes (`gateway`) detém apenas `GATEWAY_URL` + `GATEWAY_TOKEN`. Nunca logar nenhum dos dois.
- **Enforcement é do gateway, não do cliente:** `check_emergency_stop`, `assert_testnet` (DRY_RUN) e `validate` rodam no gateway. O thin-client não enforça nada — só transporta proposta e imprime o resultado.
- **Brief que alimenta o gate vem do servidor (cache), nunca do cliente** — o agente não pode forjar equity/ref_price/drawdown.
- **Não reescrever** `risk_engine.py`, `betrader_client.py`, `observability.py`, `schemas.py`, `dogmas.yaml` — o gateway os **compõe**. Mudança nesses arquivos só se estritamente necessária para DI (ex.: já são injetáveis).
- **Testabilidade stdlib (padrão `webhook_shim.py`):** a lógica de `/brief` e `/execute` fica em **funções puras injetáveis** (recebem `redis_client`, `observability`, lêem betrader via env+respx); o `BaseHTTPRequestHandler` é um adapter fino. Testes alvejam as funções puras com DI — sem sockets reais (exceto, se desejado, 1 smoke de `/health`).
- **Match de estilo exato:** comentário-cabeçalho explicando o módulo, docstrings PT-BR, naming/erro/logging como nos vizinhos. Segredos nunca em repr/str/log.
- **Mocks só nas fronteiras de I/O** (HTTP betrader via respx, Redis via fakeredis). DI real nos módulos internos.

## Dependency justification

- **Task 3.1 blockedBy Task 1.1 + Task 2.1:** o teste de integração/e2e exercita o round-trip thin-client ↔ gateway real (em processo). Consome o módulo `risk_gateway.py` (1.1) E o `strategist_cycle.py` refatorado (2.1) — ambos artefatos.
- **Task 5.1 blockedBy Task 1.1:** o compose/coolify precisam do command de execução, da porta HTTP e dos nomes de env vars que o serviço `risk_gateway.py` (1.1) expõe.
- **Task 1.1 e Task 2.1 NÃO dependem entre si:** ambas consomem o *contrato HTTP* definido na seção Policy (artefato do plano), não um output uma da outra → paralelas. Task 4.1 idem (documenta o contrato).

## Clusters

### Cluster 1 — Risk Gateway service

**Inter-cluster dependency:** none

#### Task 1.1: Implementar `risk_gateway.py` + testes [opus] +reviewer

**Files:**
- Create: `hermes/scripts/risk_gateway.py`
- Create: `tests/unit/test_risk_gateway.py`

**Diagnosis:** O gate hoje roda in-process em `strategist_cycle._cmd_execute`. F2 move toda a metade de leitura/escrita do betrader + gate + observability para um serviço HTTP stdlib que detém o token. A lógica de `/brief` e `/execute` deve ficar em funções puras injetáveis para ser testável sem sockets (padrão `webhook_shim.py`).

**Verification:** `python -m pytest tests/unit/test_risk_gateway.py -q`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project (Python 3.11; pytest+respx+fakeredis; pythonpath=hermes/scripts).

TAREFA: criar o serviço Risk Gateway (F2) — promover o enforcement in-process a serviço HTTP determinístico que detém o token do betrader.

LEIA ANTES (estilo + contratos a reusar, NÃO reescrever):
- hermes/scripts/webhook_shim.py  → padrão EXATO a copiar: cabeçalho-comentário, funções puras + BaseHTTPRequestHandler fino em thread daemon, on_error(type) como DI, segredo nunca logado, __main__ que sobe o servidor e dorme.
- hermes/scripts/observability.py → Observability (start_servers já expõe /metrics:porta e /health:porta+1), FinancialState (load/persist), maybe_trigger_drawdown_wake. REUSE; não reescreva.
- hermes/scripts/betrader_client.py → BetraderClient.from_env(on_error=), fetch_brief(symbol,timeframe,mode,risk_state), ensure_monitor, assert_testnet, place_entry_with_stop(entry,equity,ref_price=), teardown, install_automations. REUSE.
- hermes/scripts/risk_engine.py → validate(proposal,dogmas,brief)->GateResult, check_emergency_stop(). REUSE.
- hermes/scripts/schemas.py → Brief, StrategyProposal, ExecutionMode, load_dogmas. REUSE.
- hermes/scripts/strategist_cycle.py → a ordem EXATA do ciclo atual (_cmd_brief e _cmd_execute): replique a mesma sequência no gateway (ensure_monitor → FinancialState.load → fetch_brief; e no execute: emergency_stop → carrega brief → maybe_trigger_drawdown_wake → validate → assert_testnet(DRY_RUN) → teardown → entries → install_automations → record_decision → record_cycle → state.persist).

CRIAR hermes/scripts/risk_gateway.py com:
1. Funções PURAS injetáveis (testáveis sem socket):
   - handle_brief(*, symbol, timeframe, mode, redis_client, observability) -> dict (Brief.model_dump). Replica _cmd_brief, e ALÉM disso cacheia o Brief no Redis em "binance:strategist:brief:<symbol>" como JSON com TTL = int(os.environ.get("BRIEF_CACHE_TTL_SECONDS","900")) via redis.set(key, json, ex=ttl).
   - handle_execute(*, proposal: StrategyProposal, symbol, redis_client, observability) -> dict ({"executed":...}). Replica _cmd_execute MAS o brief vem do cache Redis "binance:strategist:brief:<symbol>" (ausente/expirado → return {"executed":False,"reason":"brief_missing"}); valida o JSON cacheado com Brief.model_validate_json. emergency_stop, assert_testnet e validate rodam AQUI. Mesmo contrato de stdout do _cmd_execute (reasons: emergency_stop, brief_missing, invalid_proposal, gate_rejected; sucesso: executed/orders/automations/errors).
   - O símbolo do execute: derive de proposal.entries[0].symbol quando houver entries; senão use os.environ.get("SYMBOL","BTCUSDT"). (Para teardown/automation-only, usa o symbol do env.)
2. Auth: helper require_auth(header_value) comparando com os.environ["GATEWAY_TOKEN"] via hmac.compare_digest contra "Bearer <token>". GATEWAY_TOKEN ausente → o servidor recusa subir (raise no start, igual webhook_shim com BETRADER_WEBHOOK_SECRET).
3. BaseHTTPRequestHandler FINO (igual webhook_shim): POST /brief e POST /execute (401 sem auth válida; 400 JSON inválido; chama as funções puras; serializa o dict de volta como JSON 200), GET /health (200 {"status":"ok"}), GET /metrics → delega ao registry da Observability (pode reusar start_http_server da observability em porta separada, OU servir o registry; escolha a que casa observability.py — preferir Observability.start_servers para /metrics+/health e manter o handler do gateway só para /brief+/execute na porta GATEWAY_PORT). Documente a decisão de portas no cabeçalho como observability.py faz.
4. start_gateway(port=int(os.environ.get("GATEWAY_PORT","8647")), *, redis_client=None, observability=None, on_error=None): constrói redis (REDIS_HOST/REDIS_PORT, decode_responses=True) e Observability se não injetados, chama observability.restore_metrics(FinancialState.load(redis)) no início (resiliência bot.md), sobe Observability.start_servers() e o handler do gateway. __main__ sobe e dorme (loop sleep), igual webhook_shim.
5. NUNCA logar GATEWAY_TOKEN nem BETRADER_TOKEN.

CRIAR tests/unit/test_risk_gateway.py (espelhe a ESTRUTURA de tests/integration/test_cycle.py atual — MIGRE os casos para o gateway):
- respx mocka o betrader (mesmos payloads/shapes de test_cycle.py: _users_payload, _monitors_payload, _indicators_payload, _market_payload, _balance_*_payload, _futures_payload, etc.), fakeredis para Redis, Observability() com registry dedicado.
- Testar handle_brief: monta brief, retorna dict, e CACHEIA no Redis "binance:strategist:brief:BTCUSDT" (assert redis.get != None).
- Testar handle_execute via cache: depois de handle_brief, handle_execute com proposta aprovada (LIMIT BUY 60000/stop 59000/sizing5/lev3) → executed True, 2 POSTs (entry+stop), estado persistido, decisão no stream.
- gate-reject (leverage 20 > teto) → executed False, reason gate_rejected, violations contém "leverage", NENHUMA call de escrita.
- emergency_stop (env EMERGENCY_STOP=true) → {"executed":False,"reason":"emergency_stop"}, zero calls.
- brief_missing (execute sem brief no cache) → {"executed":False,"reason":"brief_missing"}.
- proposta inválida (sem stop_loss → ValidationError) → reason invalid_proposal.
- rollback (entry OK, stop 500 → DELETE) → executed True, orders vazio, "entry_rolled_back_no_stop" em errors, estado persistido.
- auth: require_auth aceita "Bearer <token-correto>" e rejeita token errado/ausente.
Use as MESMAS env vars dos testes atuais (BETRADER_BASE_URL, BETRADER_TOKEN, EXECUTION_MODE=DRY_RUN, SYMBOL, TIMEFRAME) + GATEWAY_TOKEN.

NÃO MODIFIQUE: risk_engine.py, betrader_client.py, observability.py, schemas.py, dogmas.yaml, strategist_cycle.py, nenhum arquivo de infra.

RETORNE: resumo do que criou, arquivos tocados, e a saída de `python -m pytest tests/unit/test_risk_gateway.py -q`.
Return when `python -m pytest tests/unit/test_risk_gateway.py -q` exits 0.
```

### Cluster 2 — Hermes thin-client

**Inter-cluster dependency:** none

#### Task 2.1: Refatorar `strategist_cycle.py` para thin-client HTTP + reescrever `test_cycle.py` [sonnet]

**Files:**
- Modify: `hermes/scripts/strategist_cycle.py`
- Modify: `tests/integration/test_cycle.py`

**Diagnosis:** Hoje `strategist_cycle.py` compõe `BetraderClient`+`validate`+`Observability` direto (e detém o token). Com o gateway assumindo isso, o cliente vira transporte HTTP: `brief` chama `POST GATEWAY_URL/brief` e escreve `workspace/brief.json`; `execute` lê `workspace/proposal.json` e chama `POST GATEWAY_URL/execute`. Mesma CLI e mesmo contrato de stdout.

**Verification:** `python -m pytest tests/integration/test_cycle.py -q && ! grep -qE 'from (betrader_client|risk_engine|observability) import|BetraderClient|risk_engine|FinancialState' hermes/scripts/strategist_cycle.py`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project (Python 3.11; pytest+respx+fakeredis; pythonpath=hermes/scripts).

TAREFA: refatorar hermes/scripts/strategist_cycle.py de orquestrador in-process para THIN-CLIENT HTTP do Risk Gateway (F2). O agente perde o token do betrader; fala só com o gateway.

CONTRATO HTTP DO GATEWAY (estável, NÃO mudar):
- POST {GATEWAY_URL}/brief, header "Authorization: Bearer {GATEWAY_TOKEN}", body {"symbol","timeframe","mode"} → 200 com Brief JSON.
- POST {GATEWAY_URL}/execute, header "Authorization: Bearer {GATEWAY_TOKEN}", body = StrategyProposal JSON → 200 com {"executed":bool,...} (reasons possíveis: emergency_stop, brief_missing, invalid_proposal, gate_rejected; sucesso inclui orders/automations/errors).

LEIA ANTES:
- hermes/scripts/strategist_cycle.py atual (preserve: CLI argparse com subcomandos `brief` e `execute proposal`; _WORKSPACE=Path("workspace"); _BRIEF_PATH=workspace/brief.json; contrato de stdout — `brief` imprime o PATH absoluto do brief.json, `execute` imprime JSON do resultado; _emit()).
- hermes/scripts/webhook_shim.py e observability.py → estilo de chamada httpx, tratamento de erro de I/O, on_error/record_error.

REESCREVER strategist_cycle.py:
- Remover imports e uso de BetraderClient, validate, check_emergency_stop, Observability, FinancialState, maybe_trigger_drawdown_wake, load_dogmas, _ref_price, _DOGMAS_PATH, _build_redis. O cliente NÃO enforça nada nem toca Redis/dogmas/betrader.
- Ler env: GATEWAY_URL (ex.: http://risk-gateway:8647) e GATEWAY_TOKEN. Ausentes → erro claro em JSON ({"executed":false,"reason":"missing_gateway_config"}) no caminho execute, e no brief um stderr+exit !=0 OU JSON de erro — siga o padrão de _emit já existente; mantenha stdout limpo (path ou JSON, nunca traceback).
- `brief`: monta {symbol,timeframe,mode} de SYMBOL/TIMEFRAME/EXECUTION_MODE do env, POST /brief, escreve a resposta em workspace/brief.json (model_dump já vem do gateway — escreve o JSON recebido), imprime o path absoluto. Falha de I/O HTTP → JSON/stderr de erro sem traceback.
- `execute`: lê workspace/proposal.json (texto cru), POST /execute com esse corpo, imprime o JSON da resposta do gateway via _emit. NÃO envia o brief (o gateway usa o brief cacheado server-side). ValidationError/OSError de leitura local do proposal.json → {"executed":false,"reason":"invalid_proposal","detail":...} como hoje. Erro de I/O HTTP ao gateway → {"executed":false,"reason":"gateway_error","detail":...}.
- main(argv=None, *, http_client=None): injete o cliente httpx como fronteira de I/O (default httpx.Client) para os testes mockarem via respx. NÃO precisa mais de redis_client/observability na assinatura — remova-os.

REESCREVER tests/integration/test_cycle.py (agora testa o CLIENTE contra o GATEWAY MOCKADO):
- respx mocka {GATEWAY_URL}/brief e {GATEWAY_URL}/execute (não mais o betrader). fakeredis NÃO é mais necessário aqui.
- Casos: 
  (a) brief → POST /brief retorna um Brief JSON de exemplo; assert workspace/brief.json escrito com esse conteúdo e stdout = path.
  (b) execute happy → POST /execute retorna {"executed":true,"orders":[...],"automations":[],"errors":[]}; assert stdout == esse JSON e que o corpo enviado ao gateway == conteúdo de proposal.json.
  (c) execute gate_rejected → gateway retorna {"executed":false,"reason":"gate_rejected","violations":[...]}; assert repassado fielmente.
  (d) execute com proposal.json inválido (sem stop_loss) → cliente NEM chama o gateway (reason invalid_proposal local) → assert respx call_count do /execute == 0.
  (e) auth header: assert que o cliente envia "Authorization: Bearer <GATEWAY_TOKEN>" em /brief e /execute.
  (f) gateway_error: /execute responde 502 → cliente imprime {"executed":false,"reason":"gateway_error",...}.
- env de teste: GATEWAY_URL, GATEWAY_TOKEN, EXECUTION_MODE=DRY_RUN, SYMBOL, TIMEFRAME (sem BETRADER_TOKEN no lado do cliente).

NÃO MODIFIQUE: risk_gateway.py, risk_engine.py, betrader_client.py, observability.py, schemas.py, infra. O e2e (tests/e2e) é de outra tarefa — não mexer.

RETORNE: resumo, diff conceitual, saída de `python -m pytest tests/integration/test_cycle.py -q`.
Return when `python -m pytest tests/integration/test_cycle.py -q` exits 0 E `grep -qE 'BetraderClient|risk_engine|FinancialState' hermes/scripts/strategist_cycle.py` NÃO casa (cliente não enforça).
```

### Cluster 3 — Verificação ponta-a-ponta

**Inter-cluster dependency:** depends on Cluster 1, Cluster 2

#### Task 3.1: Adaptar e2e de lifecycle ao split gateway/cliente [sonnet] +reviewer

**Intra-cluster dependency:** —

**Files:**
- Modify: `tests/e2e/test_lifecycle_dry_run.py`
- Modify (se necessário): `tests/e2e/test_lifecycle_event.py`

**Diagnosis:** O e2e atual chama `strategist_cycle.main(["brief"/"execute"], redis_client=, observability=)` com respx no betrader. Com o split, o brief/execute reais acontecem no gateway. O e2e deve exercitar o caminho real: gateway (handle_brief/handle_execute, betrader via respx, fakeredis) servindo o ciclo, mantendo as asserções de integridade financeira (equity-curve, win/loss, MaxDD, restart). As asserções de FinancialState/restore_metrics agora testam a observability DO gateway.

**Verification:** `python -m pytest tests/e2e -q`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project (Python 3.11; pytest+respx+fakeredis; pythonpath=hermes/scripts).

CONTEXTO: a F2 dividiu o ciclo em (a) Risk Gateway hermes/scripts/risk_gateway.py com funções puras handle_brief(*, symbol,timeframe,mode, redis_client, observability)->dict e handle_execute(*, proposal: StrategyProposal, symbol, redis_client, observability)->dict (o gateway detém o betrader e o gate; brief é cacheado no Redis "binance:strategist:brief:<symbol>"); e (b) hermes/scripts/strategist_cycle.py virou thin-client HTTP. LEIA os dois antes.

TAREFA: adaptar o e2e de lifecycle DRY_RUN para o novo split, SEM perder cobertura de integridade financeira.

LEIA ANTES:
- tests/e2e/test_lifecycle_dry_run.py atual (narrativa: Ciclo1 entrada → Ciclo2 gestão → saída lucro → Ciclo3 perda → restart → kill switch). Mantém todas as asserções de FinancialState/equity-curve/MaxDD/win_rate/restore_metrics.
- tests/unit/test_risk_gateway.py (estrutura de mocks respx do betrader + fakeredis a reusar).
- tests/e2e/test_lifecycle_event.py (cheque se referencia o caminho de execute; adapte só se quebrar).

ABORDAGEM: troque as chamadas `main(["brief"], redis_client=, observability=)` / `main(["execute", path], ...)` por chamadas DIRETAS às funções puras do gateway:
- brief: chame risk_gateway.handle_brief(symbol="BTCUSDT", timeframe="15m", mode=ExecutionMode.DRY_RUN, redis_client=redis_lc, observability=obs) — isso popula o cache de brief no Redis.
- execute: carregue a proposta como StrategyProposal e chame risk_gateway.handle_execute(proposal=..., symbol="BTCUSDT", redis_client=redis_lc, observability=obs). Asserte o dict retornado (mesma forma do stdout antigo: executed/orders/automations/errors/reason/violations).
- Mantenha respx mockando o betrader (mesmos payloads), fakeredis compartilhado no lifecycle, e TODAS as asserções de: persistência em "binance:strategist:financial_state", stream "binance:strategist:decisions", contadores de ciclo, FinancialState.record_trade/equity/peak/drawdown/win_rate, restart via novo Observability+restore_metrics.
- kill switch: EMERGENCY_STOP=true → handle_execute retorna {"executed":False,"reason":"emergency_stop"} sem call de escrita (respx call_count 0 nas rotas de escrita).
- Onde o e2e antigo lia workspace/brief.json do disco (Ciclo 2 valida posição no brief), agora valide o brief cacheado no Redis (json.loads(redis_lc.get("binance:strategist:brief:BTCUSDT"))) OU o dict retornado por handle_brief — escolha o que for observável e mantenha a asserção de "posição aberta no brief".

NÃO MODIFIQUE: código de produção (risk_gateway.py, strategist_cycle.py, módulos do ciclo) nem infra. Só os arquivos de teste e2e.

RETORNE: resumo das adaptações + saída de `python -m pytest tests/e2e -q`.
Return when `python -m pytest tests/e2e -q` exits 0.
```

### Cluster 4 — Contexto do agente (docs)

**Inter-cluster dependency:** none

#### Task 4.1: Atualizar `AGENTS.md` (+ SOUL/config se referenciam token) [sonnet]

**Files:**
- Modify: `hermes/AGENTS.md`
- Modify (só se referenciar `BETRADER_TOKEN`/gate in-process): `hermes/SOUL.md`, `hermes/config.yaml`

**Diagnosis:** `AGENTS.md` descreve o ciclo com o agente rodando `strategist_cycle.py` e detendo `BETRADER_TOKEN`, e o gate como `risk_engine.py` in-process. Pós-F2: agente sem token de trading, falando com o Risk Gateway via `GATEWAY_URL`/`GATEWAY_TOKEN`; o gate vive no serviço separado. A CLI `strategist_cycle.py brief|execute` continua igual (thin-client) — então a mudança é cirúrgica.

**Verification:** `grep -qE 'risk-gateway|Risk Gateway|GATEWAY_URL' hermes/AGENTS.md && ! grep -q 'BETRADER_TOKEN' hermes/SOUL.md`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project. Docs do agente Hermes (PT-BR).

TAREFA: atualizar hermes/AGENTS.md para refletir a F2 (Risk Gateway como serviço separado). Mudança CIRÚRGICA — a CLI do agente (`python scripts/strategist_cycle.py brief|execute`) e o contrato de stdout NÃO mudam; muda QUEM detém o token e onde roda o gate.

LEIA ANTES:
- hermes/AGENTS.md inteiro (especialmente: seção "betrader-hydra (executor)" linhas ~32-37; "Ciclo do estrategista" ~56-110; tabela de env vars ~140-145; menções a risk_engine.py e BETRADER_TOKEN).
- hermes/SOUL.md e hermes/config.yaml — só para checar se referenciam BETRADER_TOKEN ou "gate in-process"; se não, NÃO toque.
- A seção Policy do plano docs/fsa-tools/plans/2026-06-10-hermes-binance-risk-gateway-f2.md (contrato HTTP do gateway, quem detém o token).

EDITS em AGENTS.md (mínimos, match de estilo):
1. Seção betrader-hydra: deixar claro que o betrader é acessado SÓ pelo Risk Gateway (serviço separado), não pelo agente. O agente NÃO tem BETRADER_TOKEN.
2. Seção "Ciclo do estrategista": o agente continua rodando `scripts/strategist_cycle.py brief|execute`, mas agora esses comandos falam HTTP com o Risk Gateway (que detém o token, aplica os Dogmas, executa no betrader). O gate NÃO é mais in-process; é o serviço. emergency_stop/assert_testnet/validate rodam no gateway.
3. Tabela de env vars: remover/realocar BETRADER_TOKEN (agora é do serviço risk-gateway), adicionar GATEWAY_URL e GATEWAY_TOKEN (do container do agente). BETRADER_BASE_URL também migra para o serviço.
4. Onde menciona risk_engine.py como "gate read-only do agente", esclarecer que o enforcement agora é o serviço Risk Gateway (mesma constituição, inviolável por arquitetura).
NÃO inventar capacidades novas; não documentar rotas internas do gateway em detalhe (o agente não as chama direto — usa a CLI). Datas relativas → ISO se houver.

NÃO MODIFIQUE: código, testes, infra, dogmas.yaml.

RETORNE: resumo dos trechos alterados.
Return when `grep -qE 'risk-gateway|Risk Gateway|GATEWAY_URL' hermes/AGENTS.md` exits 0.
```

### Cluster 5 — Infra (compose / coolify / env / Dockerfile)

**Inter-cluster dependency:** depends on Cluster 1

#### Task 5.1: Adicionar serviço `risk-gateway` e remover token do Hermes [sonnet]

**Intra-cluster dependency:** —

**Files:**
- Modify: `hermes-compose.local.yml`
- Modify: `hermes-coolify.yml`
- Modify: `.env.example`
- Modify (só se precisar de porta/command novo): `Dockerfile`

**Diagnosis:** O serviço `risk-gateway` roda a mesma imagem (`python scripts/risk_gateway.py`), detém `BETRADER_TOKEN`/`BETRADER_BASE_URL`/`REDIS_HOST` e expõe a API HTTP (porta `GATEWAY_PORT`, default 8647) + métricas (9468/9469, herdadas da Observability que migrou pra cá). O serviço `gateway` (Hermes) **perde** `BETRADER_TOKEN`/`BETRADER_BASE_URL` e **ganha** `GATEWAY_URL=http://risk-gateway:8647` + `GATEWAY_TOKEN`. Operador aprovou a mudança de infra nesta sessão.

**Verification:** `python -c "import yaml; yaml.safe_load(open('hermes-compose.local.yml')); yaml.safe_load(open('hermes-coolify.yml'))" && grep -q 'risk-gateway' hermes-compose.local.yml && grep -q 'risk-gateway' hermes-coolify.yml && grep -q 'GATEWAY_TOKEN' .env.example`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project. Infra Docker Compose (mudança APROVADA pelo operador nesta sessão).

TAREFA: introduzir o serviço `risk-gateway` (Risk Gateway F2) e remover o token de trading do container do Hermes.

LEIA ANTES (replicar padrões EXATOS):
- hermes-compose.local.yml inteiro — note o padrão do sidecar `webhook-shim` (mesma imagem/Dockerfile target local, command custom, working_dir /opt/data, volumes ./hermes:/opt/data, env_file hermes/.env). Note as portas já usadas: gateway 8644, webhook-shim 8645, metrics 9468, dashboard 9121, redis 6381 (e o comentário-cabeçalho de portas).
- hermes-coolify.yml inteiro — tudo interno (sem ports), service DNS na rede `binance`, restart unless-stopped, env vars vindas do Coolify (${VAR}).
- .env.example.
- hermes/scripts/risk_gateway.py (criado na Task 1.1) — confirme o command de execução, a porta default (GATEWAY_PORT, 8647) e as env vars exatas que ele lê: GATEWAY_TOKEN, BETRADER_TOKEN, BETRADER_BASE_URL, REDIS_HOST/REDIS_PORT, EXECUTION_MODE, SYMBOL, TIMEFRAME, INITIAL_EQUITY, EMERGENCY_STOP, BRIEF_CACHE_TTL_SECONDS, WEBHOOK_PUBLIC_URL/BETRADER_WEBHOOK_SECRET (drawdown wake).

EDITS:
1. hermes-compose.local.yml:
   - Novo serviço `risk-gateway`: build mesmo Dockerfile target local; command ["sh","-c","python scripts/risk_gateway.py"]; working_dir /opt/data; volumes ./hermes:/opt/data; depends_on redis; network `binance`; env_file hermes/.env; environment: HERMES_DATA_DIR=/opt/data, REDIS_HOST=redis, REDIS_PORT="6379", e as env vars que ele detém. Publicar a porta da API (8647:8647) e mover o mapeamento de métricas 9468 para ESTE serviço (a Observability migrou pra cá). Atualizar o comentário-cabeçalho de portas.
   - Serviço `gateway`: REMOVER BETRADER_TOKEN e BETRADER_BASE_URL do environment; ADICIONAR GATEWAY_URL: "http://risk-gateway:8647" e GATEWAY_TOKEN: ${GATEWAY_TOKEN}. Remover o mapeamento 9468 do gateway se ele só servia a observability do estrategista (confirme; se o gateway usa 9468 pra outra coisa, mantenha). depends_on: adicionar risk-gateway.
2. hermes-coolify.yml:
   - Novo serviço `risk-gateway` análogo (target vps, sem ports, rede binance, restart unless-stopped, depends_on redis healthy), com as mesmas env vars (${BETRADER_TOKEN}, ${BETRADER_BASE_URL}, ${BETRADER_USER}, EXECUTION_MODE, EMERGENCY_STOP, INITIAL_EQUITY, GATEWAY_TOKEN, REDIS_HOST/PORT, BRIEF_CACHE_TTL_SECONDS, e webhook secret/url se aplicável).
   - Serviço `gateway`: remover BETRADER_* ; adicionar GATEWAY_URL=http://risk-gateway:8647 e GATEWAY_TOKEN=${GATEWAY_TOKEN}; depends_on risk-gateway. Atualizar o comentário do topo (lista de env vars por serviço).
3. .env.example: adicionar `GATEWAY_TOKEN=gwt_xxx` (placeholder) numa seção "--- Risk Gateway (F2) ---" e um comentário de que o agente usa GATEWAY_URL/GATEWAY_TOKEN e só o risk-gateway detém BETRADER_TOKEN. Manter BETRADER_TOKEN (agora consumido pelo risk-gateway). NÃO colocar valores reais.
4. Dockerfile: provavelmente NÃO precisa mudar (mesma imagem/deps já instaladas: httpx, prometheus-client, redis, pydantic, pyyaml). Só edite se faltar algo; se editar, explique.

CONSTRAINTS: não quebrar o YAML; manter naming/ordem/estilo dos serviços vizinhos; segredos só como ${VAR}/placeholder, nunca valores. Não tocar Redis/dashboard.

RETORNE: resumo das mudanças de cada arquivo + saída de:
`python -c "import yaml; yaml.safe_load(open('hermes-compose.local.yml')); yaml.safe_load(open('hermes-coolify.yml'))" && echo OK`
Return when esse comando imprime OK e `grep -q risk-gateway hermes-compose.local.yml` exits 0.
```

## Launch order (DAG resolved)

### Phase 0 — parallel

- Cluster 1 / Task 1.1  (Risk Gateway service) `[opus] +reviewer`
- Cluster 2 / Task 2.1  (thin-client + test_cycle) `[sonnet]`
- Cluster 4 / Task 4.1  (AGENTS.md) `[sonnet]`

**Fan-out Phase 0: 3 parallel tasks**

### Phase 1 — after Phase 0 completes

- Cluster 3 / Task 3.1  (e2e adaptado) `[sonnet] +reviewer`  ← 1.1 + 2.1
- Cluster 5 / Task 5.1  (infra compose/coolify/env) `[sonnet]`  ← 1.1
