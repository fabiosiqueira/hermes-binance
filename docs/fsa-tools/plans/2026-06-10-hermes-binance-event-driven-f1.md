# Plan — F1 Estrategista event-driven (webhook do betrader acorda o Hermes)

## Metadata

- **Generated:** 2026-06-10
- **Worktree:** recommended
- **Issue:** `fabiosiqueira/hermes-binance#2` (ref #1). Spec: `docs/superpowers/specs/2026-06-09-hermes-binance-strategist-design.md` (fase F1).

## Context

Overlay `binance-project` (estrategista Hermes para Binance Futures via betrader-hydra). Engine Hermes consumido como imagem pública; customização (persona, ciclo, dogmas) no data dir `hermes/`. Scripts do ciclo em Python (`hermes/scripts/`, flat imports, `pythonpath=["hermes/scripts"]`). Testes pytest na raiz (`tests/{unit,integration,e2e}`), mocks só na fronteira de I/O (`respx` HTTP betrader, `fakeredis` Redis), DI real. M1 (ciclo agendado por candle 15m via `cron/jobs.json`) já entregue e merged.

## Baseline (current state)

```bash
# Requer deps de teste: pip install -e ".[test]"
python -m pytest -q                       # M1: suíte passa (unit+integration+e2e), 0 failed
test -f hermes/scripts/webhook_shim.py    # ausente (a criar) → exit 1
python -c "import yaml;print('webhook' in (yaml.safe_load(open('hermes/config.yaml')).get('platforms',{})))"  # False (plataforma webhook não habilitada)
```

## Objective

Adicionar o ramo **por evento** ao ritmo do estrategista: o betrader emite um `WEBHOOK` (action de automation-sentinela) em evento severo; um shim verifica a assinatura HMAC e repassa ao webhook **nativo** do engine, cujo `prompt` de rota acorda o HAWK para **re-decidir a estratégia** (não a ordem) e notificar. Self-trigger nosso para drawdown-80% (que o betrader não enxerga).

## Definition of Done (global)

Single verifiable command:

```bash
python -m pytest -q && docker compose -f hermes-compose.local.yml config -q && python -c "import yaml;w=yaml.safe_load(open('hermes/config.yaml'))['platforms']['webhook'];assert w['enabled'] and 'strategist-event' in w['extra']['routes']"
```

**Expected output:** linha final do pytest com `passed` (0 failed) e exit 0; `docker compose ... config` sem erro; assert do config webhook sem AssertionError.

## Policy (invariant)

- **Arquitetura travada (Opção B — shim re-assina):** `betrader → shim (verifica X-Beholder-Signature: sha256=<hex>) → 127.0.0.1:8646/webhooks/strategist-event (header X-Webhook-Signature: <hex raw>, esquema Generic do engine) → prompt da rota acorda o HAWK`. Sem Redis stream, sem consumer, sem subprocess, sem `INSECURE_NO_AUTH`.
- **Contrato de portas/paths/secret (stub compartilhado por todas as tasks):**
  - Shim escuta na porta pública **8645** (`/hook/betrader`, POST).
  - Webhook nativo do engine na porta **8646** (loopback-only, **não exposta** no compose), rota **`strategist-event`**.
  - Launch do shim: `python scripts/webhook_shim.py` (sidecar com `network_mode: "service:gateway"`).
  - Secret único **`BETRADER_WEBHOOK_SECRET`** (env, gitignored): betrader assina, shim verifica, engine (Generic) verifica o re-assinado. URL pública das sentinelas em **`WEBHOOK_PUBLIC_URL`** (env).
  - Assinatura = `HMAC-SHA256(secret, raw_body)` hex. betrader manda `sha256=<hex>` no header `X-Beholder-Signature`; o engine Generic espera **hex puro** (sem prefixo) no header `X-Webhook-Signature`.
- **Secrets nunca versionados nem expostos:** `BETRADER_WEBHOOK_SECRET`/`BETRADER_TOKEN` só em `hermes/.env` (gitignored). Nunca em `config.yaml` versionado, log, `repr`/`str`, nem no prompt do LLM. `.env.example` mostra a chave sem valor.
- **Match existing style exactly:** shim espelha o pattern de `observability.start_servers` (`BaseHTTPRequestHandler` + `Thread`, `do_GET`/`do_POST`, `log_message` silenciado). Erros de I/O seguem o padrão `BetraderError(type)` + `on_error` do `betrader_client`. Schemas em pydantic v2. Imports flat.
- **Immutability:** nunca mutar dicts recebidos (ex.: action do LLM) — criar novo dict ao injetar `webhookUrl`/`webhookSecret`.
- **Invioláveis (não tocar):** `hermes/dogmas.yaml`, `hermes/scripts/risk_engine.py`. **betrader-hydra é read-only** (integração só via REST/contrato já documentado).
- **Infra aprovada para este plano:** `hermes/config.yaml`, `hermes-compose.local.yml`, `.env.example` podem ser tocados (operador aprovou explicitamente). Menor diff possível; não mexer em `Dockerfile` (sidecar evita).
- **Redis** sempre via `REDIS_HOST`/`REDIS_PORT` do env.
- **Testes:** novos testes na raiz `tests/`, mocks só na fronteira (`respx`/`fakeredis`), DI real. Rodar com `pip install -e ".[test]"`.

## Dependency justification

- **Task 4.1 blockedBy Cluster 1, Cluster 2, Cluster 3:** o E2E event-driven integra ponta-a-ponta o shim+config (Cluster 1), a injeção de sentinela no `betrader_client` (Cluster 2) e o self-trigger de drawdown (Cluster 3). Consome artefatos reais dos três (script `webhook_shim.py`, rota `strategist-event`, `install_automations` com injeção, helper de drawdown).
- Demais tasks são **independentes** (arquivos disjuntos; só compartilham o contrato de portas/secret fixado na Policy, que é stub conhecido a priori). Fan-out máximo na fase 0.

## Clusters

### Cluster 1 — Caminho do receiver (betrader → shim → webhook nativo)

**Inter-cluster dependency:** none

#### Task 1.1: `scripts/webhook_shim.py` — verify HMAC + re-sign + forward [opus] +reviewer

**Files:**
- Create: `hermes/scripts/webhook_shim.py`
- Create: `tests/unit/test_webhook_shim.py`

**Diagnosis:** Não existe receiver HTTP. O engine só reconhece `X-Hub-Signature-256`/`X-Gitlab-Token`/`X-Webhook-Signature` (header fixo, não-configurável) e betrader manda `X-Beholder-Signature` (read-only). O shim é a ponte: verifica a assinatura do betrader e re-assina no esquema Generic do engine. É a única fronteira de confiança externa → segurança crítica.

**Verification:** `python -m pytest tests/unit/test_webhook_shim.py -q`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project (overlay Hermes/Binance, Python).

Tarefa: criar um servidor HTTP "shim" que recebe o webhook do betrader-hydra, verifica a assinatura HMAC, e repassa ao webhook nativo do engine Hermes re-assinando para o esquema que o engine reconhece.

Crie `hermes/scripts/webhook_shim.py` e `tests/unit/test_webhook_shim.py`.

CONTEXTO DE ESTILO — leia ANTES de escrever e copie o padrão sem desvio:
- `hermes/scripts/observability.py` (método `start_servers` / classe `_HealthHandler`): use o MESMO padrão `http.server.BaseHTTPRequestHandler` + `threading.Thread(daemon=True)`, `do_POST`, `send_response`/`send_header`/`end_headers`, e `log_message` silenciado.
- `hermes/scripts/betrader_client.py` (classe `BetraderError`, método `_notify`/`on_error`): erros de I/O externo notificam `on_error(type)` com `type` descritivo. Reuse esse contrato de DI (`on_error: Callable[[str], None] | None`).
- `tests/unit/test_betrader_client.py`: estilo de teste (respx na fronteira HTTP, asserts de comportamento observável). Imports flat (`from observability import ...`).

CONTRATO (fixo — não invente outros valores):
- O shim escuta em `0.0.0.0:8645`, único path `POST /hook/betrader`. Outros paths → 404; outros métodos → 405; JSON inválido → 400.
- Lê o secret de `os.environ["BETRADER_WEBHOOK_SECRET"]` e a URL de forward de `os.environ.get("ENGINE_WEBHOOK_URL", "http://127.0.0.1:8646/webhooks/strategist-event")`.
- Verificação da entrada: header `X-Beholder-Signature` no formato `sha256=<hex>`. Calcula `expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()` e compara com `hmac.compare_digest` contra o hex após o prefixo `sha256=`. Ausente ou divergente → responde 401 `{"ok": false, "error": "invalid signature"}` e NÃO faz forward. Use o corpo RAW exato (bytes lidos), não re-serializado.
- Em sucesso: faz `POST` (httpx, já é dependência) para `ENGINE_WEBHOOK_URL` com o MESMO corpo raw e header `X-Webhook-Signature: <hex>` (hex PURO, SEM prefixo `sha256=` — é o esquema Generic do engine), `Content-Type: application/json`. Repassa o status do engine; em falha de rede chama `on_error("engine_forward_error")` e responde 502 `{"ok": false, "error": "forward failed"}`.
- Responde 200 `{"ok": true}` quando o engine aceitar (2xx).
- Função pública testável `verify_signature(raw_body: bytes, header: str | None, secret: str) -> bool` e `build_forward_signature(raw_body: bytes, secret: str) -> str`, separadas do handler (igual `observability` separa lógica de servidor). `start_shim(port: int = 8645, *, on_error=None) -> None` espelhando `start_servers`. Bloco `if __name__ == "__main__":` que chama `start_shim()` lendo env.

CONSTRAINTS:
- NUNCA logar/printar o secret nem o corpo cru com assinatura. `log_message` silenciado.
- Mock só na fronteira HTTP (respx para o forward ao engine). Sem mockar lógica interna. O secret de teste é um valor fixo local (ex.: "whsec_test").
- Imports flat. Não toque em nenhum outro arquivo.
- Cubra nos testes: assinatura válida → forward chamado com header `X-Webhook-Signature` hex puro e mesmo body (assert via respx) → 200; assinatura inválida → 401 e forward NÃO chamado; header ausente → 401; método GET → 405; path errado → 404; falha do engine (respx 500 ou ConnectError) → 502.

RETORNE quando `cd /Users/fabiosiqueira/dev/projetos/hermes/binance-project && python -m pytest tests/unit/test_webhook_shim.py -q` sair 0. Reporte: arquivos criados e a linha final do pytest.
```

#### Task 1.2: Rota + plataforma webhook em `hermes/config.yaml` [sonnet]

**Files:**
- Modify: `hermes/config.yaml`

**Diagnosis:** A plataforma `webhook` do engine não está habilitada. Precisa de `platforms.webhook.enabled: true`, `extra.port: 8646` e a rota `strategist-event` com um `prompt` que acorda o HAWK para rodar o ciclo. O secret NÃO entra no arquivo versionado — vem do env (`WEBHOOK_SECRET`/route herda o global). Menor diff possível no config.

**Verification:** `python -c "import yaml;w=yaml.safe_load(open('hermes/config.yaml'))['platforms']['webhook'];assert w['enabled'] and w['extra']['port']==8646 and 'strategist-event' in w['extra']['routes'] and w['extra']['routes']['strategist-event'].get('prompt')"`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project.

Tarefa: habilitar a plataforma webhook NATIVA do engine Hermes em `hermes/config.yaml`, com uma rota que acorda o agente estrategista (HAWK) quando recebe um evento severo do betrader.

Leia ANTES: `hermes/config.yaml` (note a chave de topo `platforms` se existir, e a seção `platform_toolsets.webhook`), e `hermes/cron/jobs.json` (o prompt do job `strategist-candle-15m` é o modelo EXATO do que o HAWK deve fazer no ciclo — copie a sequência brief→propose→execute).

Adicione/edite a chave de topo `platforms.webhook` (MENOR diff possível; se `platforms` não existir, crie só o necessário) com:
```
platforms:
  webhook:
    enabled: true
    extra:
      port: 8646
      rate_limit: 30
      routes:
        strategist-event:
          prompt: |
            Evento severo recebido do betrader (sentinela disparou). Payload:
            {__raw__}
            RE-DECIDA a estratégia à luz do evento (não a ordem):
            1) rode `python scripts/strategist_cycle.py brief`;
            2) leia workspace/brief.json, reavalie posição/risco/risk_state e escreva
               workspace/proposal.json no schema StrategyProposal (toda entry exige stop_loss;
               sem edge claro, proposta vazia com reasoning é válida);
            3) rode `python scripts/strategist_cycle.py execute workspace/proposal.json`;
            4) leia o JSON de resultado e reporte no canal o evento, a decisão e o gate.
          deliver: "local"
```
CONSTRAINTS CRÍTICAS:
- NÃO coloque nenhum secret no arquivo (ele é versionado). A rota herda o secret global da env `WEBHOOK_SECRET` (configurada no compose/.env na Task 1.3) — o engine valida o esquema Generic (`X-Webhook-Signature`). NÃO adicione campo `secret:` com valor literal.
- Porta 8646 é loopback-only (não será exposta) — apenas o shim a alcança.
- NÃO toque em nenhuma outra seção do config além de `platforms.webhook`. Preserve indentação/estilo YAML existente. O engine gera `.bak` automático.

RETORNE quando `cd /Users/fabiosiqueira/dev/projetos/hermes/binance-project && python -c "import yaml;w=yaml.safe_load(open('hermes/config.yaml'))['platforms']['webhook'];assert w['enabled'] and w['extra']['port']==8646 and 'strategist-event' in w['extra']['routes'] and w['extra']['routes']['strategist-event'].get('prompt')"` sair 0. Reporte o diff aplicado.
```

#### Task 1.3: Wiring de infra — sidecar do shim + porta pública + `.env.example` [sonnet]

**Files:**
- Modify: `hermes-compose.local.yml`
- Modify: `.env.example`

**Diagnosis:** O shim precisa rodar no mesmo namespace de rede do `gateway` (para alcançar `127.0.0.1:8646`) e ter a porta 8645 exposta. Sidecar com `network_mode: "service:gateway"` evita tocar o Dockerfile. `.env.example` documenta as novas chaves sem valores.

**Verification:** `docker compose -f hermes-compose.local.yml config -q && grep -q 'BETRADER_WEBHOOK_SECRET' .env.example && grep -q 'WEBHOOK_PUBLIC_URL' .env.example && grep -q 'WEBHOOK_SECRET' .env.example && grep -q '8645' hermes-compose.local.yml`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project. INFRA — aprovada pelo operador para este plano; ainda assim MENOR diff possível e NÃO toque no Dockerfile.

Tarefa: wirar o webhook shim no compose como sidecar do gateway, expor a porta pública, e documentar as novas env vars.

Leia ANTES: `hermes-compose.local.yml` (note o service `gateway`: build target `local`, command `["gateway","run"]`, env_file `hermes/.env`, ports 8644/9468, network `binance`) e `.env.example`.

1) Em `hermes-compose.local.yml`:
   - Exponha a porta pública do shim adicionando `"8645:8645"` à lista `ports` do service `gateway` (a porta 8646 do webhook nativo NÃO é exposta — é loopback-only).
   - Adicione as env vars do webhook nativo ao `environment` do `gateway`: `WEBHOOK_ENABLED: "true"`, `WEBHOOK_PORT: "8646"`, `WEBHOOK_SECRET: ${BETRADER_WEBHOOK_SECRET}` (interpolado do .env).
   - Adicione um novo service `webhook-shim` que: usa o MESMO build/imagem do gateway (`build: {context: ., dockerfile: Dockerfile, target: local}`), `network_mode: "service:gateway"` (compartilha o netns → alcança 127.0.0.1:8646 e publica via o gateway), `command: ["sh","-c","python scripts/webhook_shim.py"]` com working dir do data dir (volume `./hermes:/opt/data`, e o engine roda com cwd no data dir — espelhe como o gateway acessa `scripts/`), `env_file: [hermes/.env]`, `depends_on: [gateway]`. NÃO declare `ports` nem `networks` no sidecar (herdados via network_mode).
     IMPORTANTE: com `network_mode: service:gateway`, o sidecar NÃO pode ter `ports`/`networks` próprios — a porta 8645 é publicada no service `gateway` (passo acima). Verifique se o shim precisa de `HERMES_DATA_DIR`/cwd para achar `scripts/webhook_shim.py`; replique o que o gateway usa (`HERMES_DATA_DIR: /opt/data`, volume `./hermes:/opt/data`).

2) Em `.env.example` adicione (sem valores reais, com comentário):
   - `BETRADER_WEBHOOK_SECRET=whsec_xxx`  (secret HMAC compartilhado: betrader assina ↔ shim verifica ↔ engine re-valida)
   - `WEBHOOK_SECRET=whsec_xxx`  (mesmo valor de BETRADER_WEBHOOK_SECRET; secret global do webhook nativo do engine)
   - `WEBHOOK_PUBLIC_URL=https://<host-publico>/hook/betrader`  (URL que as automations-sentinela do betrader chamam)

CONSTRAINTS:
- NÃO altere portas existentes (8644/9468/6381/9121) nem o service `redis`/`dashboard`.
- NÃO ponha valores de secret reais em nenhum arquivo versionado.
- Preserve o estilo/indentação YAML e os comentários de cabeçalho do compose.

RETORNE quando `cd /Users/fabiosiqueira/dev/projetos/hermes/binance-project && docker compose -f hermes-compose.local.yml config -q && grep -q BETRADER_WEBHOOK_SECRET .env.example && grep -q WEBHOOK_PUBLIC_URL .env.example && grep -q 8645 hermes-compose.local.yml` sair 0. Reporte o diff dos dois arquivos.
```

### Cluster 2 — Sentinelas (betrader emite o evento)

**Inter-cluster dependency:** none

#### Task 2.1: Injeção determinística de `webhookUrl`/`webhookSecret` nas actions WEBHOOK [opus] +reviewer

**Files:**
- Modify: `hermes/scripts/betrader_client.py`
- Modify: `tests/unit/test_betrader_client.py`

**Diagnosis:** `install_automations` repassa `spec.action` cru ao POST. Para sentinelas `WEBHOOK`, a action precisa de `webhookUrl`/`webhookSecret`, mas o LLM NÃO pode ver/escrever o secret. A camada determinística injeta esses campos a partir do env quando `action["type"] == "WEBHOOK"`, sem mutar o dict do LLM.

**Verification:** `python -m pytest tests/unit/test_betrader_client.py -q -k webhook`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project (Python). Código de PRODUÇÃO financeira — disciplina total.

Tarefa: em `hermes/scripts/betrader_client.py`, fazer `install_automations` injetar deterministicamente `webhookUrl` e `webhookSecret` nas actions do tipo WEBHOOK, lendo do ambiente, SEM expor o secret ao LLM e SEM mutar o dict recebido.

Leia ANTES e copie o estilo: `hermes/scripts/betrader_client.py` (método `install_automations` ~linha 425; classe `BetraderError`; uso de `os.environ`; `from_env`), `hermes/scripts/schemas.py` (`AutomationSpec`: `name`, `condition` validado por regex Beholder, `action: dict`, `schedule`), e `tests/unit/test_betrader_client.py` (respx, fixtures, asserts observáveis).

MUDANÇA em `install_automations(self, automations)`:
- Para cada `spec` cujo `spec.action.get("type") == "WEBHOOK"`: construir um NOVO dict de action (NÃO mutar `spec.action` — immutability) com os campos originais MAIS `webhookUrl` e `webhookSecret` lidos de `os.environ["WEBHOOK_PUBLIC_URL"]` e `os.environ["BETRADER_WEBHOOK_SECRET"]`. Se qualquer uma das duas env vars estiver ausente/vazia, levantar `BetraderError("missing_webhook_config", ...)` ANTES do POST (não instalar sentinela quebrada). Actions de outros tipos passam inalteradas.
- O resto (POST /api/automations com `{"newAutomation": {...}}`, `/start`, coleta de ids) permanece idêntico.

CONSTRAINTS:
- NUNCA logar/expor o secret (segue o padrão do token: nunca em repr/log).
- NÃO mutar `spec.action` nem nenhum input — criar dict novo (`{**spec.action, "webhookUrl": url, "webhookSecret": secret}`).
- NÃO alterar a assinatura pública do método nem outros métodos. NÃO tocar `risk_engine.py`/`dogmas.yaml`/`schemas.py`.
- Testes (em `tests/unit/test_betrader_client.py`, nomes contendo "webhook" para o `-k webhook`): (a) action WEBHOOK recebe webhookUrl/webhookSecret do env no corpo POSTado (assert via respx no payload `actions[0]`), e o dict original do spec NÃO foi mutado; (b) action não-WEBHOOK passa intacta; (c) env ausente → `BetraderError("missing_webhook_config")` e nenhum POST. Use monkeypatch para env.

RETORNE quando `cd /Users/fabiosiqueira/dev/projetos/hermes/binance-project && python -m pytest tests/unit/test_betrader_client.py -q -k webhook` sair 0. Reporte o diff e a linha final do pytest.
```

#### Task 2.2: Guidance event-driven no `hermes/AGENTS.md` [sonnet]

**Files:**
- Modify: `hermes/AGENTS.md`

**Diagnosis:** O HAWK precisa saber (a) que existe o ramo por evento, (b) como propor automations-sentinela WEBHOOK com `condition` no formato Beholder usando os índices de MEMORY que o betrader publica (`LAST_LIQ`, `POSITION_LIQ_PRICE`, mark price), e (c) que NÃO escreve o secret (a camada determinística injeta).

**Verification:** `grep -qi 'WEBHOOK' hermes/AGENTS.md && grep -qiE 'sentinel|LAST_LIQ|por evento|event-driven' hermes/AGENTS.md`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project. Documento de persona/contexto operacional do agente (PT-BR).

Tarefa: adicionar ao `hermes/AGENTS.md` uma seção curta sobre o ramo POR EVENTO do ciclo (F1), em PT-BR direto, casando o tom e a estrutura do arquivo existente.

Leia ANTES: `hermes/AGENTS.md` inteiro (note a seção "Ciclo do estrategista (cron 15m)" e o estilo). Leia também `hermes/scripts/schemas.py` (`AutomationSpec.condition` — regex `MEMORY['SYMBOL:INDICATOR_params'] <op> valor`).

Adicione uma subseção (ex.: "Ciclo por evento (F1)") explicando:
- Além do cron 15m, o betrader pode me ACORDAR via webhook quando uma automation-sentinela dispara. Recebo um prompt com o payload do evento e devo rodar o MESMO ciclo (brief → proposal → execute) para re-decidir a estratégia (não a ordem).
- Para armar sentinelas, proponho no `StrategyProposal.automations` uma `AutomationSpec` com `action: {"type": "WEBHOOK"}` (a infra injeta `webhookUrl`/`webhookSecret` automaticamente — eu NUNCA escrevo o secret). A `condition` segue o formato Beholder e usa índices que o betrader publica em MEMORY, ex.: proximidade de liquidação via `MEMORY['BTCUSDT:POSITION_LIQ_PRICE']`, ou um nível de preço/mark. Eventos típicos: stop prestes a disparar, posição perto de liquidação.
- Drawdown do meu equity-curve NÃO é visível ao betrader — esse alerta vem do meu próprio monitor (não preciso armá-lo como sentinela betrader).

CONSTRAINTS:
- NÃO reescreva seções existentes; só ADICIONE a subseção nova no lugar coerente (perto do "Ciclo do estrategista"). Menor diff.
- Não invente endpoints/campos: use só o que está em schemas.py e no resto do AGENTS.md.

RETORNE quando `cd /Users/fabiosiqueira/dev/projetos/hermes/binance-project && grep -qi WEBHOOK hermes/AGENTS.md && grep -qiE 'sentinel|LAST_LIQ|por evento|event-driven' hermes/AGENTS.md` sair 0. Reporte o trecho adicionado.
```

### Cluster 3 — Drawdown-80% self-trigger

**Inter-cluster dependency:** none

#### Task 3.1: Self-trigger de wake ao cruzar 80% do limite de drawdown [sonnet]

**Files:**
- Modify: `hermes/scripts/observability.py`
- Modify: `hermes/scripts/strategist_cycle.py`
- Modify: `tests/unit/test_observability.py`

**Diagnosis:** O betrader não enxerga nosso drawdown (equity-curve no Redis). O ciclo computa `FinancialState.drawdown_pct`; ao cruzar 80% de `dogmas.max_daily_drawdown_pct`, disparamos o MESMO caminho de wake fazendo um POST assinado ao shim (`WEBHOOK_PUBLIC_URL`, header `X-Beholder-Signature`), reusando a verificação do shim. `strategist_cycle` chama o helper após carregar estado/dogmas.

**Verification:** `python -m pytest tests/unit/test_observability.py -q -k 'drawdown or breach'`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project (Python).

Tarefa: disparar um "wake" quando o drawdown do nosso equity-curve cruzar 80% do limite dos dogmas — via POST assinado ao mesmo shim do webhook (o betrader não enxerga nosso drawdown).

Leia ANTES e copie o estilo: `hermes/scripts/observability.py` (classe `FinancialState` com `drawdown_pct`; classe `Observability`; uso de `httpx`? note que `betrader_client` usa httpx — use httpx aqui também; `record_error`), `hermes/scripts/strategist_cycle.py` (`_cmd_execute`: carrega `dogmas`, `state = FinancialState.load(...)`), `hermes/scripts/schemas.py` (`Dogmas.max_daily_drawdown_pct`), `tests/unit/test_observability.py` (estilo, fakeredis).

1) Em `observability.py`, adicione uma função/método (ex.: módulo-level `maybe_trigger_drawdown_wake(state: FinancialState, max_daily_drawdown_pct: float, *, on_error=None) -> bool`):
   - Calcula `threshold = 0.8 * max_daily_drawdown_pct`. Se `state.drawdown_pct >= threshold` (e threshold > 0), faz POST a `os.environ["WEBHOOK_PUBLIC_URL"]` com body JSON `{"source":"drawdown_monitor","type":"drawdown.threshold","drawdown_pct":..., "limit_pct":...}` e header `X-Beholder-Signature: sha256=<hmac>` onde `<hmac> = HMAC-SHA256(BETRADER_WEBHOOK_SECRET, raw_body)` hex (MESMO esquema que o betrader usa, para o shim aceitar). Retorna True se disparou.
   - Se `WEBHOOK_PUBLIC_URL`/`BETRADER_WEBHOOK_SECRET` ausentes, ou DD abaixo do threshold: no-op, retorna False. Falha de rede no POST → `on_error("drawdown_wake_error")` e retorna False (não derruba o ciclo).
   - NUNCA logar o secret.
2) Em `strategist_cycle.py`, dentro de `_cmd_execute`, APÓS carregar `dogmas` e `state` e ANTES (ou junto) do gate, chamar `maybe_trigger_drawdown_wake(state, dogmas.max_daily_drawdown_pct, on_error=observability.record_error)`. Não alterar o contrato de stdout do ciclo (não imprime nada extra; o disparo é side-effect). Menor diff.

CONSTRAINTS:
- Mock só na fronteira HTTP (respx) e Redis (fakeredis) nos testes. DI real no resto.
- NÃO tocar `risk_engine.py`/`dogmas.yaml`. Não mudar a saída JSON do ciclo.
- Immutability: não mutar inputs.
- Testes (nomes contendo "drawdown"/"breach"): (a) DD ≥ 80% do limite → POST disparado com header X-Beholder-Signature válido (assert respx) → retorna True; (b) DD abaixo → nenhum POST, False; (c) env ausente → no-op False; (d) falha de rede → on_error chamado, sem exceção propagada.

RETORNE quando `cd /Users/fabiosiqueira/dev/projetos/hermes/binance-project && python -m pytest tests/unit/test_observability.py -q -k 'drawdown or breach'` sair 0. Reporte diffs e linha final do pytest.
```

### Cluster 4 — E2E + verificação

**Inter-cluster dependency:** depends on Cluster 1, Cluster 2, Cluster 3

#### Task 4.1: E2E de lifecycle por evento [opus]

**Files:**
- Create: `tests/e2e/test_lifecycle_event.py`

**Diagnosis:** Falta o E2E do ramo por evento (separado do E2E agendado de M1). Cobre: POST assinado do betrader → shim verifica → re-assina → forward ao webhook nativo (mockado) → assert; e a instalação de sentinela WEBHOOK com injeção de url/secret. Integração ponta-a-ponta das Clusters 1–3.

**Verification:** `python -m pytest tests/e2e/test_lifecycle_event.py -q`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project (Python). Depende de: webhook_shim.py (Task 1.1), injeção WEBHOOK em betrader_client.install_automations (Task 2.1), maybe_trigger_drawdown_wake em observability.py (Task 3.1) — todos já implementados quando este rodar.

Tarefa: criar `tests/e2e/test_lifecycle_event.py` — E2E do ramo POR EVENTO, separado do E2E agendado `tests/e2e/test_lifecycle_dry_run.py`.

Leia ANTES e espelhe o estilo: `tests/e2e/test_lifecycle_dry_run.py` (narrativa, fixtures de payload, respx, fakeredis, monkeypatch de env, asserts de comportamento observável), `hermes/scripts/webhook_shim.py`, `hermes/scripts/betrader_client.py` (`install_automations`), `hermes/scripts/observability.py` (`maybe_trigger_drawdown_wake`, `FinancialState`).

Cubra como narrativa E2E:
1) "betrader acorda o Hermes": construir um body JSON de evento, assiná-lo com `X-Beholder-Signature: sha256=<hmac(BETRADER_WEBHOOK_SECRET, body)>`, chamar a função de verificação/handler do shim (use `verify_signature` e o forward — mocke o engine via respx em http://127.0.0.1:8646/webhooks/strategist-event). Assert: assinatura válida → forward chamado com header `X-Webhook-Signature` hex puro e MESMO body → 200; assinatura adulterada → 401 e forward NÃO chamado.
2) "estrategista arma a sentinela": via `BetraderClient.install_automations` (respx na fronteira betrader, env WEBHOOK_PUBLIC_URL/BETRADER_WEBHOOK_SECRET via monkeypatch) com uma `AutomationSpec` `action={"type":"WEBHOOK"}` → assert que o POST /api/automations carregou `webhookUrl`/`webhookSecret` injetados e a action original não foi mutada.
3) "drawdown nos acorda": montar `FinancialState` com DD ≥ 80% do limite, chamar `maybe_trigger_drawdown_wake` (respx no WEBHOOK_PUBLIC_URL) → assert POST assinado disparado.

CONSTRAINTS:
- Mock só nas fronteiras (respx HTTP, fakeredis Redis, monkeypatch env). DI real no resto. Imports flat.
- Secret de teste é valor fixo local. NÃO tocar código de produção — só criar o arquivo de teste.

RETORNE quando `cd /Users/fabiosiqueira/dev/projetos/hermes/binance-project && python -m pytest tests/e2e/test_lifecycle_event.py -q` sair 0. Reporte os cenários cobertos e a linha final do pytest.
```

## Launch order (DAG resolved)

### Phase 0 — parallel

- Cluster 1 / Task 1.1 (`webhook_shim.py`) [opus] +reviewer
- Cluster 1 / Task 1.2 (`config.yaml` rota) [sonnet]
- Cluster 1 / Task 1.3 (compose + `.env.example`) [sonnet]
- Cluster 2 / Task 2.1 (injeção WEBHOOK em `betrader_client`) [opus] +reviewer
- Cluster 2 / Task 2.2 (guidance `AGENTS.md`) [sonnet]
- Cluster 3 / Task 3.1 (drawdown self-trigger) [sonnet]

**Fan-out Phase 0: 6 parallel tasks**

### Phase 1 — after Phase 0 completes

- Cluster 4 / Task 4.1 (E2E event-driven) [opus]

## Notas de execução / riscos a verificar contra o engine rodando

- **Esquema de assinatura do engine (Generic):** o plano assume que o engine valida `X-Webhook-Signature` como HMAC-SHA256 hex puro sobre o corpo raw. Confirmar no primeiro deploy (logs do gateway ao receber um forward do shim). Se o engine usar outra convenção de digest, ajustar `build_forward_signature` (Task 1.1) — é o único ponto de acoplamento.
- **`network_mode: service:gateway` + publicação de porta:** a porta 8645 é publicada no service `gateway` (não no sidecar). Confirmar que o shim, no netns do gateway, enxerga `127.0.0.1:8646` (webhook nativo) — é o motivo do sidecar.
- **Bind do webhook nativo:** como só o shim o acessa (loopback) e ele exige secret (Generic), não usamos `INSECURE_NO_AUTH`. Se o engine recusar bind, a porta 8646 segue interna ao container do gateway.
