# Plan — Reduzir burn autônomo de tokens LLM (cron 15m → heartbeat 4h + deliver Telegram + max_turns cap)

## Metadata

- **Generated:** 2026-06-11
- **Worktree:** none

## Context

Projeto `binance-project` (`/Users/fabiosiqueira/dev/projetos/hermes/binance-project`) — overlay do Hermes Agent (HAWK, estrategista Binance Futures). Data dir do agente em `hermes/` (montado como `/opt/data` no container). Prod no Coolify (app `hermes-binance`, uuid `dcvrz0exi8hqzq8jbxd0x9xw`, server Contabo, SSH host `contabo`), build_pack dockercompose, volume nomeado `hermes-data` semeado da imagem **só no 1º boot** — edits no repo NÃO propagam pro volume vivo via rebuild.

Causa raiz do burn: cron `strategist-candle-15m` (`*/15 * * * *`) = ~96 sessões completas de agente/dia no plano MiniMax (modelo `minimax-m3`), com `agent.max_turns: 90` e `deliver: "local"` (reportes invisíveis ao operador — Telegram está wireado mas o deliver não aponta pra ele). Serviço atualmente **parado** no Coolify (containers removidos).

Semântica confirmada no engine (`~/dev/projetos/hermes/hermes-engine`): `deliver: "telegram"` (cron `cron/scheduler.py` e webhook `gateway/delivery.py`) entrega no `TELEGRAM_HOME_CHANNEL`. Run manual: `hermes cron run <job_id>`.

## Baseline (current state)

```bash
grep -o '"expr": "[^"]*"' hermes/cron/jobs.json          # "expr": "*/15 * * * *"
grep -o '"deliver": "[^"]*"' hermes/cron/jobs.json        # "deliver": "local"
grep -n 'max_turns:' hermes/config.yaml | head -1         # max_turns: 90
grep -n 'deliver: "local"' hermes/config.yaml             # 602 (rota webhook strategist-event)
# Prod: app exited (parado manualmente em 2026-06-11 via Coolify para estancar o burn)
```

## Objective

Reduzir o consumo autônomo de ~96 para 6 ciclos agendados/dia (heartbeat a cada 4h), entregar todos os reportes (cron e webhook) no home channel do Telegram, capar `max_turns` em 25 como defesa contra ciclo patológico — e propagar tudo isso tanto no repo quanto no volume vivo de prod, religando o serviço com verificação ponta-a-ponta.

## Definition of Done (global)

Single verifiable command:

```bash
ssh -o ConnectTimeout=10 contabo 'C=$(docker ps --filter "name=gateway-dcvrz0exi8hqzq8jbxd0x9xw" --format "{{.Names}}" | grep -v risk | head -1); test -n "$C" && docker exec "$C" python3 -c "
import json,sys
d=json.load(open(\"/opt/data/cron/jobs.json\"))
j=[x for x in d[\"jobs\"] if x[\"id\"]==\"a1b2c3d4e5f6\"][0]
assert j[\"schedule\"][\"expr\"]==\"0 */4 * * *\", j[\"schedule\"][\"expr\"]
assert j[\"deliver\"]==\"telegram\", j[\"deliver\"]
cfg=open(\"/opt/data/config.yaml\").read()
assert \"max_turns: 25\" in cfg
assert chr(100)+\"eliver: \\\"local\\\"\" not in cfg
print(\"DOD-OK\")
"' | grep -q DOD-OK && [ -z "$(git -C /Users/fabiosiqueira/dev/projetos/hermes/binance-project status --porcelain --untracked-files=no)" ] && echo DOD-OK
```

**Expected output:** `DOD-OK` (volume de prod sincronizado e working tree limpo).

## Policy (invariant)

- **Mudança cirúrgica e mínima** — só os campos/linhas listados em cada task. Zero refactor, zero rename além do especificado.
- **NÃO tocar**: `Dockerfile`, `docker-compose.yaml`, `hermes-compose.local.yml`, `hermes/dogmas.yaml`, `hermes/scripts/*` (código de produção), `.env*`.
- **SSH sempre síncrono** (nunca `run_in_background`); descoberta de container via `docker ps --filter 'name=<prefix>'`, nunca UUID/nome hardcoded de container.
- Segredos nunca em arquivo versionado nem em output de task.
- PT-BR em textos de persona/docs do agente; termos técnicos em inglês.

## Dependency justification

- **Cluster 2 blockedBy Cluster 1:** o commit/deploy (2.1) consome os arquivos editados em 1.1/1.2/1.3 — sem eles, o deploy não carrega o fix.
- **Task 2.2 blockedBy Task 2.1:** o sync do volume exige containers de prod em pé, que só existem após o deploy/start do 2.1 (o stop do Coolify removeu os containers).
- **Cluster 3 blockedBy Cluster 2:** a verificação ponta-a-ponta consome o serviço religado e o volume já sincronizado por 2.2.

## Clusters

### Cluster 1 — Fix no repo (fonte de verdade)

**Inter-cluster dependency:** none

#### Task 1.1: Reconfigurar cron job para heartbeat 4h com deliver Telegram [sonnet]

**Files:**
- Modify: `hermes/cron/jobs.json`

**Diagnosis:** O job `strategist-candle-15m` roda `*/15 * * * *` com `deliver: "local"` — 96 sessões LLM/dia invisíveis ao operador. Vira heartbeat de 4h entregue no Telegram; o id `a1b2c3d4e5f6` é preservado (referência estável).

**Verification:** `python3 -c "import json; j=json.load(open('hermes/cron/jobs.json'))['jobs'][0]; assert j['schedule']['expr']=='0 */4 * * *' and j['deliver']=='telegram' and j['name']=='strategist-heartbeat-4h' and j['id']=='a1b2c3d4e5f6'; print('OK')"`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project
Arquivo único a modificar: hermes/cron/jobs.json (JSON com um único job, id "a1b2c3d4e5f6").

Faça EXATAMENTE estas mudanças no job, preservando todos os demais campos:
1. "name": "strategist-candle-15m" → "strategist-heartbeat-4h"
2. "schedule".​"expr": "*/15 * * * *" → "0 */4 * * *"
3. "schedule".​"display": "*/15 * * * *" → "0 */4 * * *"
4. "schedule_display": "*/15 * * * *" → "0 */4 * * *"
5. "deliver": "local" → "telegram"
6. No campo "prompt", troque APENAS o prefixo "Ciclo do estrategista (candle 15m fechou):" por "Ciclo do estrategista (heartbeat de 4h):" — o restante do prompt fica intocado.

Constraints: não altere "id", não reformate o JSON além do necessário, não adicione/remova campos, não toque em nenhum outro arquivo.

Verificação — retorne quando este comando sair com exit 0:
python3 -c "import json; j=json.load(open('hermes/cron/jobs.json'))['jobs'][0]; assert j['schedule']['expr']=='0 */4 * * *' and j['deliver']=='telegram' and j['name']=='strategist-heartbeat-4h' and j['id']=='a1b2c3d4e5f6'; print('OK')"

Output esperado de você: resumo de 2-3 linhas das mudanças + confirmação do comando de verificação com saída OK.
```

#### Task 1.2: Cap de max_turns e deliver Telegram na rota webhook [haiku]

**Files:**
- Modify: `hermes/config.yaml`

**Diagnosis:** `agent.max_turns: 90` permite que um único ciclo patológico custe como ~20 heartbeats; o ciclo brief→proposal→execute→report cabe folgado em 25 turns. A rota webhook `strategist-event` também entrega em `local` (linha 602) — mesmo problema de invisibilidade do cron.

**Verification:** `grep -q 'max_turns: 25' hermes/config.yaml && ! grep -q 'deliver: "local"' hermes/config.yaml && echo OK`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project
Arquivo único a modificar: hermes/config.yaml

Faça EXATAMENTE duas edições de linha:
1. Linha ~13, dentro do bloco `agent:`: `  max_turns: 90` → `  max_turns: 25`
2. Linha ~602, dentro de `platforms.webhook.extra.routes.strategist-event`: `          deliver: "local"` → `          deliver: "telegram"`

Constraints: preserve indentação YAML exata; não reordene chaves; não altere nenhuma outra linha (o arquivo tem outras ocorrências de "deliver"-like e "turns" — toque só nas duas linhas indicadas); não toque em outros arquivos.

Verificação — retorne quando este comando sair com exit 0:
grep -q 'max_turns: 25' hermes/config.yaml && ! grep -q 'deliver: "local"' hermes/config.yaml && python3 -c "import yaml; yaml.safe_load(open('hermes/config.yaml'))" && echo OK

Output esperado de você: as duas linhas alteradas (antes/depois) + confirmação OK.
```

#### Task 1.3: Alinhar AGENTS.md à nova cadência [haiku]

**Files:**
- Modify: `hermes/AGENTS.md`

**Diagnosis:** AGENTS.md é doc LLM-facing carregada pelo agente; gotcha conhecido do projeto é drift entre docs e realidade. Três referências citam o job antigo/cadência 15m (linhas 54, 59, 115).

**Verification:** `! grep -q 'strategist-candle-15m' hermes/AGENTS.md && grep -q 'strategist-heartbeat-4h' hermes/AGENTS.md && echo OK`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project
Arquivo único a modificar: hermes/AGENTS.md (doc em PT-BR carregada pelo agente em runtime)

Três edições pontuais:
1. Linha ~54: troque "(`jobs.json`, ex.: `strategist-candle-15m` em `*/15`)" por "(`jobs.json`, ex.: `strategist-heartbeat-4h` em `0 */4`)".
2. Linha ~59: na frase "O cron (`cron/jobs.json`, job `strategist-candle-15m`, expr `*/15 * * * *`) dispara a cada fechamento de candle." troque para "O cron (`cron/jobs.json`, job `strategist-heartbeat-4h`, expr `0 */4 * * *`) dispara como heartbeat periódico de revisão." — mantenha o restante do parágrafo (SYMBOL/TIMEFRAME) intocado.
3. Linha ~115: troque "Além do cron de 15m, o betrader pode me **acordar via webhook**" por "Além do heartbeat de 4h, o betrader pode me **acordar via webhook**".

IMPORTANTE: a env `TIMEFRAME` (default `15m`, linha ~149) refere-se ao timeframe do candle do brief, NÃO à cadência do cron — não a altere. Não mude mais nada no arquivo.

Verificação — retorne quando este comando sair com exit 0:
! grep -q 'strategist-candle-15m' hermes/AGENTS.md && grep -q 'strategist-heartbeat-4h' hermes/AGENTS.md && ! grep -qE 'cron de 15m' hermes/AGENTS.md && echo OK

Output esperado de você: as 3 linhas alteradas (antes/depois) + confirmação OK.
```

### Cluster 2 — Deploy + sync do volume vivo

**Inter-cluster dependency:** depends on Cluster 1

#### Task 2.1: Commit, push e deploy no Coolify [sonnet]

**Files:**
- Modify: nenhum (git + Coolify apenas)

**Diagnosis:** Repo é a fonte de deploy (Coolify app `dcvrz0exi8hqzq8jbxd0x9xw`, build_pack dockercompose, branch main). O deploy rebuilda a imagem (que ganha os arquivos novos via `COPY hermes/ /opt/data/`) e sobe o stack — mas o volume `hermes-data` existente NÃO é re-semeado (gotcha conhecido).

**Verification:** `git -C /Users/fabiosiqueira/dev/projetos/hermes/binance-project status --porcelain | grep -q . && exit 1; ssh -o ConnectTimeout=10 contabo 'docker ps --filter "name=gateway-dcvrz0exi8hqzq8jbxd0x9xw" --format "{{.Names}}" | grep -q gateway' && echo OK`

**Prompt for subagent (Agent tool):**
```
Projeto: /Users/fabiosiqueira/dev/projetos/hermes/binance-project (branch main; remote origin = github fabiosiqueira/hermes-binance)

Tarefa em 3 passos:
1. Commit: stage SOMENTE hermes/cron/jobs.json, hermes/config.yaml, hermes/AGENTS.md. Mensagem:
   "fix(binance-project): heartbeat 4h + deliver telegram + max_turns 25 (corta burn de tokens ~94%)"
   seguida de linha em branco e "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>". NÃO commitar docs/ nem nada além dos 3 arquivos.
2. Push para origin main.
3. Deploy no Coolify prod: use a tool MCP mcp__coolify-prod__deploy com uuid "dcvrz0exi8hqzq8jbxd0x9xw". Acompanhe via mcp__coolify-prod__deployment / mcp__coolify-prod__list_deployments até status finished (timeout razoável ~10min; o build herda imagem ghcr e é rápido). Se a tool deploy não aceitar uuid diretamente, consulte mcp__coolify-prod__deploy (schema) — NÃO improvise workaround via SSH para deploy.

Constraints: nenhuma edição de arquivo; não tocar Dockerfile/composes; não criar branch; SSH apenas para a verificação final, sempre síncrono.

Verificação — retorne quando este comando sair com exit 0:
ssh -o ConnectTimeout=10 contabo 'docker ps --filter "name=gateway-dcvrz0exi8hqzq8jbxd0x9xw" --format "{{.Names}} {{.Status}}" | grep -v risk | grep -q "Up"' && echo OK

Output esperado de você: hash do commit, confirmação do push, id+status do deployment, e a saída OK da verificação.
```

#### Task 2.2: Sincronizar volume hermes-data em prod e recarregar o gateway [sonnet]

**Intra-cluster dependency:** 2.1

**Files:**
- Modify: nenhum no repo (volume remoto via docker exec)

**Diagnosis:** O volume `hermes-data` persiste o jobs.json/config.yaml ANTIGOS (cron 15m volta a rodar assim que o stack sobe). Aplicar no volume vivo as mesmas mudanças das tasks 1.1/1.2/1.3 e reiniciar o container do gateway para o scheduler recarregar. Fazer isso imediatamente após o 2.1 minimiza ciclos de 15m disparados no intervalo.

**Verification:** `ssh -o ConnectTimeout=10 contabo 'C=$(docker ps --filter "name=gateway-dcvrz0exi8hqzq8jbxd0x9xw" --format "{{.Names}}" | grep -v risk | head -1); docker exec "$C" sh -c "grep -q \"0 \\*/4\" /opt/data/cron/jobs.json && grep -q \"max_turns: 25\" /opt/data/config.yaml"' && echo OK`

**Prompt for subagent (Agent tool):**
```
Contexto: app Coolify "hermes-binance" em prod (server SSH host "contabo"). O volume hermes-data (montado em /opt/data no container do gateway) tem versões ANTIGAS de cron/jobs.json, config.yaml e AGENTS.md; o repo local /Users/fabiosiqueira/dev/projetos/hermes/binance-project/hermes/ tem as versões NOVAS (já deployadas na imagem, mas o volume não re-semeia).

Tarefa:
1. Descubra o container do gateway dinamicamente (nunca hardcode nome):
   ssh contabo 'docker ps --filter "name=gateway-dcvrz0exi8hqzq8jbxd0x9xw" --format "{{.Names}}" | grep -v risk | head -1'
2. Copie os 3 arquivos novos do repo local para dentro do volume, preservando ownership:
   - hermes/cron/jobs.json → /opt/data/cron/jobs.json
   - hermes/config.yaml   → /opt/data/config.yaml
   - hermes/AGENTS.md     → /opt/data/AGENTS.md
   Método sugerido (síncrono): scp do arquivo local para contabo:/tmp/, depois `docker cp /tmp/<f> $C:/opt/data/<path>` e `docker exec -u root $C chown hermes:hermes /opt/data/<path>`. 
   EXCEÇÃO ao jobs.json: antes de copiar, zere o estado de runtime — a partir do hermes/cron/jobs.json do repo, gere /tmp/jobs.json com "next_run_at": null e "last_run_at"/"last_status" preservados como null (o repo já os tem null; apenas confirme).
   CUIDADO config.yaml: o config.yaml do volume pode ter sido mutado em runtime pelo agente (ex.: _config_version). Antes de sobrescrever, faça diff: `docker exec $C cat /opt/data/config.yaml > /tmp/cfg-prod.yaml; diff /tmp/cfg-prod.yaml hermes/config.yaml`. Se o diff mostrar APENAS as mudanças esperadas (max_turns 90→25, deliver local→telegram, e ruído trivial), prossiga com a cópia. Se houver divergência substantiva (chaves novas/valores diferentes no lado prod), NÃO sobrescreva — aplique só as 2 edições no arquivo de prod via sed in-place dentro do container e reporte o diff no seu output.
3. Reinicie SÓ o container do gateway: ssh contabo "docker restart $C". Aguarde ~30s e confirme que voltou Up e healthy (docker ps).

Constraints: SSH sempre síncrono (nunca run_in_background); não tocar nos containers risk-gateway/redis/risk-redis/webhook-shim; não editar nada no repo local; não expor conteúdo de .env.

Verificação — retorne quando este comando sair com exit 0:
ssh -o ConnectTimeout=10 contabo 'C=$(docker ps --filter "name=gateway-dcvrz0exi8hqzq8jbxd0x9xw" --format "{{.Names}}" | grep -v risk | head -1); docker exec "$C" sh -c "grep -q \"0 \*/4\" /opt/data/cron/jobs.json && grep -q \"max_turns: 25\" /opt/data/config.yaml && ! grep -q '\''deliver: \"local\"'\'' /opt/data/config.yaml"' && echo OK

Output esperado de você: nome do container, resultado do diff do config.yaml, método aplicado (cópia ou sed), confirmação do restart + status Up, e a saída OK da verificação.
```

### Cluster 3 — Verificação ponta-a-ponta

**Inter-cluster dependency:** depends on Cluster 2

#### Task 3.1: Disparo manual do heartbeat e confirmação de cadência + Telegram [sonnet]

**Files:**
- Modify: nenhum

**Diagnosis:** O próximo heartbeat agendado pode estar horas à frente; um run manual (`hermes cron run a1b2c3d4e5f6`) valida já o ciclo completo e a entrega no Telegram, e o `next_run_at` confirma a cadência de 4h.

**Verification:** `ssh -o ConnectTimeout=10 contabo 'C=$(docker ps --filter "name=gateway-dcvrz0exi8hqzq8jbxd0x9xw" --format "{{.Names}}" | grep -v risk | head -1); docker exec "$C" python3 -c "import json; j=json.load(open(\"/opt/data/cron/jobs.json\"))[\"jobs\"][0]; assert j[\"last_status\"]==\"ok\" and j[\"next_run_at\"], (j[\"last_status\"], j[\"next_run_at\"]); print(\"OK\")"'`

**Prompt for subagent (Agent tool):**
```
Contexto: app "hermes-binance" religado em prod (SSH host "contabo"), cron heartbeat 4h (job id a1b2c3d4e5f6) com deliver "telegram" recém-sincronizado no volume.

Tarefa:
1. Descubra o container do gateway: ssh contabo 'docker ps --filter "name=gateway-dcvrz0exi8hqzq8jbxd0x9xw" --format "{{.Names}}" | grep -v risk | head -1'
2. Liste o estado do cron: docker exec $C hermes cron list — confirme job "strategist-heartbeat-4h" enabled com next_run_at em boundary de 4h (00/04/08/12/16/20h no fuso America/Porto_Velho).
3. Dispare um run manual: docker exec $C hermes cron run a1b2c3d4e5f6. Aguarde a conclusão (poll de jobs.json: last_run_at atualizado e last_status preenchido; o ciclo leva tipicamente 1-5 min; timeout 10 min).
4. Cheque last_status == "ok" e last_delivery_error == null (delivery_error é rastreado separado do erro do agente — se houver delivery_error, o Telegram falhou mesmo com agente ok; reporte o valor).
5. Colete as últimas ~50 linhas de log do gateway (docker logs --tail 50 $C) e verifique ausência de tracebacks relacionados ao job.

Constraints: SSH sempre síncrono; NÃO crie/edite/remova jobs; NÃO altere arquivos; se o run falhar (last_status != ok), NÃO re-dispare em loop — colete o erro (last_error, logs) e reporte como falha.

Verificação — retorne quando este comando sair com exit 0:
ssh -o ConnectTimeout=10 contabo 'C=$(docker ps --filter "name=gateway-dcvrz0exi8hqzq8jbxd0x9xw" --format "{{.Names}}" | grep -v risk | head -1); docker exec "$C" python3 -c "import json; j=json.load(open(\"/opt/data/cron/jobs.json\"))[\"jobs\"][0]; assert j[\"last_status\"]==\"ok\" and not j[\"last_delivery_error\"] and j[\"next_run_at\"]; print(\"OK\")"'

Output esperado de você: next_run_at observado, last_status, last_delivery_error, resumo de 2-3 linhas do log, e a saída OK da verificação. Observação ao operador: confirmar visualmente que a mensagem chegou no home channel do Telegram.
```

## Launch order (DAG resolved)

### Phase 0 — parallel

- Cluster 1 / Task 1.1
- Cluster 1 / Task 1.2
- Cluster 1 / Task 1.3

**Fan-out Phase 0: 3 parallel tasks**

### Phase 1 — after Phase 0 completes

- Cluster 2 / Task 2.1

### Phase 2 — after Task 2.1

- Cluster 2 / Task 2.2

### Phase 3 — after Phase 2 completes

- Cluster 3 / Task 3.1
