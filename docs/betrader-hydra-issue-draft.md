## Contexto

Durante a validação da integração do **hermes-binance** (estrategista HAWK → Risk Gateway → betrader REST), o Risk Gateway (que detém o `BETRADER_TOKEN` e faz as chamadas autenticadas) não consegue montar o Brief: o `POST /brief` retorna `502 brief failed`. Isolando endpoint-a-endpoint de dentro do container do Risk Gateway (com o token real), encontrei **dois problemas no lado betrader-hydra**.

Usuário alvo: `Fábio Siqueira` · `isTestnet: true` · `EXECUTION_MODE=DRY_RUN`.

## Evidência (chamadas diretas ao betrader, com Bearer token do usuário)

| Endpoint | Resultado |
|---|---|
| `GET /api/users` | ✅ 200 |
| `GET /api/indicators` | ✅ 200 |
| `GET /api/market?asset=BTCUSDT&timeframes=15m` | ⚠️ 200 mas `{"indicators":{}}` (cache vazio / monitor inativo) |
| `GET /api/automations?mode=all` | ✅ 200 `[]` |
| `GET /api/orders?isFuture=true` | ✅ 200 |
| `GET /api/monitors` | ✅ 200 |
| **`GET /api/exchange/balance?fiat=USDT&isFuture=true`** | ❌ **500** `Invalid API-key, IP, or permissions for action (-2015)` |
| **`GET /api/futures?symbol=BTCUSDT`** | ❌ **500** `Invalid API-key, IP, or permissions for action (-2015)` |
| **`GET /api/beholder/memory`** | ❌ **500** `Value is not JSON serializable` |

---

## Bug 1 — Binance `-2015` em balance/futures (credenciais Futures testnet)

`src/lib/exchange.ts:35-48`: para futures, o client usa `user.futuresKey` / `user.futuresSecret` contra `BINANCE_FUTURES_API_URL_TESTNET = https://testnet.binancefuture.com` (porque `isTestnet=true`). O `/api/users` mostra `futuresKey` setado (len=64), mas a Binance responde `-2015` (chave inválida / IP não-whitelisted / sem permissão / **chave testnet expirada**).

`-2015` em testnet com a chave presente aponta, em ordem de probabilidade, para:
1. **Credenciais Futures testnet expiradas/invalidadas.** O `testnet.binancefuture.com` reseta e invalida chaves periodicamente — causa nº 1 de um setup que parou de funcionar.
2. IP do servidor (Contabo) não está no whitelist da API key, caso a key tenha restrição de IP.
3. A key não tem permissão de Futures.

**Ação requerida (operador):** regenerar as API keys de **Binance Futures testnet** em https://testnet.binancefuture.com e atualizar `futuresKey`/`futuresSecret` do usuário no betrader (idem `accessKey`/`secretKey` spot se também expiraram). Confirmar IP do servidor no whitelist se a key for IP-restricted.

**Hardening sugerido (betrader, opcional):** validar as keys no startup / num healthcheck (`GET /fapi/v2/balance` testnet) e expor um status claro ("Binance testnet creds inválidas") em vez de só propagar `-2015` como 500 opaco no fluxo de Brief.

---

## Bug 2 — `/api/beholder/memory` retorna 500 "Value is not JSON serializable" (defeito de código)

`src/app/api/beholder/memory/route.ts:21` faz `return NextResponse.json(result)` com `result = await beholderController.getMemory(...)`. O `getMemory` retorna um valor que `NextResponse.json` (i.e. `JSON.stringify`) não consegue serializar — tipicamente `BigInt`, `Map`/`Set`, `Date` aninhado de forma inesperada, ou referência circular.

Isso é um defeito real de código (não depende de credenciais) e **também** quebra o Brief do hermes-binance: o `fetch_brief` chama `/api/beholder/memory` para alimentar o reasoning do LLM, e o 500 aborta o ciclo independentemente do Bug 1.

**Fix sugerido (betrader):** sanitizar a saída do `beholderController.getMemory` antes do `NextResponse.json` (converter BigInt→string/number, Map→objeto, remover circularidade) ou identificar o campo ofensor no controller/repository e normalizá-lo na fonte. Adicionar um teste de regressão que serializa o retorno de `getMemory` para um payload realista.

---

## Impacto

Enquanto qualquer um dos dois persistir, o estrategista HAWK não monta o Brief (sem portfolio/positions/equity) → não há proposta possível. Bug 1 exige ação do operador (keys); Bug 2 exige fix de código no betrader-hydra. Os demais endpoints (users, indicators, automations, orders, monitors) estão OK — a integração hermes↔betrader em si está correta; o bloqueio é 100% upstream (Binance creds + serialização).
