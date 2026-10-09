"""外部取得用の HTTP クライアント(タイムアウト・サイズ上限・リダイレクト上限つき)。"""

from __future__ import annotations

import requests

USER_AGENT = "Mozilla/5.0 (compatible; NewsDeskBot/1.0; +https://github.com/tatuyosi0217/kani)"
TIMEOUT = (10, 30)  # (接続, 読み取り) 秒
MAX_BYTES = 5 * 1024 * 1024


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def make_session() -> requests.Session:
    session = requests.Session()
    session.max_redirects = 5
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    return session


def get(session: requests.Session, url: str, *, headers: dict | None = None,
        params: dict | None = None, max_bytes: int = MAX_BYTES) -> requests.Response:
    """GET してボディを最大 max_bytes まで読む。304 はそのまま返す。"""
    try:
        resp = session.get(url, headers=headers, params=params, timeout=TIMEOUT, stream=True)
    except requests.RequestException as exc:
        raise FetchError(type(exc).__name__) from exc
    with resp:
        if resp.url and not resp.url.startswith(("https://", "http://")):
            raise FetchError("不正なリダイレクト先")
        if resp.status_code == 304:
            resp._content = b""
            return resp
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code}", resp.status_code)
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise FetchError("レスポンスが大きすぎます", resp.status_code)
        body = bytearray()
        try:
            for chunk in resp.iter_content(64 * 1024):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise FetchError("レスポンスが大きすぎます", resp.status_code)
        except requests.RequestException as exc:
            raise FetchError(type(exc).__name__, resp.status_code) from exc
        resp._content = bytes(body)
    return resp
