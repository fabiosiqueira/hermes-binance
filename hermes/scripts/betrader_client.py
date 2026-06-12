# Client REST do betrader-hydra (executor determinístico das propostas do Hermes).
#
# Toda a superfície de chamadas (rotas, header de auth, shapes de resposta, decisão
# 1-vs-2 calls para entrada+stop, rota de rollback, endpoint de isTestnet) vem do doc
# de verificação da API: docs/superpowers/specs/2026-06-09-betrader-api-verification.md.
# Nada aqui é suposição.
#
# Fronteira de I/O: httpx.Client. `on_error(type)` é o ponto de DI onde a observability
# pluga (este módulo NÃO importa observability). Segredos: o token JAMAIS é logado nem
# aparece em repr/str.
#
# INVARIANTE CENTRAL (seção 2 do doc): entrada e stop são 2 calls REST independentes
# (não existe OCO atômico). place_entry_with_stop executa emit → confirm → rollback:
# se o stop não confirmar, a entrada é revertida via close imediato (DELETE).
import os
from collections.abc import Callable

import httpx

from schemas import (
    ActiveItem,
    AutomationSpec,
    Brief,
    Candle,
    EntryOrder,
    ExecutionMode,
    IndicatorSpec,
    MarketState,
    Portfolio,
    Position,
    RiskState,
    parse_automation_condition,
)

# Status de ordem que contam como "stop confirmado" (seção 2 do doc): a STOP_MARKET
# fica NEW até o gatilho disparar; FILLED/PARTIALLY_FILLED também são confirmação.
_STOP_CONFIRMED_STATUS = {"NEW", "FILLED", "PARTIALLY_FILLED"}

# Colunas reais do modelo Action do betrader (prisma): qualquer outra chave no
# payload derruba o createMany com "Unknown argument".
_ACTION_COLUMNS = {
    "type",
    "orderTemplateId",
    "withdrawTemplateId",
    "webhookUrl",
    "webhookSecret",
}

# Precisão default de quantidade (casas decimais) para o sizing → quantity. O doc de
# verificação não expõe endpoint de precision no fluxo de ordem (seção 2), então é
# parâmetro do client; BTCUSDT perp (M1) usa 3 casas.
_DEFAULT_QUANTITY_PRECISION = 3

# Mercado via POST /api/market (BeTraderRequest): candles do CANDLE_LIST do monitor.
# O GET público não expõe candles e só devolve indicador já em cache; o POST lê os
# candles e computa indicadores pedidos. `_MARKET_INDICATORS` fica VAZIO de propósito:
# pedir indicador não-cacheado dispara o load on-demand do betrader (registra no
# monitor, `wait(5)` e recursa) que NÃO converge — a request pendura >90s e travaria
# o ciclo. O Mulham é candle-based (swings/ranges/CCT sobre OHLC), não depende desses
# indicadores; brief.market.indicators fica {} até o betrader corrigir o on-demand.
# `_MARKET_CANDLES`: janela para a análise Mulham (betrader devolve no máx. o disponível).
_MARKET_INDICATORS: dict[str, list[str]] = {}
_MARKET_CANDLES = 200


class BetraderError(Exception):
    """Erro do client betrader com `type` descritivo para correlação na observability.

    Ex.: "betrader_http_5xx", "stop_confirm_timeout", "entry_rolled_back_no_stop",
    "rollback_failed", "not_testnet_in_dry_run", "missing_config".
    """

    def __init__(self, type: str, message: str = "") -> None:
        self.type = type
        super().__init__(message or type)


class BetraderClient:
    """Client REST do betrader-hydra. Fronteira de I/O via httpx.Client.

    Header de auth EXATO (seção 6 do doc): `Authorization: Bearer <token>`.
    O token nunca é exposto em repr/str/logs.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        on_error: Callable[[str], None] | None = None,
        timeout: float = 10.0,
        quantity_precision: int = _DEFAULT_QUANTITY_PRECISION,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._on_error = on_error
        self._quantity_precision = quantity_precision
        # Header de auth exato documentado na seção 6 do doc de verificação.
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    @classmethod
    def from_env(
        cls, *, on_error: Callable[[str], None] | None = None, timeout: float = 10.0
    ) -> "BetraderClient":
        """Constrói o client de BETRADER_BASE_URL e BETRADER_TOKEN do ambiente.

        Levanta BetraderError type="missing_config" se ausentes. NUNCA loga o token.
        """
        base_url = os.environ.get("BETRADER_BASE_URL")
        token = os.environ.get("BETRADER_TOKEN")
        if not base_url or not token:
            faltando = [
                nome
                for nome, valor in (("BETRADER_BASE_URL", base_url), ("BETRADER_TOKEN", token))
                if not valor
            ]
            raise BetraderError(
                "missing_config", f"variáveis de ambiente ausentes: {', '.join(faltando)}"
            )
        return cls(base_url, token, on_error=on_error, timeout=timeout)

    def __repr__(self) -> str:
        # NUNCA inclui o token.
        return f"BetraderClient(base_url={self.base_url!r})"

    __str__ = __repr__

    # --- Fronteira HTTP: classifica falhas em `type` e notifica on_error ---

    def _classify_status(self, status_code: int) -> str:
        if status_code == 401:
            return "betrader_unauthorized"
        if status_code == 403:
            return "betrader_forbidden"
        if 500 <= status_code < 600:
            return "betrader_http_5xx"
        return "betrader_http_4xx"

    def _notify(self, type: str) -> None:
        # on_error é chamado em todo catch de I/O ANTES do re-raise (DI: observability).
        if self._on_error is not None:
            self._on_error(type)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Executa request HTTP; em falha notifica on_error(type) e levanta BetraderError.

        Não engole: toda falha de I/O vira BetraderError com type descritivo.
        """
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            self._notify("betrader_network_error")
            raise BetraderError("betrader_network_error", str(exc)) from exc
        if resp.status_code >= 400:
            type = self._classify_status(resp.status_code)
            self._notify(type)
            raise BetraderError(type, f"{method} {path} → HTTP {resp.status_code}")
        return resp

    def _get(self, path: str, **kwargs) -> httpx.Response:
        return self._request("GET", path, **kwargs)

    def _get_optional(self, path: str, **kwargs) -> httpx.Response | None:
        """GET tolerante: falha do betrader retorna None em vez de levantar.

        Para chamadas cujo resultado alimenta o reasoning do LLM mas NÃO compõe o
        Brief tipado — o Brief não deve quebrar por dado que ele descarta. on_error
        já é notificado por _request antes da exceção (a falha não é engolida).
        """
        try:
            return self._get(path, **kwargs)
        except BetraderError:
            return None

    def _post(self, path: str, **kwargs) -> httpx.Response:
        return self._request("POST", path, **kwargs)

    def _put(self, path: str, **kwargs) -> httpx.Response:
        return self._request("PUT", path, **kwargs)

    # --- isTestnet (seção 5 do doc) ---

    def assert_testnet(self) -> None:
        """Confirma user.isTestnet=true ANTES de qualquer escrita (DRY_RUN).

        Endpoint conforme seção 5 do doc: GET /api/users → rows[0].isTestnet.
        Falha → BetraderError type="not_testnet_in_dry_run".
        """
        data = self._get("/api/users").json()
        rows = data.get("rows", [])
        is_testnet = bool(rows[0].get("isTestnet")) if rows else False
        if not is_testnet:
            raise BetraderError(
                "not_testnet_in_dry_run",
                "isTestnet=false em DRY_RUN; escrita abortada",
            )

    # --- Monitors (idempotente) ---

    def ensure_monitor(self, symbol: str, timeframe: str) -> None:
        """Garante monitor CANDLES ativo do par/timeframe. Idempotente.

        GET /api/monitors; se já existe e está ativo → no-op. Se existe inativo →
        start. Se ausente → POST + start (rotas conforme seção "Superfície da API").
        """
        monitors = self._get("/api/monitors").json().get("rows", [])
        existente = next(
            (
                m
                for m in monitors
                if m.get("type") == "CANDLES"
                and m.get("symbol") == symbol
                and m.get("interval") == timeframe
            ),
            None,
        )
        if existente is not None:
            if existente.get("isActive"):
                return
            self._post(f"/api/monitors/{existente['id']}/start")
            return
        criado = self._post(
            "/api/monitors",
            json={
                "symbol": symbol,
                "type": "CANDLES",
                "interval": timeframe,
                "indexes": [],
                "isActive": False,
            },
        ).json()
        self._post(f"/api/monitors/{criado['id']}/start")

    # --- Brief ---

    def _parse_equity(self, fiat_estimate: str) -> float:
        """Parseia fiatEstimate "~USDT N" → float (seção 1 do doc)."""
        digits = "".join(c for c in str(fiat_estimate) if c.isdigit() or c in ".-")
        try:
            return float(digits) if digits else 0.0
        except ValueError:
            return 0.0

    def fetch_brief(
        self,
        symbol: str,
        timeframe: str,
        mode: ExecutionMode,
        risk_state: RiskState,
    ) -> Brief:
        """Monta o Brief a partir dos GETs do betrader (seção 1 e 6 do doc).

        Fontes: /api/indicators (catálogo), POST /api/market (candles + indicadores),
        /api/exchange/balance (equity futuro + balance spot), /api/futures (posições),
        /api/beholder/memory + /api/automations/indexes (índices correntes),
        /api/automations + /api/orders (ativos). risk_state vem do caller (a
        observability é quem conhece a equity-curve).
        """
        # Catálogo de indicadores: {nome: {params, name}} → IndicatorSpec[].
        catalog_raw = self._get("/api/indicators").json()
        catalog = [
            IndicatorSpec(
                name=meta.get("name", nome),
                params={"params": meta.get("params")} if meta.get("params") else {},
            )
            for nome, meta in catalog_raw.items()
        ]

        # Candles + indicadores correntes via POST /api/market (BeTraderRequest).
        # Resposta: timeframes[].{interval, indicators, candles}. Candle do betrader
        # usa `timestamp` (epoch ms) → mapeado para `open_time`. 0 = cache miss → None.
        market_raw = self._post(
            "/api/market",
            json={
                "asset": symbol,
                "timeframes": [
                    {
                        "interval": timeframe,
                        "indicatorsParams": _MARKET_INDICATORS,
                        "howManyCandles": _MARKET_CANDLES,
                    }
                ],
            },
        ).json()
        tf_data = next(
            (
                tf
                for tf in market_raw.get("timeframes", [])
                if tf.get("interval") == timeframe
            ),
            {},
        )
        candles = [
            Candle(
                open_time=int(c["timestamp"]),
                open=float(c["open"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
                volume=float(c["volume"]),
            )
            for c in tf_data.get("candles", [])
        ]
        indicators: dict[str, float | None] = {
            nome: (None if valor == 0 else float(valor))
            for nome, valor in tf_data.get("indicators", {}).items()
        }
        market = MarketState(
            symbol=symbol, timeframe=timeframe, candles=candles, indicators=indicators
        )

        # Equity: balance futuro (fiatEstimate "~USDT N"). Balance: spot USDT.available.
        balance_fut = self._get(
            "/api/exchange/balance", params={"fiat": "USDT", "isFuture": "true"}
        ).json()
        balance_spot = self._get(
            "/api/exchange/balance", params={"fiat": "USDT", "isFuture": "false"}
        ).json()
        equity = self._parse_equity(balance_fut.get("fiatEstimate", "0"))
        balance = float(
            balance_spot.get("assets", {}).get("USDT", {}).get("available", 0) or 0
        )

        # Posições: FuturesPosition[] → Position[] (side pelo sinal de positionAmt).
        positions_raw = self._get("/api/futures", params={"symbol": symbol}).json()
        positions: list[Position] = []
        used_leverage = 0.0
        for p in positions_raw:
            amt = float(p.get("positionAmt", 0) or 0)
            if amt == 0:
                continue
            lev = int(float(p.get("leverage", 1) or 1))
            positions.append(
                Position(
                    symbol=p.get("symbol", symbol),
                    side="BUY" if amt > 0 else "SELL",
                    entry_price=float(p.get("entryPrice", 0) or 0),
                    quantity=abs(amt),
                    unrealized_pnl=float(p.get("unRealizedProfit", 0) or 0),
                    leverage=lev,
                )
            )
            used_leverage += lev
        portfolio = Portfolio(
            equity=equity,
            balance=balance,
            positions=positions,
            used_leverage=used_leverage,
        )

        # Índices correntes do Beholder (alimentam o reasoning do LLM; não tipados aqui).
        # Não-essenciais ao Brief: tolerados se o betrader falhar (observability já
        # notificada). O Brief não deve quebrar por dado que ele descarta — ex.
        # /api/beholder/memory 500 "Value is not JSON serializable" (betrader-hydra#6).
        self._get_optional("/api/beholder/memory")
        self._get_optional("/api/automations/indexes", params={"symbol": symbol})

        # Ativos: automations + ordens vigentes.
        automations = self._get("/api/automations", params={"mode": "all"}).json()
        orders = self._get("/api/orders", params={"isFuture": "true"}).json().get("rows", [])
        active: list[ActiveItem] = [
            ActiveItem(
                id=str(a.get("id")),
                kind="automation",
                summary=a.get("name", str(a.get("id"))),
            )
            for a in automations
        ] + [
            ActiveItem(
                id=str(o.get("orderId")),
                kind="order",
                summary=f"{o.get('side')} {o.get('type')} {o.get('status')}",
            )
            for o in orders
        ]

        from datetime import datetime, timezone

        return Brief(
            timestamp=datetime.now(timezone.utc).isoformat(),
            mode=mode,
            catalog=catalog,
            market=market,
            portfolio=portfolio,
            risk_state=risk_state,
            active=active,
        )

    # --- Entrada + stop (INVARIANTE CENTRAL: 2 calls + rollback) ---

    def _reference_price(self, entry: EntryOrder, ref_price: float | None) -> float:
        """Preço de referência para dimensionar quantity.

        LIMIT → limit_price (seção 2 do doc). MARKET → ref_price do caller (último
        close do brief); ausente → BetraderError type="missing_ref_price".
        """
        if entry.order_type == "LIMIT" and entry.limit_price is not None:
            return entry.limit_price
        if ref_price is not None and ref_price > 0:
            return ref_price
        raise BetraderError(
            "missing_ref_price",
            "MARKET exige ref_price (preço corrente do brief) para dimensionar quantity",
        )

    def place_entry_with_stop(
        self, entry: EntryOrder, equity: float, *, ref_price: float | None = None
    ) -> dict:
        """Emite entrada + stop como unidade; rollback se o stop não confirmar.

        Decisão 1-vs-2 calls (seção 2 do doc): 2 calls REST independentes — não existe
        OCO atômico na Binance Futures. Fluxo:
          (a) sizing_pct → quantity = (equity * sizing_pct/100) / preço_ref, arredondado
              a `quantity_precision` casas;
          (b) configura leverage/marginType (PUT /api/futures/{symbol}) antes da entrada;
          (c) POST /api/orders MARKET|LIMIT (entrada);
          (d) POST /api/orders STOP_MARKET reduceOnly=true (stop) + confirma via GET;
          (e) stop NÃO confirma → DELETE /api/futures/{symbol} (close imediato) e raise
              BetraderError type="entry_rolled_back_no_stop". Se o rollback TAMBÉM falhar,
              raise type="rollback_failed" (estado inconsistente reportado, nunca engolido).

        Retorna dict com ids + status.
        """
        preco_ref = self._reference_price(entry, ref_price)
        notional = equity * entry.sizing_pct / 100.0
        quantity = round(notional / preco_ref, self._quantity_precision)

        # (b) leverage/marginType antes da entrada (PUT /api/futures/{symbol}).
        self._put(f"/api/futures/{entry.symbol}", json={"leverage": entry.leverage})

        # (c) entrada.
        entry_body: dict = {
            "symbol": entry.symbol,
            "quantity": f"{quantity}",
            "side": entry.side,
            "type": entry.order_type,
        }
        if entry.order_type == "LIMIT":
            entry_body["limitPrice"] = f"{entry.limit_price}"
        entry_resp = self._post("/api/orders", params={"isFuture": "true"}, json=entry_body).json()
        entry_order_id = entry_resp.get("orderId")

        # (d) stop: STOP_MARKET reduceOnly, lado oposto à entrada (seção 2 do doc).
        stop_side = "SELL" if entry.side == "BUY" else "BUY"
        stop_body = {
            "symbol": entry.symbol,
            "quantity": f"{quantity}",
            "side": stop_side,
            "type": "STOP_MARKET",
            "stopPrice": f"{entry.stop_loss}",
            "reduceOnly": True,
        }
        stop_order_id = None
        stop_confirmed = False
        try:
            stop_resp = self._post(
                "/api/orders", params={"isFuture": "true"}, json=stop_body
            ).json()
            stop_order_id = stop_resp.get("orderId")
            status = stop_resp.get("status")
            stop_confirmed = status in _STOP_CONFIRMED_STATUS
        except BetraderError:
            stop_confirmed = False

        if stop_confirmed:
            return {
                "status": "ok",
                "entry_order_id": entry_order_id,
                "stop_order_id": stop_order_id,
                "quantity": quantity,
            }

        # (e) stop não confirmou → rollback (close imediato). Falha do rollback é
        # estado inconsistente reportado, nunca engolido.
        try:
            self._request("DELETE", f"/api/futures/{entry.symbol}")
        except BetraderError as exc:
            self._notify("rollback_failed")
            raise BetraderError(
                "rollback_failed",
                f"stop não confirmou e o rollback falhou (posição {entry.symbol} "
                f"possivelmente sem stop): {exc}",
            ) from exc

        raise BetraderError(
            "entry_rolled_back_no_stop",
            f"stop não confirmou; entrada {entry.symbol} revertida via close imediato",
        )

    # --- Automations ---

    def _webhook_action(self, action: dict) -> dict:
        """Injeta webhookUrl/webhookSecret (do ambiente) em action do tipo WEBHOOK.

        Lê WEBHOOK_PUBLIC_URL e BETRADER_WEBHOOK_SECRET de os.environ; ausente/vazia →
        BetraderError type="missing_webhook_config" ANTES do POST (não instala sentinela
        quebrada). NÃO muta a action recebida — retorna dict novo. O secret JAMAIS é
        logado/exposto em repr/str.
        """
        url = os.environ.get("WEBHOOK_PUBLIC_URL")
        secret = os.environ.get("BETRADER_WEBHOOK_SECRET")
        if not url or not secret:
            faltando = [
                nome
                for nome, valor in (
                    ("WEBHOOK_PUBLIC_URL", url),
                    ("BETRADER_WEBHOOK_SECRET", secret),
                )
                if not valor
            ]
            raise BetraderError(
                "missing_webhook_config",
                f"variáveis de ambiente ausentes: {', '.join(faltando)}",
            )
        return {**action, "webhookUrl": url, "webhookSecret": secret}

    def install_automations(self, automations: list[AutomationSpec]) -> list[str]:
        """Instala automations de gestão/saída no Beholder; retorna os ids criados.

        POST /api/automations (+ start). A condition é decomposta no shape real do
        betrader: conditions=[{eval, operator, variable}] (campos obrigatórios do
        AutomationCondition) + symbol e indexes na Automation — sem indexes o brain
        do Beholder nunca se inscreve nos updates de memória e a automation não
        dispara. Actions WEBHOOK recebem webhookUrl/webhookSecret do ambiente
        (sem mutar o spec).
        """
        ids: list[str] = []
        for spec in automations:
            action = (
                self._webhook_action(spec.action)
                if spec.action.get("type") == "WEBHOOK"
                else spec.action
            )
            # O modelo Action do betrader só tem estas colunas — chave extra
            # (method/side/reduceOnly) quebra o insert Prisma do lado de lá.
            action = {
                campo: valor
                for campo, valor in action.items()
                if campo in _ACTION_COLUMNS
            }
            parsed = parse_automation_condition(spec.condition)
            new_automation: dict = {
                "name": spec.name,
                "symbol": parsed["symbol"],
                "indexes": [parsed["index_key"]],
                "conditions": [
                    {
                        "eval": parsed["eval"],
                        "operator": parsed["operator"],
                        "variable": parsed["variable"],
                    }
                ],
                "actions": [action],
            }
            if spec.schedule:
                new_automation["schedule"] = spec.schedule
            created = self._post(
                "/api/automations", json={"newAutomation": new_automation}
            ).json()
            auto_id = str(created.get("id"))
            self._post(f"/api/automations/{auto_id}/start")
            ids.append(auto_id)
        return ids

    def teardown(self, ids: list[str]) -> None:
        """Cancela automations/ordens por id. Idempotente: 404 = já removido, não é erro.

        DELETE /api/automations/{id}.
        """
        for item_id in ids:
            try:
                self._request("DELETE", f"/api/automations/{item_id}")
            except BetraderError as exc:
                # 404 já é classificado e levantado como betrader_http_4xx; aqui só o
                # 404 (já removido) é tolerado. Demais erros propagam.
                if exc.type == "betrader_http_4xx":
                    continue
                raise

    # --- Lifecycle ---

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BetraderClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
