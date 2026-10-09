from newsdesk.sanitize import canonical_url, clean_text, html_to_text, safe_url, title_key


def test_html_to_text_strips_tags_and_scripts():
    assert html_to_text("<p>Hello <b>world</b><script>alert(1)</script></p>") == "Hello world"
    assert html_to_text("A &amp; B &lt;tag&gt;") == "A & B <tag>"
    assert html_to_text(None) == ""


def test_clean_text_removes_control_and_bidi_chars():
    assert clean_text("a\x00b‮c  d") == "abc d"
    assert clean_text("x" * 20, max_len=10) == "x" * 9 + "…"


def test_safe_url_accepts_only_http_and_https():
    assert safe_url("https://example.com/a?b=1#frag") == "https://example.com/a?b=1"
    assert safe_url("HTTP://example.com/") == "http://example.com/"
    for bad in [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox",
        "//example.com/x",
        "/relative",
        "https://user:pass@example.com/",
        "https://exa mple.com/",
        "https://example.com/\nx",
        "",
        None,
        "https://" + "a" * 3000 + ".com",
    ]:
        assert safe_url(bad) is None, bad


def test_canonical_url_drops_tracking_params():
    a = canonical_url("https://www.example.com/news/1/?utm_source=x&id=5")
    b = canonical_url("http://example.com/news/1?id=5&fbclid=abc")
    assert a == b == "https://example.com/news/1?id=5"


def test_title_key_ignores_case_and_punctuation():
    assert title_key("Fed Holds Rates!") == title_key("fed holds rates")
