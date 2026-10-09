"""外部から取得した文字列・URL を無害化する。

サイトにはここを通したプレーンテキストと検証済み URL だけを出力する。
フロントエンド側でも textContent のみで描画するが、ここが一次防御。
"""

from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_WS = re.compile(r"\s+")
# 改行・タブ以外の制御文字と、表示を偽装できる双方向制御文字を除去する
_BIDI = dict.fromkeys(map(ord, "‪‫‬‭‮⁦⁧⁨⁩‎‏"))
_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|ocid$|cmpid$|ref$)", re.I)


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "iframe", "object"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """HTML 断片からテキストだけを取り出す(タグ・スクリプトは全て捨てる)。"""
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # 壊れた HTML でも落とさない
        text = re.sub(r"<[^>]*>", " ", value)
    return clean_text(html.unescape(text))


def clean_text(value: str | None, max_len: int | None = None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFC", value).translate(_BIDI)
    value = "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    value = _WS.sub(" ", value).strip()
    if max_len and len(value) > max_len:
        value = value[: max_len - 1].rstrip() + "…"
    return value


def safe_url(value: str | None) -> str | None:
    """http(s) の絶対 URL だけを通す。javascript: や認証情報付き URL は拒否。"""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) > 2048 or any(unicodedata.category(c) == "Cc" or c.isspace() for c in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))


def canonical_url(value: str) -> str:
    """重複判定用: トラッキング用パラメータ・末尾スラッシュ・www を除いた URL。"""
    parts = urlsplit(value)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)])
    host = (parts.hostname or "").lower().removeprefix("www.")
    return urlunsplit(("https", host, parts.path.rstrip("/"), query, ""))


def title_key(title: str) -> str:
    """見出しの表記ゆれを吸収した重複判定キー。"""
    return re.sub(r"[\W_]+", "", title.casefold())[:120]
