import json
from datetime import datetime, timedelta, timezone

from newsdesk import build, markets, sources

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def art(url, title, cat="finance", hours=1, **extra):
    return {"id": sources.article_id(url), "url": url, "title": title, "summary": "", "source": "S",
            "category": cat, "region": "", "lang": "en",
            "published": (NOW - timedelta(hours=hours)).isoformat(), **extra}


def test_merge_dedups_and_prefers_existing():
    existing = [art("https://a.com/1", "Fed holds rates", title_ja="FRBが金利据え置き")]
    fresh = [
        art("https://www.a.com/1/?utm_source=x", "Different title"),  # 同じ URL
        art("https://b.com/2", "FED HOLDS RATES!"),                    # 同じ見出し
        art("https://c.com/3", "Old", hours=100),                     # 古い
        art("https://d.com/4", "Unknown category", cat="sports"),
        art("https://e.com/5", "Qubits", cat="quantum"),
    ]
    merged = build.merge_articles(existing, fresh, NOW, timedelta(hours=72), 100)
    assert [a["title"] for a in merged] == ["Fed holds rates", "Qubits"]
    assert merged[0]["title_ja"] == "FRBが金利据え置き"


def test_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    monkeypatch.delenv("TRANSLATOR", raising=False)
    monkeypatch.setattr(sources, "fetch_all_feeds", lambda feeds, cache, now, max_age: (
        [art("https://a.com/1", "Hello")], [{"name": "A", "ok": True, "items": 1}]))
    monkeypatch.setattr(sources, "fetch_gdelt", lambda q, now: ([], []))
    monkeypatch.setattr(markets, "fetch_markets", lambda inst, now, prev: (
        [{"id": "x", "points": [["2026-10-08", 1], ["2026-10-09", 2]]}], []))
    out, state = tmp_path / "out", tmp_path / "state"
    assert build.main(["--out-dir", str(out), "--state-dir", str(state)]) == 0
    news = json.loads((out / "news.json").read_text())
    assert news["count"] == 1 and news["articles"][0]["title"] == "Hello"
    assert json.loads((out / "status.json").read_text())["translation"]["pending"] == 1
    assert (out / "markets.json").exists() and (state / "articles.json").exists()
