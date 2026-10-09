import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from newsdesk import translate

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def art(i, lang="en", title=None):
    return {"id": f"id{i}", "title": title or f"Title {i}", "summary": f"Summary {i}", "lang": lang,
            "published": f"2026-10-09T{i:02d}:00:00+00:00"}


class FakeTranslator:
    name = "fake"
    batch_size = 2

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def translate(self, items):
        self.calls.append([i["id"] for i in items])
        if self.fail_on and self.fail_on in [i["id"] for i in items]:
            raise translate.TranslationError("一時的なエラー")
        return {i["id"]: (f"見出し{i['id']}", f"要約{i['id']}") for i in items}


def test_translates_and_caches_and_skips_japanese():
    articles = [art(1), art(2), art(3, lang="ja")]
    cache = {}
    tr = FakeTranslator()
    stats = translate.translate_articles(articles, tr, cache, NOW)
    assert stats["translated"] == 2
    assert articles[0]["title_ja"] == "見出しid1"
    assert "title_ja" not in articles[2]  # 日本語記事は翻訳しない
    # 2 回目はキャッシュから(API を呼ばない)
    again = [art(1), art(2)]
    tr2 = FakeTranslator()
    stats2 = translate.translate_articles(again, tr2, cache, NOW)
    assert tr2.calls == [] and stats2["cached"] == 2 and again[1]["title_ja"] == "見出しid2"


def test_max_new_limits_cost_and_prefers_newest():
    articles = [art(i) for i in range(1, 6)]
    tr = FakeTranslator()
    stats = translate.translate_articles(articles, tr, {}, NOW, max_new=3)
    assert stats["translated"] == 3 and stats["pending"] == 2
    assert sorted(sum(tr.calls, [])) == ["id3", "id4", "id5"]


def test_failed_batch_is_left_untranslated():
    articles = [art(1), art(2), art(3), art(4)]
    tr = FakeTranslator(fail_on="id4")
    stats = translate.translate_articles(articles, tr, {}, NOW)
    assert stats["failed"] == 2 and stats["translated"] == 2
    assert stats["errors"] == ["一時的なエラー"]


def test_no_translator_marks_pending():
    stats = translate.translate_articles([art(1)], None, {}, NOW)
    assert stats["pending"] == 1 and stats["engine"] == "none"


def test_prune_cache():
    cache = {"old": {"t": "a", "s": "", "at": "2026-09-01T00:00:00+00:00"},
             "new": {"t": "b", "s": "", "at": "2026-10-08T00:00:00+00:00"},
             "bad": {"t": "c"}}
    assert set(translate.prune_cache(cache, NOW)) == {"new"}


def test_make_translator_selection(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    monkeypatch.delenv("TRANSLATOR", raising=False)
    assert translate.make_translator() is None
    monkeypatch.setenv("DEEPL_API_KEY", "abc:fx")
    tr = translate.make_translator()
    assert tr.name == "deepl" and "api-free.deepl.com" in tr.url
    with pytest.raises(SystemExit):
        translate.make_translator("claude")


class _FakeMessages:
    def __init__(self, text, stop_reason="end_turn"):
        self.text, self.stop_reason, self.kwargs = text, stop_reason, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=self.text)])


def _claude(monkeypatch, text, stop_reason="end_turn", model=None):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    tr = translate.ClaudeTranslator(model=model)
    msgs = _FakeMessages(text, stop_reason)
    tr.client = SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=msgs))
    return tr, msgs


def test_claude_translator_request_and_parse(monkeypatch):
    out = json.dumps({"items": [{"id": "id1", "title": "見出し", "summary": "要約"},
                                {"id": "unknown", "title": "x", "summary": ""}]})
    tr, msgs = _claude(monkeypatch, out)
    result = tr.translate([art(1)])
    assert result == {"id1": ("見出し", "要約")}
    kw = msgs.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "Title 1" in kw["messages"][0]["content"]


def test_claude_translator_no_fallback_for_unsupported_model(monkeypatch):
    tr, msgs = _claude(monkeypatch, json.dumps({"items": []}), model="claude-haiku-5-5")
    tr.translate([art(1)])
    assert "fallbacks" not in msgs.kwargs and "betas" not in msgs.kwargs


def test_claude_translator_refusal(monkeypatch):
    tr, _ = _claude(monkeypatch, "", stop_reason="refusal")
    with pytest.raises(translate.TranslationError):
        tr.translate([art(1)])


def test_deepl_translator(monkeypatch):
    tr = translate.DeepLTranslator("key")
    assert tr.url == "https://api.deepl.com/v2/translate"
    sent = {}

    def post(url, headers, json, timeout):
        sent.update(json)
        return SimpleNamespace(status_code=200, json=lambda: {"translations": [
            {"text": "見出し1"}, {"text": "要約1"}, {"text": "見出し2"}, {"text": "-"}]})

    tr.session = SimpleNamespace(post=post)
    a2 = art(2)
    a2["summary"] = ""
    result = tr.translate([art(1), a2])
    assert sent["target_lang"] == "JA"
    assert result == {"id1": ("見出し1", "要約1"), "id2": ("見出し2", "")}
