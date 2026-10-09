"""収集 → 重複排除 → 翻訳 → JSON 出力 を行うエントリポイント。

使い方:
    python -m newsdesk --state-dir state --out-dir site/data
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import CATEGORIES, markets, sources, translate
from .sanitize import canonical_url, title_key

log = logging.getLogger("newsdesk")
CONFIG_DIR = Path(__file__).parent / "config"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        log.warning("%s を読めません (%s)。初期化します", path, exc)
        return default


def write_json(path: Path, data) -> None:
    """書き込み途中のファイルが公開されないよう、一時ファイル経由で置き換える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def merge_articles(existing: list[dict], fresh: list[dict], now: datetime,
                   max_age: timedelta, limit: int) -> list[dict]:
    """既存記事(翻訳済み)を優先しつつ新着を追加し、古い記事を落とす。"""
    seen_ids, seen_urls, seen_titles = set(), set(), set()
    merged = []
    for article in [*existing, *fresh]:
        if article.get("category") not in CATEGORIES:
            continue
        try:
            published = datetime.fromisoformat(article["published"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - published > max_age:
            continue
        keys = (article["id"], canonical_url(article["url"]), title_key(article["title"]))
        if keys[0] in seen_ids or keys[1] in seen_urls or (keys[2] and keys[2] in seen_titles):
            continue
        seen_ids.add(keys[0])
        seen_urls.add(keys[1])
        if keys[2]:
            seen_titles.add(keys[2])
        merged.append(article)
    merged.sort(key=lambda a: a["published"], reverse=True)
    return merged[:limit]


def run(args) -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state_dir, out_dir = Path(args.state_dir), Path(args.out_dir)
    max_age = timedelta(hours=args.max_age_hours)

    feeds_cfg = load_json(CONFIG_DIR / "feeds.json", {})
    markets_cfg = load_json(CONFIG_DIR / "markets.json", {})
    existing = load_json(state_dir / "articles.json", [])
    feed_cache = load_json(state_dir / "feed_cache.json", {})
    tr_cache = load_json(state_dir / "translations.json", {})
    statuses = []

    fresh = []
    if not args.skip_news:
        log.info("RSS %d 件を取得中", len(feeds_cfg.get("rss", [])))
        rss_articles, rss_status = sources.fetch_all_feeds(feeds_cfg.get("rss", []), feed_cache, now, max_age)
        fresh += rss_articles
        statuses += rss_status
        if not args.skip_gdelt and feeds_cfg.get("gdelt"):
            log.info("GDELT %d クエリを取得中", len(feeds_cfg["gdelt"]))
            g_articles, g_status = sources.fetch_gdelt(feeds_cfg["gdelt"], now)
            fresh += g_articles
            statuses += g_status

    articles = merge_articles(existing, fresh, now, max_age, args.limit)

    translator = translate.make_translator(args.translator)
    tr_stats = translate.translate_articles(articles, translator, tr_cache, now, max_new=args.max_translate)
    log.info("翻訳: %s", tr_stats)

    market_data = []
    if not args.skip_markets:
        prev_markets = load_json(out_dir / "markets.json", {}).get("instruments", [])
        market_data, m_status = markets.fetch_markets(
            markets_cfg.get("instruments", []), now, {m["id"]: m for m in prev_markets}
        )
        statuses += m_status

    generated = now.isoformat(timespec="seconds")
    write_json(out_dir / "news.json", {
        "generated": generated,
        "categories": CATEGORIES,
        "count": len(articles),
        "articles": articles,
    })
    if not args.skip_markets:
        write_json(out_dir / "markets.json", {"generated": generated, "instruments": market_data})
    write_json(out_dir / "status.json", {
        "generated": generated,
        "translation": {k: v for k, v in tr_stats.items()},
        "sources": statuses,
    })

    write_json(state_dir / "articles.json", articles)
    write_json(state_dir / "feed_cache.json", feed_cache)
    write_json(state_dir / "translations.json", translate.prune_cache(tr_cache, now))

    ok = sum(1 for s in statuses if s.get("ok"))
    log.info("完了: 記事 %d 件 / 取得元 %d/%d 成功", len(articles), ok, len(statuses))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="世界のニュースを収集して日本語化する")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--out-dir", default="site/data")
    parser.add_argument("--translator", choices=["auto", "claude", "deepl", "none"], default=None,
                        help="既定は環境変数 TRANSLATOR、なければ auto")
    parser.add_argument("--max-age-hours", type=int, default=72, help="この時間より古い記事は捨てる")
    parser.add_argument("--limit", type=int, default=2500, help="保持する記事数の上限")
    parser.add_argument("--max-translate", type=int,
                        default=int(os.environ.get("MAX_TRANSLATE_PER_RUN", "200")),
                        help="1 回の実行で新規に翻訳する記事数の上限(費用の上限)")
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--skip-gdelt", action="store_true")
    parser.add_argument("--skip-markets", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # requests/anthropic のデバッグログに API キーが出ないよう抑える
    for noisy in ("urllib3", "httpx", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return run(args)
