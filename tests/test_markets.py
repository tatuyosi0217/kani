from datetime import datetime, timezone

import pytest

from newsdesk import markets

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def test_parse_yahoo_skips_nulls():
    data = {"chart": {"result": [{
        "meta": {"currency": "JPY"},
        "timestamp": [1791504000, 1791590400, 1791676800],
        "indicators": {"quote": [{"close": [100.0, None, 102.5]}]},
    }]}}
    points, currency = markets.parse_yahoo(data)
    assert currency == "JPY"
    assert [p[1] for p in points] == [100.0, 102.5]


def test_parse_yahoo_empty():
    with pytest.raises(ValueError):
        markets.parse_yahoo({"chart": {"result": None, "error": {"code": "Not Found"}}})


def test_parse_stooq():
    csv_text = "Date,Open,High,Low,Close,Volume\n2026-10-07,1,1,1,150.5,0\n2026-10-08,1,1,1,abc,0\n2026-10-09,1,1,1,151,0\n"
    assert markets.parse_stooq(csv_text) == [["2026-10-07", 150.5], ["2026-10-09", 151.0]]
    with pytest.raises(ValueError):
        markets.parse_stooq("No data")


def test_summarize_change():
    inst = {"id": "x", "name": "X", "group": "株価指数"}
    out = markets.summarize(inst, [["2026-10-08", 200.0], ["2026-10-09", 210.0]], "USD", "Yahoo Finance", NOW)
    assert out["last"] == 210.0 and out["change"] == 10.0 and out["change_pct"] == 5.0


def test_fetch_markets_keeps_previous_on_failure(monkeypatch):
    def boom(*a, **k):
        raise markets.net.FetchError("down")

    monkeypatch.setattr(markets, "fetch_instrument", boom)
    prev = {"x": {"id": "x", "name": "X", "points": [["2026-10-08", 1.0], ["2026-10-09", 2.0]]}}
    out, status = markets.fetch_markets([{"id": "x", "name": "X"}], NOW, prev)
    assert out[0]["stale"] is True and status[0]["ok"] is False
