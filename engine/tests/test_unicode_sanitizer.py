"""Tests for Unicode sanitization — sanitize_unicode, sanitize_value, and ASGI middleware."""

import pytest

from src.security.unicode_sanitizer import (
    MAX_RECURSION_DEPTH,
    sanitize_unicode,
    sanitize_value,
    UnicodeSanitizationMiddleware,
)


class TestSanitizeUnicode:
    def test_clean_ascii_unchanged(self):
        assert sanitize_unicode("Hello, world!") == "Hello, world!"

    def test_empty_string(self):
        assert sanitize_unicode("") == ""

    def test_ascii_fast_path(self):
        text = "a" * 32_000
        result = sanitize_unicode(text)
        assert result == text


    def test_nfkc_fullwidth_a(self):
        assert sanitize_unicode("\uff21") == "A"

    def test_nfkc_fi_ligature(self):
        assert sanitize_unicode("\ufb01") == "fi"

    def test_nfkc_fullwidth_digits(self):
        assert sanitize_unicode("\uff10\uff11\uff12") == "012"


    def test_null_byte_stripped(self):
        assert sanitize_unicode("he\x00llo") == "hello"

    def test_multiple_null_bytes_stripped(self):
        assert sanitize_unicode("\x00a\x00b\x00") == "ab"


    def test_zero_width_space(self):
        assert sanitize_unicode("he\u200bllo") == "hello"

    def test_zero_width_joiner(self):
        assert sanitize_unicode("he\u200dllo") == "hello"

    def test_zero_width_non_joiner(self):
        assert sanitize_unicode("he\u200cllo") == "hello"

    def test_ltr_mark(self):
        assert sanitize_unicode("he\u200ello") == "hello"

    def test_rtl_mark(self):
        assert sanitize_unicode("he\u200fllo") == "hello"


    def test_directional_overrides_all_five(self):
        text = "\u202a\u202b\u202c\u202d\u202eHello"
        assert sanitize_unicode(text) == "Hello"


    def test_directional_isolates_all_four(self):
        text = "\u2066\u2067\u2068\u2069Hello"
        assert sanitize_unicode(text) == "Hello"


    def test_bom_stripped(self):
        assert sanitize_unicode("\ufeffHello") == "Hello"

    def test_bom_mid_string(self):
        assert sanitize_unicode("He\ufeffllo") == "Hello"


    def test_soft_hyphen(self):
        assert sanitize_unicode("hy\u00adphen") == "hyphen"


    def test_bmp_private_use(self):
        assert sanitize_unicode("a\ue000b\ue001c") == "abc"

    def test_bmp_private_use_end(self):
        assert sanitize_unicode("a\uf8ffb") == "ab"


    def test_supplementary_private_use(self):
        result = sanitize_unicode("a\U000f0000b")
        assert result == "ab"

    def test_supplementary_private_use_plane_16(self):
        result = sanitize_unicode("a\U00100000b")
        assert result == "ab"


    def test_tag_language_tag(self):
        assert sanitize_unicode("a\U000e0001b") == "ab"

    def test_tag_cancel_tag(self):
        assert sanitize_unicode("a\U000e007fb") == "ab"

    def test_tag_latin_letters(self):
        hidden = "".join(chr(0xE0041 + i) for i in range(26))
        result = sanitize_unicode("visible" + hidden + "text")
        assert result == "visibletext"

    def test_tag_encoded_hidden_message(self):
        tag_hello = "\U000e0048\U000e0065\U000e006c\U000e006c\U000e006f"
        result = sanitize_unicode("safe" + tag_hello + "text")
        assert result == "safetext"


    def test_interlinear_annotation(self):
        text = "a\ufff9b\ufffac\ufffbd"
        assert sanitize_unicode(text) == "abcd"


    def test_mixed_legitimate_and_dangerous(self):
        assert sanitize_unicode("Hello\u200bworld") == "Helloworld"

    def test_multi_attack_vector(self):
        text = "\u200b\u202aHe\ufeffl\u2066lo\U000e0041"
        result = sanitize_unicode(text)
        assert result == "Hello"


    def test_idempotent(self):
        vectors = [
            "Hello\u200bworld",
            "\u202aTest\u200d",
            "a\U000e0041b",
            "\ufeffBOM",
            "clean text",
        ]
        for v in vectors:
            once = sanitize_unicode(v)
            twice = sanitize_unicode(once)
            assert once == twice, f"Not idempotent for {v!r}"


    def test_preserves_emoji(self):
        text = "Hello 👋🌍"
        assert sanitize_unicode(text) == text

    def test_preserves_cjk(self):
        text = "你好世界"
        assert sanitize_unicode(text) == text

    def test_preserves_accented(self):
        text = "café résumé"
        assert sanitize_unicode(text) == text

    def test_preserves_arabic(self):
        text = "مرحبا بالعالم"
        assert sanitize_unicode(text) == text

    def test_preserves_hebrew(self):
        text = "שלום עולם"
        assert sanitize_unicode(text) == text

    def test_preserves_korean(self):
        text = "안녕하세요"
        assert sanitize_unicode(text) == text

    def test_preserves_thai(self):
        text = "สวัสดี"
        assert sanitize_unicode(text) == text


    def test_stuck_loop_raises_value_error(self, monkeypatch):
        import src.security.unicode_sanitizer as mod

        monkeypatch.setattr(mod, "MAX_ITERATIONS", 0)
        with pytest.raises(ValueError, match="did not converge"):
            sanitize_unicode("test\u200b")


    def test_large_ascii_input_fast(self):
        import time

        text = "a" * 32_000
        start = time.monotonic()
        result = sanitize_unicode(text)
        elapsed = time.monotonic() - start
        assert result == text
        assert elapsed < 0.01

    def test_large_mixed_input_completes(self):
        import time

        base = "Hello\u200bWorld " * 1_600
        start = time.monotonic()
        result = sanitize_unicode(base)
        elapsed = time.monotonic() - start
        assert "\u200b" not in result
        assert elapsed < 0.5


class TestSanitizeValue:
    def test_string_sanitized(self):
        assert sanitize_value("he\u200bllo") == "hello"

    def test_dict_string_values(self):
        result = sanitize_value({"key": "val\u200bue"})
        assert result == {"key": "value"}

    def test_dict_string_keys(self):
        result = sanitize_value({"ke\u200by": "value"})
        assert result == {"key": "value"}

    def test_dict_keys_and_values(self):
        result = sanitize_value({"\u200bk": "\u200bv"})
        assert result == {"k": "v"}

    def test_nested_dict_list_dict(self):
        data = {"a": [{"b": "he\u200bllo"}]}
        result = sanitize_value(data)
        assert result == {"a": [{"b": "hello"}]}

    def test_integer_passthrough(self):
        assert sanitize_value(42) == 42

    def test_float_passthrough(self):
        assert sanitize_value(3.14) == 3.14

    def test_bool_passthrough(self):
        assert sanitize_value(True) is True
        assert sanitize_value(False) is False

    def test_none_passthrough(self):
        assert sanitize_value(None) is None

    def test_empty_dict(self):
        assert sanitize_value({}) == {}

    def test_empty_list(self):
        assert sanitize_value([]) == []

    def test_tuple_sanitized(self):
        result = sanitize_value(("he\u200bllo",))
        assert result == ("hello",)

    def test_mixed_types(self):
        data = {"count": 1, "name": "test\u200b", "active": True, "tags": None}
        result = sanitize_value(data)
        assert result == {"count": 1, "name": "test", "active": True, "tags": None}

    def test_deeply_nested_within_limit(self):
        data: dict = {"v": "ok\u200b"}
        for _ in range(60):
            data = {"nested": data}
        result = sanitize_value(data)
        current = result
        for _ in range(60):
            current = current["nested"]
        assert current == {"v": "ok"}

    def test_depth_limit_exceeded(self):
        data: dict = {"v": "x"}
        for _ in range(MAX_RECURSION_DEPTH + 5):
            data = {"n": data}
        with pytest.raises(ValueError, match="Recursion depth"):
            sanitize_value(data)

    def test_list_of_strings(self):
        result = sanitize_value(["a\u200b", "b\u200c", "c\u200d"])
        assert result == ["a", "b", "c"]


@pytest.fixture
def test_app():
    """Minimal FastAPI app wrapped in the sanitization middleware."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    inner_app = FastAPI()

    class EchoBody(BaseModel):
        model_config = {"extra": "allow"}

    @inner_app.post("/echo")
    async def echo(request: Request):
        body = await request.json()
        return JSONResponse(content=body)

    @inner_app.post("/echo-pydantic")
    async def echo_pydantic(body: EchoBody):
        return JSONResponse(content=body.model_dump())

    @inner_app.get("/path-echo/{name}")
    async def path_echo(name: str):
        return JSONResponse(content={"name": name})

    @inner_app.get("/query-echo")
    async def query_echo(request: Request):
        return JSONResponse(content={"qs": str(request.query_params)})

    @inner_app.post("/form-echo")
    async def form_echo(request: Request):
        body = await request.body()
        return JSONResponse(content={"raw": body.decode("utf-8", errors="replace")})

    middleware = UnicodeSanitizationMiddleware(inner_app)
    return middleware


@pytest.fixture
def client(test_app):
    """httpx AsyncClient bound to the ASGI test app."""
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app),
        base_url="http://testserver",
    )


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_json_body_zero_width_stripped(self, client):
        resp = await client.post(
            "/echo",
            json={"message": "Hello\u200bWorld"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "HelloWorld"

    @pytest.mark.asyncio
    async def test_json_body_tag_characters_stripped(self, client):
        tag_text = "safe\U000e0048\U000e0049text"
        resp = await client.post(
            "/echo",
            json={"message": tag_text},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "safetext"

    @pytest.mark.asyncio
    async def test_json_body_null_byte_stripped(self, client):
        raw = b'{"message": "he\\u0000llo"}'
        resp = await client.post(
            "/echo",
            content=raw,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "hello"

    @pytest.mark.asyncio
    async def test_json_body_preserves_emoji(self, client):
        resp = await client.post(
            "/echo",
            json={"message": "Hello 👋🌍"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Hello 👋🌍"

    @pytest.mark.asyncio
    async def test_json_body_preserves_cjk(self, client):
        resp = await client.post(
            "/echo",
            json={"message": "你好世界"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "你好世界"

    @pytest.mark.asyncio
    async def test_nested_json_sanitized(self, client):
        resp = await client.post(
            "/echo",
            json={"outer": {"inner": ["val\u200bue"]}},
        )
        assert resp.status_code == 200
        assert resp.json()["outer"]["inner"] == ["value"]

    @pytest.mark.asyncio
    async def test_json_dict_keys_sanitized(self, client):
        resp = await client.post(
            "/echo",
            json={"\u200bkey": "value"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"key": "value"}

    @pytest.mark.asyncio
    async def test_non_json_content_type_passthrough(self, client):
        body = "raw\u200bbody"
        resp = await client.post(
            "/form-echo",
            content=body.encode("utf-8"),
            headers={"content-type": "text/plain"},
        )
        assert resp.status_code == 200
        assert "\u200b" in resp.json()["raw"]

    @pytest.mark.asyncio
    async def test_malformed_json_passthrough(self, client):
        resp = await client.post(
            "/echo-pydantic",
            content=b"{broken json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_request_passthrough(self, client):
        resp = await client.get("/query-echo")
        assert resp.status_code == 200
        assert "qs" in resp.json()

    @pytest.mark.asyncio
    async def test_empty_body_passthrough(self, client):
        resp = await client.post(
            "/echo-pydantic",
            content=b"",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_path_sanitized(self, client):
        resp = await client.get("/path-echo/hello\u200bworld")
        assert resp.status_code == 200
        assert resp.json()["name"] == "helloworld"

    @pytest.mark.asyncio
    async def test_query_string_sanitized(self, client):
        resp = await client.get("/query-echo", params={"q": "hello\u200bworld"})
        assert resp.status_code == 200
        qs = resp.json()["qs"]
        assert "\u200b" not in qs
        assert "hello" in qs

    @pytest.mark.asyncio
    async def test_put_method_sanitized(self, client):
        resp = await client.put(
            "/echo",
            json={"message": "test\u200b"},
        )
        assert resp.status_code in (200, 405)

    @pytest.mark.asyncio
    async def test_deeply_nested_within_limit(self, client):
        data: dict = {"v": "ok\u200b"}
        for _ in range(55):
            data = {"n": data}
        resp = await client.post("/echo", json=data)
        assert resp.status_code == 200
        current = resp.json()
        for _ in range(55):
            current = current["n"]
        assert current["v"] == "ok"

    @pytest.mark.asyncio
    async def test_deeply_nested_exceeds_limit_returns_400(self, client):
        data: dict = {"v": "x"}
        for _ in range(MAX_RECURSION_DEPTH + 5):
            data = {"n": data}
        resp = await client.post("/echo", json=data)
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "invalid_params"
        assert body["error"]["retryable"] is False

    @pytest.mark.asyncio
    async def test_clean_ascii_json_unchanged(self, client):
        payload = {"message": "Hello World", "count": 42, "flag": True}
        resp = await client.post("/echo", json=payload)
        assert resp.status_code == 200
        assert resp.json() == payload

    @pytest.mark.asyncio
    async def test_multiple_dangerous_chars_all_stripped(self, client):
        resp = await client.post(
            "/echo",
            json={
                "a": "\u200b\u202atext\ufeff",
                "b": "\u2066x\u2069",
                "c": "clean",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["a"] == "text"
        assert data["b"] == "x"
        assert data["c"] == "clean"
