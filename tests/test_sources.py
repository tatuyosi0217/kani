from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsdesk import sources

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
FEED = {"name": "Sample", "url": "https://example.com/rss", "category": "finance", "region": "米国", "lang": "en"}


def test_parse_rss_sanitizes_and_filters():
    articles = sources.parse_feed((FIX / "sample_rss.xml").read_bytes(), FEED, NOW, timedelta(hours=72))
    titles = [a["title"] for a in articles]
    assert titles == ["Central bank holds rates steady", "No date story"]
    first = articles[0]
    assert first["summary"] == "The bank kept rates at 5%."
    assert "script" not in first["summary"] and "onerror" not in first["summary"]
    assert first["url"].startswith("https://example.com/news/rates")
    assert first["published"] == "2026-10-09T10:00:00+00:00"
    assert first["category"] == "finance" and first["lang"] == "en"
    # 日付なしは取得時刻
    assert articles[1]["published"] == NOW.isoformat()


def test_parse_atom():
    feed = {**FEED, "category": "quantum"}
    articles = sources.parse_feed((FIX / "sample_atom.xml").read_bytes(), feed, NOW, timedelta(hours=72))
    assert len(articles) == 1
    assert articles[0]["title"] == "New qubit record"
    assert articles[0]["summary"] == "Researchers achieved 99.9% fidelity."


def test_parse_garbage_raises():
    import pytest

    with pytest.raises(sources.net.FetchError):
        sources.parse_feed(b"<html><body>Access denied</body></html>", FEED, NOW, timedelta(hours=72))


def test_parse_gdelt():
    data = {"articles": [
        {"url": "https://nikkei.example.jp/a", "title": "量子コンピューターの新記録", "seendate": "20261009T110000Z",
         "domain": "nikkei.example.jp", "language": "Japanese", "sourcecountry": "Japan"},
        {"url": "javascript:alert(1)", "title": "x", "seendate": "20261009T110000Z", "language": "English"},
        {"url": "https://lemonde.example.fr/b", "title": "Inflation en zone euro", "seendate": "bad",
         "domain": "lemonde.example.fr", "language": "French", "sourcecountry": "France"},
    ]}
    articles = sources.parse_gdelt(data, "economy", NOW)
    assert [a["lang"] for a in articles] == ["ja", "fr"]
    assert articles[0]["published"] == "2026-10-09T11:00:00+00:00"
    assert articles[1]["published"] == NOW.isoformat()
    assert all(a["via"] == "GDELT" for a in articles)


def test_fetch_feed_reports_http_errors(monkeypatch):
    def boom(*a, **k):
        raise sources.net.FetchError("HTTP 403", 403)

    monkeypatch.setattr(sources.net, "get", boom)
    items, status = sources.fetch_feed(None, FEED, {}, NOW, timedelta(hours=72))
    assert items == [] and status["ok"] is False and status["http_status"] == 403
