# CLAUDE.md

Guidance para o Claude Code (ou qualquer IA de coding) quando trabalhar **neste repo** (`binance-project` / customização Hermes Binance Futures estrategista).

Não é lido pelo HAWK em runtime — quem o agente lê é `AGENTS.md` (dentro do data dir).

## O que este repo é
Overlay / customization layer para rodar um Hermes Agent especializado em **estratégia de Binance Futures via betrader-hydra**.

- Raiz do git (`binance-project/`): `CLAUDE.md` (este guia), `Dockerfile` (herda engine + instala deps Python do estrategista: redis, pydantic, httpx, prometheus-client, pyyaml), `hermes-compose.local.yml` (serviços `gateway`, `dashboard`, `redis`; volume `./hermes:/opt/data`; `REDIS_HOST=redis`; portas não-colidentes), `hermes-coolify.yml`, `.env.example` (template com `BETRADER_TOKEN`, `BETRADER_BASE_URL`, `EXECUTION_MODE`).
- `hermes/`: o data dir real do agente (montado como `/opt/data` dentro do container). É aqui que vivem:
  - `SOUL.md` — persona e limites do HAWK (carregado a cada mensagem).
  - `AGENTS.md` — contexto operacional do agente (onde estão as coisas, redis via env, ciclo do estrategista, betrader REST, etc.).
  - `config.yaml`, `dogmas.yaml`, `scripts/`, `memories/`, `memory/hermes_memory.db`, `workspace/`, `cron/` etc.
- O engine/framework é consumido via imagem pública `ghcr.io/fabiosiqueira/hermes-engine:latest`.

O agente é **estrategista, não trader-no-loop**: lê Brief, propõe StrategyProposal, o gate (`risk_engine.py`) valida e o betrader executa. O HAWK nunca chama a Binance diretamente.

## Ler sempre antes de propor qualquer mudança
1. `hermes/SOUL.md` — identidade, missão, princípios e **limites duros** do HAWK. Qualquer edição que conflite com os limites → pare e pergunte.
2. `hermes/AGENTS.md` — layout exato de paths, como o agente acessa redis (sempre via env), o ciclo do estrategista (brief/proposal/gate/execute), contrato com o betrader.
3. `hermes/config.yaml` — modelo atual, personality "hawk", toolsets, memory etc. Mudança mínima.
4. `docs/superpowers/specs/2026-06-09-hermes-binance-strategist-design.md` — spec de design aprovado: decisões travadas, contratos de dados (Brief/StrategyProposal/Dogmas), superfície da API betrader, escopo M1.
5. `hermes/dogmas.yaml` — constituição de risco. Read-only para edições de código; só o operador modifica.

## Princípios de edição de código (invioláveis)
- **Match existing style exactly.** Antes de editar qualquer arquivo, leia código existente do mesmo domínio/função no projeto. Copie naming, error handling, logging, estrutura de testes, imports e padrões arquiteturais **sem desvio**.
- **Mudança cirúrgica e mínima.** Altere **apenas** o necessário para cumprir a tarefa. Zero refactors não solicitados, zero renomeação, zero reorganização de imports/pastas/arquitetura, zero "melhorias" estéticas ou de design.
- **Nunca invente estilo.** Se o projeto usa um padrão ruim, siga o padrão ruim. Só proponha mudança de padrão se a tarefa pedir explicitamente.
- **Leia antes de propor.** Em tarefas de código, leia pelo menos 2-3 arquivos relevantes antes de qualquer edição ou sugestão de plano.

## Guardrails específicos deste projeto
- **Escopo:** nunca altere arquivos fora do solicitado. Mudança que exige tocar adicionais → sinalize, aguarde aprovação.
- **Infra:** **nunca** modifique `Dockerfile`, `hermes-compose.local.yml`, `hermes-coolify.yml`, configs de rede/volumes ou qualquer coisa de infra sem aprovação explícita do Fábio.
- **betrader-hydra é READ-ONLY:** o repo `gitlab.com/fabiosiqueira/betrader-hydra-bot` não deve ser modificado. Qualquer integração com ele é via REST usando os endpoints documentados no spec.
- **`scripts/` do estrategista são código de produção testado**, não uma pasta livre para o agente criar ferramentas arbitrárias. Edições em `scripts/` requerem a mesma disciplina que qualquer código de produção: testes, match de estilo, aprovação implícita pelo scope da task.
- **`dogmas.yaml` e `risk_engine.py` são invioláveis pelo agente em runtime.** Nunca crie paths de bypass ou "override temporário" sem aprovação explícita.
- **Segredos:** `BETRADER_TOKEN` e qualquer credential nunca entram em arquivo versionado. Usar `.env` (gitignored) ou secret management. `.env.example` mostra as variáveis sem valores.
- **Redis:** todo código usa `REDIS_HOST`/`REDIS_PORT` do `os.environ`. O compose já seta `redis` corretamente. Nunca hardcode host.
- **Memória e ensinamentos:** o agente absorve ensinamentos via conversa + tools de memory. Não edite `memories/` ou o sqlite diretamente a menos que seja recuperação de desastre.
- **Deploy:** siga o processo estabelecido (compose local para dev; push + rebuild no Coolify ou skill `/deploy` para prod). Após deploy, verifique status/logs/restart antes de declarar pronto.

## Layout mínimo (o que editar)
- `hermes/SOUL.md` — persona do agente (PT-BR direto, ⚠️ para incerteza, push-back, limites duros). Carregado em runtime.
- `hermes/AGENTS.md` — contexto que o agente usa para saber onde estão suas coisas e como opera.
- `hermes/scripts/*.py` — ciclo do estrategista. Código de produção: testes obrigatórios, DI real, mocks só nas fronteiras de I/O (HTTP betrader, Redis).
- `hermes/dogmas.yaml` — só o operador edita.
- `hermes/config.yaml` — menor mudança possível.
- `hermes/memories/MEMORY.md` e `USER.md` — working set (editar só quando intencional e com caps em mente).
- Raiz: `Dockerfile` / compose / README — com aprovação.

## Convenções de edição
- PT-BR para textos de persona, docs do agente e explicações ao Fábio. Termos técnicos e código em inglês.
- Datas relativas → absolutas (ISO) ao persistir.
- Paths: use relativos quando possível (dentro do contexto do agente, cwd = /opt/data).
- Não crie helpers genéricos ou abstrações "para o futuro" a menos que a tarefa peça.
- Testes: rode o que for necessário. Nunca declare pronto sem verificação concreta.
- Após mudanças no customization: se afetar o agente em execução, tipicamente `docker compose restart gateway` (ou equivalente no orquestrador).

## Deploy notes
- Local: da raiz do binance-project: `docker compose -f hermes-compose.local.yml up --build -d`
- Logs úteis: `docker compose -f hermes-compose.local.yml logs -f gateway`
- Redis debug (do host): `redis-cli -p 6381` (ou dentro do container gateway: `redis-cli -h redis`)
- O agente usa `HERMES_DATA_DIR=/opt/data` — tools de journal/memory que dependem disso devem respeitar.
- Coolify / produção: o app aponta para este repo. Push → rebuild. O volume do data dir persiste `scripts/`, memories, state e config.

## Hermes Agent (engine)
- Consumido via imagem ghcr. Para referência de CLI, skills, tools, slash commands, delegation, curator, kanban etc.: use a skill `hermes-agent`.
- Documentação oficial: https://hermes-agent.nousresearch.com/docs/

## Resumo de paths (para o agente vs para edição humana)
- Agente (runtime, /opt/data): vê `scripts/`, `dogmas.yaml`, `SOUL.md`, `AGENTS.md`, redis via env, `workspace/`.
- Humano/IA editando o projeto: edita de `binance-project/` (compose, Dockerfile) ou `binance-project/hermes/` (persona, scripts do ciclo, dogmas).

Qualquer dúvida sobre intenção do Fábio, dogmas, gate ou betrader → pergunte antes de implementar. Edge cases de trading são parte do design, não cleanup posterior.
