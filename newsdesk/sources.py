"""RSS / Atom フィードと GDELT からの記事取得。"""

from __future__ import annotations

import calendar
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import feedparser

from . import net
from .sanitize import clean_text, html_to_text, safe_url

log = logging.getLogger(__name__)

MAX_TITLE = 300
MAX_SUMMARY = 500
DEFAULT_MAX_ITEMS = 40

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_INTERVAL = 6  # GDELT は 5 秒に 1 リクエストまで

# GDELT の言語名 → ISO 639-1
GDELT_LANGS = {
    "english": "en", "japanese": "ja", "chinese": "zh", "korean": "ko", "french": "fr",
    "german": "de", "spanish": "es", "portuguese": "pt", "russian": "ru", "arabic": "ar",
    "italian": "it", "turkish": "tr", "hindi": "hi", "indonesian": "id", "vietnamese": "vi",
    "thai": "th", "dutch": "nl", "polish": "pl", "ukrainian": "uk", "persian": "fa",
    "hebrew": "he",
}


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def make_article(*, url, title, summary, source, category, region, lang, published) -> dict | None:
    url = safe_url(url)
    title = clean_text(html_to_text(title), MAX_TITLE)
    if not url or not title:
        return None
    return {
        "id": article_id(url),
        "url": url,
        "title": title,
        "summary": clean_text(html_to_text(summary), MAX_SUMMARY),
        "source": clean_text(source, 80),
        "category": category,
        "region": clean_text(region, 40),
        "lang": (lang or "").lower()[:5],
        "published": published.astimezone(timezone.utc).isoformat(timespec="seconds"),
    }


def _entry_time(entry, now: datetime) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            ts = datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc)
            # 未来日付のフィード(タイムゾーン誤り)は現在時刻に丸める
            return min(ts, now)
    return now


def parse_feed(body: bytes, feed: dict, now: datetime, max_age: timedelta) -> list[dict]:
    parsed = feedparser.parse(body)
    if not parsed.entries and not parsed.get("version"):
        # ボット対策のチャレンジページ等、フィード以外の HTML が返ってきた場合
        raise net.FetchError("フィードではない応答(ブロックされた可能性)")
    if parsed.bozo and not parsed.entries:
        raise net.FetchError(f"フィードを解析できません: {type(parsed.bozo_exception).__name__}")
    articles = []
    for entry in parsed.entries[: feed.get("max_items", DEFAULT_MAX_ITEMS)]:
        published = _entry_time(entry, now)
        if now - published > max_age:
            continue
        article = make_article(
            url=entry.get("link"),
            title=entry.get("title"),
            summary=entry.get("summary") or entry.get("description"),
            source=feed["name"],
            category=feed["category"],
            region=feed.get("region", ""),
            lang=feed.get("lang", "en"),
            published=published,
        )
        if article:
            articles.append(article)
    return articles


def fetch_feed(session, feed: dict, cache: dict, now: datetime, max_age: timedelta):
    """1 フィードを取得。(記事リスト, ステータス) を返す。cache は ETag 等の保存先。"""
    status = {"name": feed["name"], "category": feed["category"], "type": "rss", "url": feed["url"]}
    headers = {"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
    validators = cache.get(feed["url"], {})
    if validators.get("etag"):
        headers["If-None-Match"] = validators["etag"]
    if validators.get("modified"):
        headers["If-Modified-Since"] = validators["modified"]
    try:
        resp = net.get(session, feed["url"], headers=headers)
        if resp.status_code == 304:
            return [], {**status, "ok": True, "items": 0, "note": "更新なし"}
        articles = parse_feed(resp.content, feed, now, max_age)
    except net.FetchError as exc:
        return [], {**status, "ok": False, "items": 0, "error": str(exc), "http_status": exc.status}
    except Exception as exc:  # 1 フィードの失敗で全体を止めない
        log.exception("feed %s failed", feed["name"])
        return [], {**status, "ok": False, "items": 0, "error": type(exc).__name__}
    cache[feed["url"]] = {
        "etag": resp.headers.get("ETag"),
        "modified": resp.headers.get("Last-Modified"),
    }
    return articles, {**status, "ok": True, "items": len(articles)}


def fetch_all_feeds(feeds: list[dict], cache: dict, now: datetime, max_age: timedelta, workers: int = 8):
    session = net.make_session()
    targets = [f for f in feeds if f.get("enabled", True)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda f: fetch_feed(session, f, cache, now, max_age), targets))
    articles = [a for items, _ in results for a in items]
    statuses = [s for _, s in results]
    return articles, statuses


def _gdelt_time(value: str, now: datetime) -> datetime:
    try:
        return min(datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), now)
    except (TypeError, ValueError):
        return now


def parse_gdelt(data: dict, category: str, now: datetime) -> list[dict]:
    articles = []
    for item in data.get("articles", []) or []:
        lang = GDELT_LANGS.get(str(item.get("language", "")).lower(), "")
        article = make_article(
            url=item.get("url"),
            title=item.get("title"),
            summary="",
            source=item.get("domain") or "GDELT",
            category=category,
            region=item.get("sourcecountry") or "",
            lang=lang,
            published=_gdelt_time(item.get("seendate", ""), now),
        )
        if article:
            article["via"] = "GDELT"
            articles.append(article)
    return articles


def fetch_gdelt(queries: list[dict], now: datetime, timespan: str = "6h", max_records: int = 50):
    session = net.make_session()
    articles, statuses = [], []
    for i, q in enumerate(queries):
        if i:
            time.sleep(GDELT_INTERVAL)
        status = {"name": f"GDELT ({q['category']})", "category": q["category"], "type": "gdelt"}
        params = {
            "query": q["query"], "mode": "artlist", "format": "json", "sort": "datedesc",
            "timespan": timespan, "maxrecords": str(max_records),
        }
        try:
            resp = net.get(session, GDELT_URL, params=params)
            data = resp.json() if resp.content.strip() else {}
            found = parse_gdelt(data, q["category"], now)
        except (net.FetchError, ValueError) as exc:
            statuses.append({**status, "ok": False, "items": 0, "error": str(exc) or type(exc).__name__})
            continue
        articles.extend(found)
        statuses.append({**status, "ok": True, "items": len(found)})
    return articles, statuses
