"""チャート用の市場データ(日足終値・約1年分)を取得する。

Yahoo Finance のチャート API を使い、失敗したら Stooq の CSV で代替する。
どちらも API キー不要だが公式の公開 API ではないため、
将来使えなくなった場合は Twelve Data / Alpha Vantage 等のキー付き API へ切り替える。
"""

from __future__ import annotations

import csv
import io
import logging
import math
from datetime import datetime, timezone
from urllib.parse import quote

from . import net

log = logging.getLogger(__name__)

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
STOOQ_URL = "https://stooq.com/q/d/l/"
MAX_POINTS = 400


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def parse_yahoo(data: dict) -> tuple[list[list], str]:
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError("Yahoo: データなし")
    res = result[0]
    stamps = res.get("timestamp") or []
    closes = (((res.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    points = {}
    for ts, close in zip(stamps, closes):
        if _finite(close):
            day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
            points[day] = round(float(close), 4)  # 同日の重複は最後の値を採用
    currency = str((res.get("meta") or {}).get("currency") or "")[:5]
    return [[d, v] for d, v in sorted(points.items())], currency


def parse_stooq(text: str) -> list[list]:
    points = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            close = float(row.get("Close") or "")
            day = datetime.strptime(row.get("Date", ""), "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            continue
        if _finite(close):
            points.append([day, round(close, 4)])
    if not points:
        raise ValueError("Stooq: データなし")
    return points


def summarize(instrument: dict, points: list[list], currency: str, source: str, now: datetime) -> dict:
    points = points[-MAX_POINTS:]
    last = points[-1][1]
    prev = points[-2][1] if len(points) > 1 else last
    return {
        "id": instrument["id"],
        "name": instrument["name"],
        "group": instrument.get("group", ""),
        "currency": currency,
        "last": last,
        "prev": prev,
        "change": round(last - prev, 4),
        "change_pct": round((last - prev) / prev * 100, 2) if prev else 0.0,
        "as_of": points[-1][0],
        "points": points,
        "source": source,
        "updated": now.isoformat(timespec="seconds"),
    }


def fetch_instrument(session, instrument: dict, now: datetime) -> dict:
    errors = []
    if instrument.get("yahoo"):
        try:
            resp = net.get(session, YAHOO_URL.format(symbol=quote(instrument["yahoo"], safe="")),
                           params={"range": "1y", "interval": "1d"}, max_bytes=2 * 1024 * 1024)
            points, currency = parse_yahoo(resp.json())
            if points:
                return summarize(instrument, points, currency, "Yahoo Finance", now)
        except (net.FetchError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"Yahoo: {exc}")
    if instrument.get("stooq"):
        try:
            resp = net.get(session, STOOQ_URL, params={"s": instrument["stooq"], "i": "d"},
                           max_bytes=2 * 1024 * 1024)
            points = parse_stooq(resp.text)[-260:]
            return summarize(instrument, points, "", "Stooq", now)
        except (net.FetchError, ValueError) as exc:
            errors.append(f"Stooq: {exc}")
    raise net.FetchError(" / ".join(errors) or "データ源が未設定です")


def fetch_markets(instruments: list[dict], now: datetime, previous: dict | None = None):
    """全銘柄を取得。失敗した銘柄は前回の値を残す。(銘柄リスト, ステータス) を返す。"""
    session = net.make_session()
    previous = previous or {}
    out, statuses = [], []
    for inst in instruments:
        status = {"name": inst["name"], "category": "markets", "type": "market"}
        try:
            out.append(fetch_instrument(session, inst, now))
            statuses.append({**status, "ok": True, "items": 1})
        except net.FetchError as exc:
            log.warning("market %s failed: %s", inst["id"], exc)
            statuses.append({**status, "ok": False, "items": 0, "error": str(exc)})
            if inst["id"] in previous:
                out.append({**previous[inst["id"]], "stale": True})
    return out, statuses
