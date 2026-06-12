# STATE — validação betrader (issue #5)

Atualizado: 2026-06-12 16:25 UTC (sessão `/hermes-validate #5` — host-side)

## Objetivo
Integração com betrader 100% testada → corrigida → entregue (issue #5).

## Resultado desta sessão (host-side, container prod)

### ✅ Blocker original RESOLVIDO — acesso ao betrader 100% funcional
Preflight ok: `gateway-dcvrz0*` e `risk-gateway-dcvrz0*` healthy; scripts deployados conferem (`_get_optional`, `mulham_analyzer.py` re-semeado).
- **`POST /brief` → 200** (antes 502). `brief.json`: `equity=7283.51` (futures testnet real → **-2015 resolvido**), `balance=10000`, `candles=200` OHLCV reais (~64k) → **cadeia de 4 bugs do candle corrigida no betrader**, `positions=[]` legítimo, `risk_state` presente.
- **Mulham determinístico:** `binance:strategist:mulham:BTCUSDT` (+`:signature`,`:material_change`) no binance-redis, sinais reais (rect 64394/64132, SL/TP).
- **Redis-first proposal + execute:** injetei proposal no-action → `execute redis:` → `{"executed":true,"orders":[],...}`. Gate rodou ok.
- **Ciclo REAL do agente** às 16:05:55 no stream `decisions` (risk-redis): reasoning Mulham completo (bias=bearish, rect 64394.44, SL 64458.83, TP 63320), `gate_ok=true`. End-to-end com LLM funcionou.
- `financial_state` persistido (risk-redis): initial_equity=1000, cum_pnl=0.

### ⚠️ "brief redis-first" — NÃO é bug de código; é overreach de doc (resolvido via investigação Opção C)
Teste decisivo (brief fresco): brief **AUSENTE** no binance-redis (redis do agente), **PRESENTE** no risk-redis TTL 898 (redis do gateway). Mas:
- **F2 (memória + `docker-compose.yaml:24`): brief cache no risk-redis é DESIGN — "inforjável pelo agente"** (anti-forja: se o brief fosse gravável no binance-redis, o agente poderia forjar equity/preços p/ passar o gate; `handle_execute` relê o brief do cache PRIVADO).
- **`71975a8` ("redis-first brief/proposal") NÃO tocou `risk_gateway.py`** — só docs + `mulham_analyzer.py` + `strategist_cycle.py`. Proposal redis-first e mulham redis-first foram wirados de verdade; **brief redis-first nunca foi** (gateway intocado segue gravando no risk-redis). Os docs foram atualizados afirmando algo que o código não faz.
- **Verdade:** brief é entregue ao agente via `workspace/brief.json` (contrato do thin-client: stdout imprime o path). O tick *"agent lê brief do Redis — não depende de filesystem"* + AGENTS.md/SOUL.md/cron + comentário `mulham_analyzer.py:364` estão ERRADOS e contradizem a F2.

## Próximo passo — RECOMENDAÇÃO: Opção B (alinhar doc à realidade segura)
Código está correto (F2). Corrigir só o que afirma falsamente "brief redis-first":
- AGENTS.md / SOUL.md / cron jobs.json: brief = **file-first** (`brief.json`); redis-first vale p/ mulham + proposal.
- comentário `mulham_analyzer.py:364` ("gateway already puts the brief under binance:strategist:brief").
- reformular o tick da Seção 1 da issue #5 (brief file-first é o canal seguro e intencional).
- (Opção A — espelhar brief no binance-redis via 2ª conexão — possível e não quebra o gate, mas adiciona infra p/ ganho ~nulo: o agente já tem brief.json. Descartada salvo pedido.)

Aguardando OK do operador p/ editar persona docs (AGENTS/SOUL/cron — operator-owned). Demais ticks: betrader access verde.

## Não-fechado
Issue #5 aberta. betrader#6 (serialização beholder) já fora do caminho crítico.
