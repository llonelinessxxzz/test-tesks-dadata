import pytest

from inn_website.evidence import context, is_directory, merge_hits, verified_identity
from inn_website.models import Hit, Plan


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://yandex.ru/legal/general_offer/ru/", False),
        ("https://yandex.ru/maps/org/1", True),
        ("https://tbank.ru/about/properties", False),
        ("https://tbank.ru/business/contractor/legal/1", True),
        ("https://kontur.ru/about", False),
        ("https://focus.kontur.ru/entity?query=1", True),
        ("https://saby.ru/about", False),
        ("https://saby.ru/profile/123", True),
    ],
)
def test_directories_do_not_block_owners(url, expected):
    assert is_directory(url) is expected


def test_identity_preserves_legal_form_and_city():
    plan = Plan(candidates=[], organization_name="ООО СБЕР", locality="Липецк")
    hit = Hit(url="https://example.ru", title="ООО «СБЕР», Липецк", text="ИНН 4826041657")
    assert verified_identity(plan, [hit], "4826041657") == {
        "name": "ООО СБЕР",
        "locality": "Липецк",
        "source_url": hit.url,
    }


def test_identity_rejects_invented_city():
    plan = Plan(candidates=[], organization_name="ООО СБЕР", locality="Москва")
    hit = Hit(url="https://example.ru", title="ООО «СБЕР», Липецк", text="ИНН 4826041657")
    assert verified_identity(plan, [hit], "4826041657")["locality"] is None


def test_identity_does_not_use_different_legal_entity():
    plan = Plan(candidates=[], organization_name="ПАО Сбербанк", locality="Москва")
    hit = Hit(url="https://example.ru", title="ООО «СБЕР», Липецк", text="ИНН 4826041657")
    assert verified_identity(plan, [hit], "4826041657") is None


def test_brand_requires_source_with_same_inn():
    plan = Plan(candidates=[], organization_name="ООО Пример", brand="Сервис Пример")
    legal = Hit(url="https://example.ru", title="ООО Пример", text="ИНН 7721581040")
    brand = Hit(url="https://directory.ru", title="Сервис Пример", text="ИНН 7721581040")
    assert "brand" not in verified_identity(plan, [legal], "7721581040")
    assert verified_identity(plan, [legal, brand], "7721581040")["brand"] == "Сервис Пример"


def test_merge_keeps_more_complete_evidence():
    short = Hit(url="https://example.ru", title="", text="ИНН")
    full = short.model_copy(update={"text": "ИНН 7721581040"})
    assert merge_hits([full], [short]) == [full]


def test_context_skips_oversized_url_without_losing_next_source():
    long = Hit(url="https://example.ru/" + "a" * 2000, title="", text="text")
    short = Hit(url="https://example.ru", title="", text="ИНН 7721581040")
    assert context([long, short], "7721581040", 500)[0]["url"] == short.url
