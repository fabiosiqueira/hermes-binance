# Unit tests do mulham_analyzer em modo redis-first (sem filesystem).
#
# Mocks SÓ na fronteira de I/O: Redis via fakeredis. main(--symbol) lê o brief do
# Redis (binance:strategist:brief:<symbol>), analisa e grava os sinais no Redis. Não
# há leitura/escrita de arquivos no handoff.
import json

import fakeredis
import pytest

from mulham_analyzer import main

BRIEF_KEY = "binance:strategist:brief:BTCUSDT"
MULHAM_KEY = "binance:strategist:mulham:BTCUSDT"


def _brief_dict() -> dict:
    candles = []
    base = 60000.0
    for i in range(40):
        price = base + (i % 7) * 50 - (i % 3) * 30
        candles.append(
            {
                "open_time": 1781100900000 + i * 900000,
                "open": price,
                "high": price + 80,
                "low": price - 90,
                "close": price + 10,
                "volume": 100.0 + i,
            }
        )
    return {
        "timestamp": "2026-06-12T16:00:00+00:00",
        "mode": "DRY_RUN",
        "catalog": [],
        "market": {"symbol": "BTCUSDT", "timeframe": "15m", "candles": candles, "indicators": {"RSI": 55.0}},
        "portfolio": {"equity": 10000.0, "balance": 8000.0, "positions": [], "used_leverage": 0.0},
        "risk_state": {"daily_pnl": 0.0, "drawdown_pct": 0.0, "equity_curve_ref": "binance:strategist:financial_state"},
        "active": [],
    }


@pytest.fixture
def redis_client():
    return fakeredis.FakeStrictRedis(decode_responses=True)


def test_le_brief_do_redis_e_grava_sinais(redis_client):
    """main(--symbol) lê o brief do Redis e persiste mulham + chaves auxiliares."""
    redis_client.set(BRIEF_KEY, json.dumps(_brief_dict()))

    rc = main(["--symbol", "BTCUSDT"], redis_client=redis_client)

    assert rc == 0
    raw = redis_client.get(MULHAM_KEY)
    assert raw is not None
    signals = json.loads(raw)
    assert signals["symbol"] == "BTCUSDT"
    assert signals["timeframe"] == "15m"
    assert "bias" in signals and "signature" in signals
    # Chaves auxiliares de detecção rápida de mudança.
    assert redis_client.get(f"{MULHAM_KEY}:signature") == signals["signature"]
    assert redis_client.get(f"{MULHAM_KEY}:material_change") in ("true", "false")


def test_brief_ausente_no_redis_retorna_erro(redis_client):
    """Sem brief no Redis (chave ausente/expirada) → rc != 0, nada gravado."""
    rc = main(["--symbol", "BTCUSDT"], redis_client=redis_client)

    assert rc != 0
    assert redis_client.get(MULHAM_KEY) is None


def test_nao_escreve_arquivo_no_handoff(redis_client, tmp_path, monkeypatch):
    """Garante que o handoff é 100% Redis: nenhum arquivo criado no cwd."""
    monkeypatch.chdir(tmp_path)
    redis_client.set(BRIEF_KEY, json.dumps(_brief_dict()))

    main(["--symbol", "BTCUSDT"], redis_client=redis_client)

    assert list(tmp_path.iterdir()) == []
