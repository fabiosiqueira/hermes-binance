# STATE — hermes/binance-project

Atualizado: 2026-06-13 (sessão /effort ultracode — gh access do HAWK + commit_domain)

## Resultado da sessão
Diagnóstico reverteu a premissa do operador ("HAWK não abre issue → falta token").
Realidade: gh access NUNCA esteve quebrado no substrato. Falha original = transitória.
Entregue: capacidade NOVA de commit escopado pro HAWK (commit_domain), commitada+pushada+
sincronizada no volume, MAS com 1 bug de corretude bloqueando o e2e.

## O que foi feito (commitado: 83dc24f em main, pushado)
- **gh access VALIDADO** (probe /hermes-validate): token durável no Coolify (env uuid
  xkmhlct14e90…, is_runtime, scope `repo`); `gh auth status` OK; GH_CONFIG_DIR=/opt/hermes/.gh
  (Dockerfile:48). HAWK abriu+fechou issue #8 com prompt mínimo, orgânico (sem --skills).
- **Probe 2**: ao pedir "registra isso", HAWK CONSERTOU a skill direto (editou
  hawk-strategist-cycle 1.1.0 + reference, migração redis-first) em vez de abrir issue.
  Resolveu a "ISSUE 1" do blob original. Skill estava órfã no volume → puxada/revisada/
  versionada em hermes/skills/hawk-strategist-cycle/ (passa os greps do spec ISSUE 1).
- **commit_domain.py** (Q1=C — operador escolheu "commit só do domínio: skills autorais +
  SOUL + memories + AGENTS.md + cron/jobs.json"): self-commit escopado. Fronteira = o script
  (allowlist + denylist backstop hardcoded: scripts/dogmas/config/Dockerfile/compose/.env*/
  traversal). Clona on-demand + push token-na-URL (token nunca logado) + git-error handling.
  39 testes TDD, security boundary 100%. AGENTS.md:48 ganhou carve-out + how-to.
- **Sync do volume FEITO** (Coolify não re-semeia): docker cp commit_domain.py + AGENTS.md
  pro /opt/data do container gateway-dcvrz0… ; md5 confere com o repo.

## BUG bloqueando o e2e (achado no dry-run read-only, ANTES de qualquer push)
- Security boundary OK em dados reais: `DENIED LEAK []` (nada de scripts/dogmas/config vaza).
- **MAS authored_skill_dirs super-inclui skills BUNDLED do engine**: o dry-run listou 21
  arquivos committáveis, incl. `skills/mlops/{evaluation/lm-evaluation-harness, inference/
  vllm, models/audiocraft, models/segment-anything}` — essas são do engine, NÃO autorais.
- Causa provável: `.bundled_manifest` nomeia skills nested por CAMINHO/categoria; meu código
  casa por `skill_md.parent.name` (leaf). Confirmar formato real do manifest p/ esses nested:
  `grep -E 'vllm|audiocraft|segment-anything|lm-evaluation' /opt/data/skills/.bundled_manifest`.
  Fix: casar pelo nome que o manifest usa (provável path `mlops/inference/vllm`), OU restringir
  authored a skills no TOPO de skills/ (depth 1) — hawk-strategist-cycle é depth 1; as bundled
  mlops são depth 3. Decidir e adicionar teste (TDD) replicando a árvore nested.
- Gotcha latente 2: `hermes/memories/` é GITIGNORED no repo (só cron/jobs.json rastreado).
  commit_domain lista memories/ na allowlist, mas o `git add` no clone as ignoraria → memórias
  NÃO commitam de fato. Decidir: tirar memories/ da allowlist OU `git add -f`. Por ora inócuo.

## Próximo passo (e2e — operador pediu "e2e fica no state.md")
1. Corrigir authored_skill_dirs (bug acima) + teste TDD nested. Re-sync commit_domain.py.
2. Re-rodar o dry-run read-only no container → confirmar que lista SÓ
   skills/hawk-strategist-cycle/* + SOUL.md + AGENTS.md + cron/jobs.json (sem mlops, sem leak).
3. e2e via /hermes-validate: `docker exec -w /opt/data <cid> /opt/hermes/.venv/bin/hermes -z
   "versiona seu domínio no GitHub" --yolo` → observar commit no main (autor "HAWK (Hermes
   Agent)"). Provável "no_changes" se domínio==main; p/ provar push, mudar 1 arquivo do domínio
   antes (ou pedir ao HAWK registrar algo + commitar).
4. Se passar → `/done --version` (release o validado). commit_domain.py + AGENTS.md já no
   volume; após o fix, re-cp a versão corrigida.

## Comandos úteis
- Container: `ssh contabo` → `docker ps --filter name=gateway-dcvrz0` (excluir risk-gateway)
- gh do HAWK funciona: `docker exec <cid> gh issue list --repo fabiosiqueira/hermes-binance`

## Aberto / fora de escopo
- #4 (F3 gradação HOM→PROD) segue aberta.
- betrader#6 (beholder serialization) débito externo GitLab, mitigado não resolvido.
