# Unit tests do client REST do betrader-hydra (betrader_client).
#
# Mock APENAS na fronteira HTTP (respx); schemas e lógica interna são reais (DI real).
# Payloads de exemplo copiados do doc de verificação da API
# (docs/superpowers/specs/2026-06-09-betrader-api-verification.md).
#
# Caminho crítico coberto: place_entry_with_stop emite entrada → confirma stop →
# rollback (close imediato) se o stop não confirmar; rollback que falha vira
# estado inconsistente reportado, nunca engolido. Token jamais vaza em repr/str.
import httpx
import pytest
import respx

from schemas import AutomationSpec, EntryOrder, ExecutionMode, RiskState

from betrader_client import BetraderClient, BetraderError

BASE_URL = "https://betrader.example.test"
TOKEN = "bht_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd"


# --- Fixtures de payloads (shapes reais do doc de verificação) ---


def _users_payload(*, is_testnet: bool = True) -> dict:
    # GET /api/users → SearchResponse<User>; rows[0].isTestnet (seção 5).
    return {"rows": [{"name": "hermes", "isTestnet": is_testnet}], "count": 1}


def _indicators_payload() -> dict:
    # GET /api/indicators → catálogo {nome: {params, name}} (seção 6 / getAnalysisIndexes).
    return {
        "RSI": {"params": "period", "name": "RSI"},
        "MACD": {"params": "fast,slow,signal", "name": "MACD"},
    }


def _market_payload() -> dict:
    # GET /api/market → {indicators: {NOME: number}}; 0 = cache miss (seção 1).
    return {"indicators": {"RSI": 55.0, "MACD": 0}}


def _balance_future_payload() -> dict:
    # GET /api/exchange/balance?isFuture=true → fiatEstimate "~USDT N" (seção 1).
    return {
        "assets": {"USDT": {"available": 10000, "onOrder": 0, "fiatEstimate": 10000}},
        "fiatEstimate": "~USDT 10000.00",
    }


def _balance_spot_payload() -> dict:
    # GET /api/exchange/balance?isFuture=false → assets.USDT.available (seção 1).
    return {
        "assets": {"USDT": {"available": 8000, "onOrder": 0, "fiatEstimate": 8000}},
        "fiatEstimate": "~USDT 8000.00",
    }


def _futures_payload() -> list[dict]:
    # GET /api/futures?symbol= → FuturesPosition[] da Binance (seção 1, nota 43).
    return [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0.010",
            "entryPrice": "60000.0",
            "markPrice": "60100.0",
            "unRealizedProfit": "1.0",
            "leverage": "3",
            "positionSide": "BOTH",
        }
    ]


def _automations_payload() -> list[dict]:
    # GET /api/automations?mode=all → [] na instância testnet (seção 3, runtime).
    return []


def _orders_payload(*, status: str = "FILLED", order_id: int = 111) -> dict:
    # GET /api/orders?isFuture=true → {rows: Order[], count} (seção 2).
    return {
        "rows": [
            {
                "orderId": order_id,
                "clientOrderId": "c-1",
                "status": status,
                "avgPrice": "60000.0",
                "quantity": "0.010",
                "side": "BUY",
                "type": "MARKET",
                "stopPrice": "0",
            }
        ],
        "count": 1,
    }


def _indexes_payload() -> dict:
    # GET /api/automations/indexes?symbol= → indicadores correntes do Beholder.
    return {"BTCUSDT:RSI_14_1h": 55.0}


def _memory_payload() -> dict:
    # GET /api/beholder/memory → memória do Beholder.
    return {"BTCUSDT:RSI_14_1h": {"current": 55.0}}


def _risk_state() -> RiskState:
    return RiskState(
        daily_pnl=0.0, drawdown_pct=0.0, equity_curve_ref="hermes:equity_curve:BTCUSDT"
    )


def _entry_market(*, side: str = "BUY", stop_loss: float = 59000.0) -> EntryOrder:
    return EntryOrder(
        symbol="BTCUSDT",
        side=side,
        sizing_pct=5.0,
        order_type="MARKET",
        stop_loss=stop_loss,
        leverage=3,
    )


def _entry_limit() -> EntryOrder:
    return EntryOrder(
        symbol="BTCUSDT",
        side="BUY",
        sizing_pct=5.0,
        order_type="LIMIT",
        limit_price=60000.0,
        stop_loss=59000.0,
        leverage=3,
    )


def _client(on_error=None) -> BetraderClient:
    return BetraderClient(base_url=BASE_URL, token=TOKEN, on_error=on_error)


# --- __init__ / auth header ---


@respx.mock
def test_header_de_auth_exato_bearer() -> None:
    route = respx.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(200, json=_users_payload())
    )
    with _client() as client:
        client.assert_testnet()
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {TOKEN}"


# --- from_env ---


def test_from_env_le_base_url_e_token(monkeypatch) -> None:
    monkeypatch.setenv("BETRADER_BASE_URL", BASE_URL)
    monkeypatch.setenv("BETRADER_TOKEN", TOKEN)
    client = BetraderClient.from_env()
    assert client.base_url == BASE_URL
    client.close()


def test_from_env_falta_token_raise(monkeypatch) -> None:
    monkeypatch.setenv("BETRADER_BASE_URL", BASE_URL)
    monkeypatch.delenv("BETRADER_TOKEN", raising=False)
    with pytest.raises(BetraderError) as exc:
        BetraderClient.from_env()
    assert exc.value.type == "missing_config"


# --- Segredo: token JAMAIS aparece em repr/str ---


def test_token_nao_vaza_em_repr_str() -> None:
    client = _client()
    assert TOKEN not in repr(client)
    assert TOKEN not in str(client)
    client.close()


def test_token_nao_vaza_em_betrader_error_de_config(monkeypatch) -> None:
    monkeypatch.setenv("BETRADER_BASE_URL", BASE_URL)
    monkeypatch.delenv("BETRADER_TOKEN", raising=False)
    with pytest.raises(BetraderError) as exc:
        BetraderClient.from_env()
    assert TOKEN not in str(exc.value)


# --- assert_testnet (seção 5) ---


@respx.mock
def test_assert_testnet_ok_quando_is_testnet_true() -> None:
    respx.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(200, json=_users_payload(is_testnet=True))
    )
    with _client() as client:
        client.assert_testnet()  # não levanta


@respx.mock
def test_assert_testnet_falha_em_dry_run_com_is_testnet_false() -> None:
    respx.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(200, json=_users_payload(is_testnet=False))
    )
    with _client() as client:
        with pytest.raises(BetraderError) as exc:
            client.assert_testnet()
    assert exc.value.type == "not_testnet_in_dry_run"


# --- ensure_monitor: idempotente (já existe → nenhum POST) ---


@respx.mock
def test_ensure_monitor_idempotente_nao_posta_se_existe() -> None:
    monitors = {
        "rows": [
            {
                "id": "mon-1",
                "symbol": "BTCUSDT",
                "type": "CANDLES",
                "interval": "1h",
                "isActive": True,
            }
        ],
        "count": 1,
    }
    get_route = respx.get(f"{BASE_URL}/api/monitors").mock(
        return_value=httpx.Response(200, json=monitors)
    )
    post_route = respx.post(f"{BASE_URL}/api/monitors")
    start_route = respx.post(url__regex=rf"{BASE_URL}/api/monitors/.+/start")
    with _client() as client:
        client.ensure_monitor("BTCUSDT", "1h")
    assert get_route.called
    assert not post_route.called
    assert not start_route.called


@respx.mock
def test_ensure_monitor_cria_e_inicia_se_ausente() -> None:
    respx.get(f"{BASE_URL}/api/monitors").mock(
        return_value=httpx.Response(200, json={"rows": [], "count": 0})
    )
    post_route = respx.post(f"{BASE_URL}/api/monitors").mock(
        return_value=httpx.Response(200, json={"id": "mon-new", "isActive": False})
    )
    start_route = respx.post(f"{BASE_URL}/api/monitors/mon-new/start").mock(
        return_value=httpx.Response(200, json={"id": "mon-new", "isActive": True})
    )
    with _client() as client:
        client.ensure_monitor("BTCUSDT", "1h")
    assert post_route.called
    assert start_route.called
    body = post_route.calls.last.request
    assert b"CANDLES" in body.content
    assert b"BTCUSDT" in body.content


@respx.mock
def test_ensure_monitor_inicia_se_existe_inativo() -> None:
    monitors = {
        "rows": [
            {
                "id": "mon-2",
                "symbol": "BTCUSDT",
                "type": "CANDLES",
                "interval": "1h",
                "isActive": False,
            }
        ],
        "count": 1,
    }
    respx.get(f"{BASE_URL}/api/monitors").mock(
        return_value=httpx.Response(200, json=monitors)
    )
    post_route = respx.post(f"{BASE_URL}/api/monitors")
    start_route = respx.post(f"{BASE_URL}/api/monitors/mon-2/start").mock(
        return_value=httpx.Response(200, json={"id": "mon-2", "isActive": True})
    )
    with _client() as client:
        client.ensure_monitor("BTCUSDT", "1h")
    assert not post_route.called  # já existe; só dá start
    assert start_route.called


# --- fetch_brief monta Brief válido a partir de payloads reais ---


def _mock_brief_endpoints(router: respx.Router) -> None:
    router.get(f"{BASE_URL}/api/indicators").mock(
        return_value=httpx.Response(200, json=_indicators_payload())
    )
    router.get(f"{BASE_URL}/api/market").mock(
        return_value=httpx.Response(200, json=_market_payload())
    )
    router.get(
        url=f"{BASE_URL}/api/exchange/balance", params={"isFuture": "true"}
    ).mock(return_value=httpx.Response(200, json=_balance_future_payload()))
    router.get(
        url=f"{BASE_URL}/api/exchange/balance", params={"isFuture": "false"}
    ).mock(return_value=httpx.Response(200, json=_balance_spot_payload()))
    router.get(f"{BASE_URL}/api/futures").mock(
        return_value=httpx.Response(200, json=_futures_payload())
    )
    router.get(f"{BASE_URL}/api/beholder/memory").mock(
        return_value=httpx.Response(200, json=_memory_payload())
    )
    router.get(f"{BASE_URL}/api/automations/indexes").mock(
        return_value=httpx.Response(200, json=_indexes_payload())
    )
    router.get(f"{BASE_URL}/api/automations").mock(
        return_value=httpx.Response(200, json=_automations_payload())
    )
    router.get(f"{BASE_URL}/api/orders").mock(
        return_value=httpx.Response(200, json={"rows": [], "count": 0})
    )


@respx.mock(assert_all_called=True)
def test_fetch_brief_monta_brief_valido(respx_mock: respx.Router) -> None:
    _mock_brief_endpoints(respx_mock)
    with _client() as client:
        brief = client.fetch_brief(
            "BTCUSDT", "1h", mode=ExecutionMode.DRY_RUN, risk_state=_risk_state()
        )
    assert brief.mode == ExecutionMode.DRY_RUN
    assert brief.market.symbol == "BTCUSDT"
    assert brief.market.timeframe == "1h"
    # catálogo parseado.
    assert any(spec.name == "RSI" for spec in brief.catalog)
    # indicators: RSI presente; MACD=0 normalizado para None (cache miss).
    assert brief.market.indicators["RSI"] == 55.0
    assert brief.market.indicators["MACD"] is None
    # equity parseado de "~USDT 10000.00"; balance de assets.USDT.available (spot).
    assert brief.portfolio.equity == 10000.0
    assert brief.portfolio.balance == 8000.0
    # posição mapeada de FuturesPosition.
    assert len(brief.portfolio.positions) == 1
    pos = brief.portfolio.positions[0]
    assert pos.side == "BUY"
    assert pos.quantity == 0.010
    assert pos.leverage == 3
    assert brief.risk_state is _risk_state() or brief.risk_state.equity_curve_ref


@respx.mock
def test_fetch_brief_repassa_risk_state_do_caller(respx_mock: respx.Router) -> None:
    _mock_brief_endpoints(respx_mock)
    rs = _risk_state()
    with _client() as client:
        brief = client.fetch_brief(
            "BTCUSDT", "1h", mode=ExecutionMode.DRY_RUN, risk_state=rs
        )
    assert brief.risk_state == rs


# --- place_entry_with_stop: caminho crítico ---


def _mock_futures_config() -> None:
    # PUT /api/futures/{symbol} (leverage/marginType).
    respx.put(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json=[{"leverage": 3}])
    )


@respx.mock
def test_place_entry_with_stop_sucesso_sem_rollback() -> None:
    _mock_futures_config()
    # 2 POSTs /api/orders: entrada (MARKET) + stop (STOP_MARKET).
    orders_route = respx.post(f"{BASE_URL}/api/orders").mock(
        side_effect=[
            httpx.Response(200, json={"orderId": 111, "status": "FILLED", "avgPrice": "60000.0"}),
            httpx.Response(200, json={"orderId": 222, "status": "NEW", "stopPrice": "59000.0"}),
        ]
    )
    # confirmação do stop via GET /api/orders.
    respx.get(f"{BASE_URL}/api/orders").mock(
        return_value=httpx.Response(200, json=_orders_payload(status="NEW", order_id=222))
    )
    close_route = respx.delete(f"{BASE_URL}/api/futures/BTCUSDT")
    with _client() as client:
        result = client.place_entry_with_stop(_entry_limit(), equity=10000.0)
    assert orders_route.call_count == 2  # entrada + stop
    assert not close_route.called  # sem rollback
    assert result["entry_order_id"] == 111
    assert result["stop_order_id"] == 222
    assert result["status"] == "ok"


@respx.mock
def test_place_entry_with_stop_stop_rejeitado_faz_rollback() -> None:
    # CAMINHO CRÍTICO: stop falha (HTTP 500) → close imediato + raise.
    _mock_futures_config()
    orders_route = respx.post(f"{BASE_URL}/api/orders").mock(
        side_effect=[
            httpx.Response(200, json={"orderId": 111, "status": "FILLED", "avgPrice": "60000.0"}),
            httpx.Response(500, text="Internal Error"),
        ]
    )
    close_route = respx.delete(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json={"orderId": 333, "status": "FILLED"})
    )
    errors: list[str] = []
    with _client(on_error=errors.append) as client:
        with pytest.raises(BetraderError) as exc:
            client.place_entry_with_stop(_entry_limit(), equity=10000.0)
    assert exc.value.type == "entry_rolled_back_no_stop"
    assert close_route.called  # rollback executado
    assert orders_route.call_count == 2
    assert "betrader_http_5xx" in errors  # on_error recebeu o type da falha de I/O


@respx.mock
def test_place_entry_with_stop_stop_status_rejeitado_faz_rollback() -> None:
    # Stop retorna 200 mas com status REJECTED → tratar como não-confirmado.
    _mock_futures_config()
    respx.post(f"{BASE_URL}/api/orders").mock(
        side_effect=[
            httpx.Response(200, json={"orderId": 111, "status": "FILLED", "avgPrice": "60000.0"}),
            httpx.Response(200, json={"orderId": 222, "status": "REJECTED"}),
        ]
    )
    close_route = respx.delete(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json={"orderId": 333, "status": "FILLED"})
    )
    with _client() as client:
        with pytest.raises(BetraderError) as exc:
            client.place_entry_with_stop(_entry_limit(), equity=10000.0)
    assert exc.value.type == "entry_rolled_back_no_stop"
    assert close_route.called


@respx.mock
def test_place_entry_with_stop_rollback_tambem_falha_reporta_inconsistencia() -> None:
    # Stop falha E o rollback (close) também falha → estado inconsistente reportado.
    _mock_futures_config()
    respx.post(f"{BASE_URL}/api/orders").mock(
        side_effect=[
            httpx.Response(200, json={"orderId": 111, "status": "FILLED", "avgPrice": "60000.0"}),
            httpx.Response(500, text="stop error"),
        ]
    )
    respx.delete(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(500, text="close error")
    )
    errors: list[str] = []
    with _client(on_error=errors.append) as client:
        with pytest.raises(BetraderError) as exc:
            client.place_entry_with_stop(_entry_limit(), equity=10000.0)
    assert exc.value.type == "rollback_failed"
    assert "rollback_failed" in errors


@respx.mock
def test_place_entry_market_exige_ref_price() -> None:
    # MARKET sem ref_price não tem preço de referência para dimensionar quantity.
    _mock_futures_config()
    with _client() as client:
        with pytest.raises(BetraderError) as exc:
            client.place_entry_with_stop(_entry_market(), equity=10000.0)
    assert exc.value.type == "missing_ref_price"


@respx.mock
def test_place_entry_with_stop_configura_leverage_antes() -> None:
    put_route = respx.put(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json=[{"leverage": 3}])
    )
    respx.post(f"{BASE_URL}/api/orders").mock(
        side_effect=[
            httpx.Response(200, json={"orderId": 111, "status": "FILLED", "avgPrice": "60000.0"}),
            httpx.Response(200, json={"orderId": 222, "status": "NEW"}),
        ]
    )
    respx.get(f"{BASE_URL}/api/orders").mock(
        return_value=httpx.Response(200, json=_orders_payload(status="NEW", order_id=222))
    )
    with _client() as client:
        client.place_entry_with_stop(_entry_limit(), equity=10000.0)
    assert put_route.called
    assert b"leverage" in put_route.calls.last.request.content


@respx.mock
def test_place_entry_quantity_arredondada_por_precision() -> None:
    # sizing 5% de 10000 = 500 USD notional; ref 60000 → 0.00833... → round 3 = 0.008.
    _mock_futures_config()
    orders_route = respx.post(f"{BASE_URL}/api/orders").mock(
        side_effect=[
            httpx.Response(200, json={"orderId": 111, "status": "FILLED", "avgPrice": "60000.0"}),
            httpx.Response(200, json={"orderId": 222, "status": "NEW"}),
        ]
    )
    respx.get(f"{BASE_URL}/api/orders").mock(
        return_value=httpx.Response(200, json=_orders_payload(status="NEW", order_id=222))
    )
    with _client() as client:
        result = client.place_entry_with_stop(_entry_limit(), equity=10000.0)
    assert result["quantity"] == 0.008
    entry_body = orders_route.calls[0].request.content
    assert b"0.008" in entry_body


# --- on_error recebe type em CADA falha de I/O ---


@respx.mock
def test_on_error_recebe_type_em_falha_http_5xx() -> None:
    respx.get(f"{BASE_URL}/api/users").mock(return_value=httpx.Response(503, text="down"))
    errors: list[str] = []
    with _client(on_error=errors.append) as client:
        with pytest.raises(BetraderError) as exc:
            client.assert_testnet()
    assert exc.value.type == "betrader_http_5xx"
    assert errors == ["betrader_http_5xx"]


@respx.mock
def test_on_error_recebe_type_em_erro_de_rede() -> None:
    respx.get(f"{BASE_URL}/api/users").mock(
        side_effect=httpx.ConnectError("conn refused")
    )
    errors: list[str] = []
    with _client(on_error=errors.append) as client:
        with pytest.raises(BetraderError) as exc:
            client.assert_testnet()
    assert exc.value.type == "betrader_network_error"
    assert errors == ["betrader_network_error"]


@respx.mock
def test_http_401_vira_type_unauthorized() -> None:
    respx.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(401, json={"error": "Unauthorized"})
    )
    errors: list[str] = []
    with _client(on_error=errors.append) as client:
        with pytest.raises(BetraderError) as exc:
            client.assert_testnet()
    assert exc.value.type == "betrader_unauthorized"
    assert "betrader_unauthorized" in errors


# --- install_automations / teardown ---


@respx.mock
def test_install_automations_retorna_ids() -> None:
    respx.post(f"{BASE_URL}/api/automations").mock(
        side_effect=[
            httpx.Response(200, json={"id": "auto-1"}),
            httpx.Response(200, json={"id": "auto-2"}),
        ]
    )
    start_route = respx.post(url__regex=rf"{BASE_URL}/api/automations/.+/start").mock(
        return_value=httpx.Response(200, json={"id": "auto-x", "isActive": True})
    )
    specs = [
        AutomationSpec(
            name="exit-rsi",
            condition="MEMORY['BTCUSDT:RSI_14'] > 70",
            action={"type": "ORDER", "side": "SELL", "reduceOnly": True},
        ),
        AutomationSpec(
            name="exit-stop",
            condition="MEMORY['BTCUSDT:RSI_14'] < 30",
            action={"type": "ORDER", "side": "SELL", "reduceOnly": True},
        ),
    ]
    with _client() as client:
        ids = client.install_automations(specs)
    assert ids == ["auto-1", "auto-2"]
    assert start_route.call_count == 2


@respx.mock
def test_teardown_idempotente_404_nao_e_erro() -> None:
    respx.delete(f"{BASE_URL}/api/automations/auto-1").mock(
        return_value=httpx.Response(200, json={"id": "auto-1"})
    )
    # 404 = já removido → não é erro.
    respx.delete(f"{BASE_URL}/api/automations/auto-2").mock(
        return_value=httpx.Response(404, text="Not Found")
    )
    with _client() as client:
        client.teardown(["auto-1", "auto-2"])  # não levanta


@respx.mock
def test_teardown_500_levanta() -> None:
    respx.delete(f"{BASE_URL}/api/automations/auto-1").mock(
        return_value=httpx.Response(500, text="boom")
    )
    errors: list[str] = []
    with _client(on_error=errors.append) as client:
        with pytest.raises(BetraderError) as exc:
            client.teardown(["auto-1"])
    assert exc.value.type == "betrader_http_5xx"
