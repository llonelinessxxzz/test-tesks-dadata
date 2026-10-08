import json

import httpx
import pytest

from inn_website.models import Plan
from inn_website.providers import LanguageModel, ProviderError, TavilySearch, Transport, settings


def test_tavily_request_and_snippets():
    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == "https://api.tavily.com/search"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["query"] == "query"
        assert payload["search_depth"] == "basic"
        assert payload["auto_parameters"] is False
        assert payload["include_answer"] is False
        assert payload["language"] == "ru"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://dadata.ru",
                        "title": "<b>DaData</b>",
                        "content": "ИНН 7721581040 &amp; реквизиты",
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        hits = TavilySearch(Transport(client), "test-key").search("query")
    assert hits[0].text == "ИНН 7721581040 & реквизиты"
    assert hits[0].title == "DaData"


def test_tavily_site_filter():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["include_domains"] == ["dadata.ru"]
        assert payload["query"] == '"7721581040" реквизиты'
        assert payload["exact_match"] is True
        return httpx.Response(200, json={"results": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert (
            TavilySearch(Transport(client), "test").search('site:dadata.ru "7721581040" реквизиты')
            == []
        )


def test_name_search_does_not_enable_exact_match():
    def handler(request):
        assert json.loads(request.content)["exact_match"] is False
        return httpx.Response(200, json={"results": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        TavilySearch(Transport(client), "test").search('ООО "СБЕР" Липецк официальный сайт')


def test_extract_only_returns_requested_public_urls():
    def handler(request):
        assert json.loads(request.content)["urls"] == ["https://example.ru/legal"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://example.ru/legal", "raw_content": "ИНН 7721581040"},
                    {"url": "https://attacker.ru", "raw_content": "ИНН 7721581040"},
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        hits = TavilySearch(Transport(client), "test").extract(
            [
                "https://example.ru/legal",
                "http://127.0.0.1",
                "file:///etc/passwd",
            ]
        )
    assert len(hits) == 1
    assert hits[0].text == "ИНН 7721581040"


@pytest.mark.parametrize("payload", [{}, {"results": None}, {"results": [{}]}])
def test_invalid_tavily_response_is_error(payload):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as client,
        pytest.raises(ProviderError, match="Tavily"),
    ):
        TavilySearch(Transport(client), "test").search("query")


def test_settings_use_tavily(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-search")
    monkeypatch.setenv("LLM_API_KEY", "test-llm")
    assert settings()["search_key"] == "test-search"


def test_missing_tavily_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-llm")
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        settings()


def test_rate_limit_retry(monkeypatch):
    monkeypatch.setattr("inn_website.providers.time.sleep", lambda _: None)
    responses = iter([httpx.Response(429), httpx.Response(503), httpx.Response(200, json={})])
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as client:
        assert Transport(client).request("GET", "https://example.com") == {}


def test_unauthorized_does_not_leak_secrets():
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret-key"))
        ) as client,
        pytest.raises(ProviderError, match="HTTP 401") as error,
    ):
        Transport(client).request("GET", "https://example.com")
    assert "secret-key" not in str(error.value)


def test_rate_limit_respects_retry_after(monkeypatch):
    delays = []
    monkeypatch.setattr("inn_website.providers.time.sleep", delays.append)
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "25.5"}),
            httpx.Response(200, json={}),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as client:
        assert Transport(client).request("GET", "https://example.com") == {}
    assert delays == [27.5]


def test_invalid_llm_json_repaired_once():
    responses = iter(["not json", '{"candidates": [], "organization_name": null}'])

    def handler(request):
        assert json.loads(request.content)["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"message": {"content": next(responses)}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = LanguageModel(Transport(client), "test", "https://example.com/v1", "test")
        assert model.structured("test", {}, Plan).candidates == []


def test_repeated_invalid_llm_json_is_error():
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
            )
        ) as client,
        pytest.raises(ProviderError, match="дважды"),
    ):
        LanguageModel(Transport(client), "test", "https://example.com", "test").structured(
            "test",
            {},
            Plan,
        )
