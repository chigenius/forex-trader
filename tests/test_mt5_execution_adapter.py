"""Tests for MT5ExecutionAdapter's guards and its position-tracking fix.

Closing a position on MT5 requires telling it *which* position via the
request's `position` field — a plain opposite-direction order isn't
guaranteed to net against the original on every account type. A real MT5
terminal is Windows-only and unavailable here, so these inject a fake
`MetaTrader5` module via sys.modules — enough to verify the request
actually carries that field, without a real broker connection.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

from forexml.execution.base import Order
from forexml.execution.mt5 import LiveTradingDisabledError, MT5ExecutionAdapter


class _FakeResult:
    def __init__(self, retcode, price, order):
        self.retcode = retcode
        self.price = price
        self.order = order


class _FakeTick:
    def __init__(self, bid, ask):
        self.bid = bid
        self.ask = ask


def _install_fake_mt5(monkeypatch, recorder):
    fake = types.SimpleNamespace()
    fake.ORDER_TYPE_BUY = 0
    fake.ORDER_TYPE_SELL = 1
    fake.TRADE_ACTION_DEAL = 1
    fake.ORDER_TIME_GTC = 0
    fake.ORDER_FILLING_IOC = 1
    fake.TRADE_RETCODE_DONE = 10009

    fake.initialize = lambda: True
    fake.last_error = lambda: (0, "no error")
    fake.symbol_info_tick = lambda symbol: _FakeTick(bid=1.1000, ask=1.1002)

    def order_send(request):
        recorder.append(request)
        return _FakeResult(retcode=fake.TRADE_RETCODE_DONE, price=request["price"], order=555)

    fake.order_send = order_send
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    return fake


def _order(**overrides):
    base = dict(
        order_id="o1",
        symbol="EURUSD",
        direction="long",
        size_units=100_000,
        timestamp_utc=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Order(**base)


def test_guard_blocks_without_enable_live_trading_flag(monkeypatch):
    monkeypatch.setenv("FOREXML_LIVE_TRADING", "1")
    adapter = MT5ExecutionAdapter(enable_live_trading=False)
    with pytest.raises(LiveTradingDisabledError):
        adapter.submit_market_order(_order())


def test_guard_blocks_without_env_var_even_with_flag(monkeypatch):
    monkeypatch.delenv("FOREXML_LIVE_TRADING", raising=False)
    adapter = MT5ExecutionAdapter(enable_live_trading=True)
    with pytest.raises(LiveTradingDisabledError):
        adapter.submit_market_order(_order())


def test_opening_order_carries_no_position_field_and_returns_the_ticket(monkeypatch):
    monkeypatch.setenv("FOREXML_LIVE_TRADING", "1")
    recorder = []
    _install_fake_mt5(monkeypatch, recorder)
    adapter = MT5ExecutionAdapter(enable_live_trading=True)

    fill = adapter.submit_market_order(_order())

    assert "position" not in recorder[-1]
    assert fill.broker_position_id == "555"


def test_closing_order_includes_the_position_field(monkeypatch):
    monkeypatch.setenv("FOREXML_LIVE_TRADING", "1")
    recorder = []
    _install_fake_mt5(monkeypatch, recorder)
    adapter = MT5ExecutionAdapter(enable_live_trading=True)

    adapter.submit_market_order(_order(direction="short", closes_position_id="555"))

    assert recorder[-1]["position"] == 555
