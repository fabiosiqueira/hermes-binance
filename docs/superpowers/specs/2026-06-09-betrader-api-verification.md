# Verificação de API betrader-hydra — 2026-06-09

> Fonte de código: `/Users/fabiosiqueira/dev/projetos/trading/betrader-hydra` (READ-ONLY)
> Base URL confirmada: `https://betrader.fabiosiqueira.dev`
> Todas as evidências de runtime foram coletadas via GET; nenhum POST/PUT/DELETE foi executado na instância viva.

---

## 1. Shape de /api/market (e fontes do Brief)

### Veredito

`GET /api/market` aceita query params `asset`, `timeframes` (CSV) e qualquer indicador como chave extra com params CSV. Retorna apenas `{ indicators: { [nome]: number } }` — **candles e portfolio foram comentados no código** e não aparecem na resposta GET. Os campos do Brief vêm de endpoints diferentes:

| Campo Brief | Endpoint / rota | Campo na resposta |
|---|---|---|
| `candles` | **Não exposto via GET público** — processado internamente pelo monitor CANDLES | — |
| `indicators` | `GET /api/market?asset=X&timeframes=T&RSI=14` | `indicators.RSI` (number) |
| `equity` | Derivado de `GET /api/exchange/balance?isFuture=true` | `fiatEstimate` (string `"~USDT N"`) |
| `balance` | `GET /api/exchange/balance?fiat=USDT&isFuture=false` | `assets.<COIN>.available` / `onOrder` / `fiatEstimate` |
| `positions` | `GET /api/futures?symbol=X` | array `FuturesPosition[]` da Binance (campos: `symbol`, `positionAmt`, `entryPrice`, `markPrice`, `unRealizedProfit`, `leverage`, etc.) |
| `used_leverage` | `GET /api/futures?symbol=X` | campo `leverage` em cada `FuturesPosition` |

### Evidências de código

`src/app/api/market/route.ts:8-44` — GET lê `asset`, `timeframes` e indicadores dinâmicos do cache Redis; retorna `{ indicators }`. Campos `candles` e `portfolio` estão comentados (linhas 41-43).

`src/app/api/exchange/balance/route.ts:5-21` — aceita `?fiat=USDT&isFuture=true|false`; delega a `exchangeController.getFullBalance`.

`src/services/controller/exchange.controller.ts:17-19` — shape retornado: `{ assets: { [coin]: { available, onOrder, fiatEstimate? } }, fiatEstimate: string }`.

`src/app/api/futures/route.ts:5-20` — GET aceita `?symbol=` e retorna `FuturesPosition[]` da lib `binance`.

### Evidência de runtime

```
GET /api/market?asset=BTCUSDT&timeframes=1m  →  200  {"indicators":{}}
GET /api/exchange/balance?fiat=USDT&isFuture=false  →  200
  {"assets":{"USDT":{"available":10000,"onOrder":0,"fiatEstimate":10000},...},"fiatEstimate":"~USDT 4008559.00"}
GET /api/futures?symbol=BTCUSDT  →  500 (Invalid API-key)  [testnet key sem permissão futures]
```

> Nota: `/api/futures` retornou 500 porque as futuresKey/futuresSecret da instância testnet não têm permissão IP no testnet da Binance. O shape é definido pelo tipo `FuturesPosition` da lib `binance` (positionAmt, entryPrice, markPrice, unRealizedProfit, leverage, positionSide, notional...).

---

## 2. Entrada+stop: 1 ou 2 calls REST?

### Veredito

**2 calls independentes são necessários.** Não existe combinação OCO nem order atômica no betrader para futures. O fluxo correto é:

1. `POST /api/orders` com `type=MARKET` (ou `LIMIT`) → entrada
2. `POST /api/orders` com `type=STOP_MARKET`, `reduceOnly=true`, `stopPrice=N` → SL

**Confirmação de fill:** `GET /api/orders?isFuture=true` retorna `{ rows: Order[], count }`. Campos relevantes de `Order`: `orderId`, `clientOrderId`, `status` (`NEW | FILLED | PARTIALLY_FILLED | CANCELED | REJECTED | EXPIRED`), `avgPrice`, `quantity`, `side`, `type`, `stopPrice`.

**Rollback / fechar posição:** `DELETE /api/futures/{symbol}` — chama `futuresController.closeFuturesPosition` que coloca order MARKET oposta ao `positionAmt`.

### Evidências de código

`src/app/api/orders/route.ts:58-76` — POST padrão monta `BinanceOrder`:
```ts
// src/app/api/orders/route.ts:73
if (STOP_TYPES.includes(postOrder.options.type))
    postOrder.options.stopPrice = `${order.stopPrice}`;

// src/app/api/orders/route.ts:73
if (order.reduceOnly) postOrder.options.reduceOnly = order.reduceOnly;
```

`STOP_TYPES` inclui `"STOP_MARKET"` (`src/lib/constants.ts:37`). Não há tipo combinado OCO — cada ordem é submetida individualmente via `orderController.placeOrder`.

`src/app/api/futures/[symbol]/route.ts:28-47` — DELETE delega a `futuresController.closeFuturesPosition(userId, symbol)` → MARKET na direção oposta.

### Evidência de runtime

```
GET /api/orders?isFuture=true&page=1&pageSize=3  →  200  {"rows":[],"count":0}
```
Shape confirmado: `{ rows: Order[], count: number }`.

---

## 3. Escopo do token bht_

### Veredito

**O token bht_ dá acesso completo de escrita** (POST/PUT/DELETE) nos endpoints que usam `currentUser()` — não existe granularidade de escopo. O token **pode ter expiração** (`expiresAt: DateTime?`) e pode ser **revogado** (`revokedAt`); se ambos forem null, o token é permanente. A instância atual não tem expiração configurada.

GET autenticado retornou 200; sem token retornou 401 (via redirecionamento para JSON `{"error":"Unauthorized"}`).

### Evidências de código

`src/lib/auth.ts:38-44` — `currentUser()` tenta primeiro `getBearerUser()` (linha 11-36): extrai `Authorization: Bearer <token>`, chama `validateServiceToken`. Se válido, retorna user idêntico ao session user — **mesmo role, sem restrição de escopo**.

`src/lib/service-token.ts:18-43` — `validateServiceToken`:
- Prefixo obrigatório `bht_` (linha 21)
- Busca por hash SHA-256 no DB (linha 23-24)
- Verifica `revokedAt` (linha 28) e `expiresAt` (linha 29-30)
- Sem expiração: token é vitalício
- Atualiza `lastUsedAt` a cada uso (linha 33-37)

`prisma/schema.prisma:58-71` — model `ServiceToken`: campos `expiresAt DateTime?`, `revokedAt DateTime?`.

`src/middleware.ts:22-43` — middleware verifica `Authorization: Bearer` header; se presente, passa a request para o handler sem redirecionar (linha 42: `if (hasBearerToken) return;`). O handler chama `currentUser()` e decide 401 se o token for inválido.

### Evidência de runtime

```
GET https://betrader.fabiosiqueira.dev/api/automations?mode=all  (sem token)  →  401  {"error":"Unauthorized"}
GET https://betrader.fabiosiqueira.dev/api/automations?mode=all  (com $BETRADER_TOKEN)  →  200  []
```

---

## 4. Action WEBHOOK do Beholder

### Veredito

**WEBHOOK está totalmente implementado e coberto por testes.** Apto para F1 e para homologação da work item #2.

- `doAction` roteia `actionTypes.WEBHOOK → sendWebhook` (`src/lib/beholder.ts:877`)
- `sendWebhook` coleta memória do Beholder, serializa payload JSON, assina com HMAC-SHA256 (header `X-Beholder-Signature: sha256=<hex>`) quando `action.webhookSecret` presente, e faz POST com retry (3 tentativas, backoff 300ms/600ms, timeout 5s)
- Sem secret → header `X-Beholder-Signature` **não é enviado**

### Payload enviado pelo Beholder

```json
{
  "automationId": "...",
  "automationName": "...",
  "symbol": "BTCUSDT",
  "actionType": "WEBHOOK",
  "indexes": [...],
  "memory": {...},
  "firedAt": "2026-06-09T..."
}
```

### Cobertura de testes

`src/lib/__tests__/beholder-webhook.test.ts` — 5 cenários:
1. Lança erro sem `webhookUrl` ✓
2. POST com payload assinado + retorno `success` ✓
3. Roteamento via `doAction` com `type=WEBHOOK` ✓
4. Sem secret → sem header de assinatura ✓
5. Retry 3x em falha de rede, depois lança ✓
6. Retry 3x em resposta não-2xx (503) ✓

**Conclusão para F1:** action WEBHOOK é funcional e testada. A instância viva pode receber webhooks do Beholder desde que uma automação ativa tenha uma action do tipo WEBHOOK configurada com `webhookUrl` apontando para o endpoint do Hermes.

---

## 5. isTestnet e modo DRY_RUN

### Veredito

- `isTestnet` é campo do model `User` (`default(true)`) e é lido diretamente do user object em `Exchange` e `ExchangeWsHelper`
- **Nenhum endpoint GET público expõe `isTestnet` diretamente**, mas `GET /api/users` (ADMIN) retorna o user completo incluindo `isTestnet`
- O usuário `BETRADER_USER` (hermes) está com **`isTestnet: true`** — confirmado por runtime
- Não existe flag `DRY_RUN` no betrader-hydra; o equivalente é o campo `automation.test = true` que aciona `execTest()` em vez da Binance real

### Onde isTestnet é lido

`src/lib/exchange.ts:37` — `this.isTestnet = user ? user.isTestnet : true;` — lido diretamente do user ao construir Exchange.

`src/lib/exchange.ts:40-41`:
```ts
const baseUrlKey = this.isTestnet ? "usdmtest" : "usdm";
const baseUrl = this.isTestnet ? BINANCE_FUTURES_API_URL_TESTNET : BINANCE_FUTURES_API_URL;
```

`prisma/schema.prisma:37` — `isTestnet Boolean @default(true)`.

`src/services/repository/user.repository.ts:250-252` — atualizado via `PUT /api/users/[id]` ou `PUT /api/users/me/settings`.

### Como o adapter pode confirmar isTestnet antes de escrever

```
GET https://betrader.fabiosiqueira.dev/api/users
  Authorization: Bearer $BETRADER_TOKEN
  → 200 { rows: [{ isTestnet: true, ... }] }
```

O campo `isTestnet` está presente em cada objeto de usuário na resposta.

### Evidência de runtime

```json
// GET /api/users → 200 (truncado)
{
  "rows": [
    { "name": "hermes", "isTestnet": true, ... }
  ]
}
```

---

## 6. Resumo executivo pro implementador

### Base URL e header de autenticação

| Item | Valor |
|---|---|
| Base URL | `https://betrader.fabiosiqueira.dev` |
| Header | `Authorization: Bearer $BETRADER_TOKEN` |
| Formato do token | `bht_<64 hex chars>` |

### Tabela de decisões → consequência no betrader_client.py

| Decisão verificada | Consequência no betrader_client.py |
|---|---|
| **2 calls para entrada+SL** — não existe OCO atômico | `place_entry()` → POST `/api/orders` MARKET; depois `place_stop()` → POST `/api/orders` STOP_MARKET + `reduceOnly=true`. Se `place_stop` falhar, acionar rollback imediato. |
| **Rollback = close imediato** via DELETE | `rollback(symbol)` → `DELETE /api/futures/{symbol}` → fecha posição MARKET na direção oposta |
| **Confirmação de fill** via GET orders | `confirm_fill(orderId)` → `GET /api/orders?isFuture=true` → filtrar por `orderId` e verificar `status == "FILLED"` |
| **Token sem escopo granular, com possível expiração** | Verificar status do token antes de cada sessão. Se 401, alertar operador — não há refresh automático. |
| **isTestnet sempre verificado antes de qualquer escrita** | No `__init__` do client: `GET /api/users` → assert `rows[0].isTestnet == expected_testnet`. Abortir se divergir. |
| **indicators via GET /api/market** — retorna `{}` se cache miss | Tratar `indicators[X] == 0` como "dado indisponível", não como sinal. |
| **balance via GET /api/exchange/balance?isFuture=true** | Campo `fiatEstimate` é string `"~USDT N"` — parsear com regex para float. `assets.USDT.available` é o campo numérico direto para equity cálculo. |
| **Webhook recebido do Beholder** — assinado com HMAC-SHA256 | Verificar header `X-Beholder-Signature: sha256=<hex>` com secret configurado na automação. Rejeitar payload sem assinatura se secret esperado. |
| **isTestnet=true na instância atual** | Todas as ordens vão para Binance Testnet. Confirmar `isTestnet` antes de qualquer trade. |
