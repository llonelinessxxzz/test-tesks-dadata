from unittest.mock import Mock

import pytest

from inn_website.evidence import context
from inn_website.models import Candidate, Hit, Plan, Verdict, domain_of, validate_inn
from inn_website.pipeline import Pipeline

INN = "7721581040"
URL = "https://dadata.ru/requisites"
TEXT = "Владелец сервиса ООО Дейта Кью. ИНН 7721581040. Реквизиты компании."


def hit(url=URL, text=TEXT):
    return Hit(url=url, title="Реквизиты", text=text)


def verdict(domain="dadata.ru", **updates):
    values = dict(
        domain=domain,
        is_official=True,
        is_primary=True,
        ambiguous=False,
        evidence_id="e1",
        relationship="owner",
        reason="Реквизиты владельца сервиса",
    )
    return Verdict(**(values | updates))


def pipeline(evidence=None, decision=None, candidate=None):
    search = Mock()
    search.search.side_effect = [
        [hit(text="Главная страница сервиса")],
        [hit()] if evidence is None else evidence,
    ]
    llm = Mock()
    llm.structured.side_effect = [
        Plan(
            organization_name=None,
            candidates=[candidate or Candidate(domain="dadata.ru", source_url=URL)],
        ),
        decision or verdict(),
    ]
    return Pipeline(search, llm)


def test_verified_domain():
    app = pipeline()
    assert app.run(INN).model_dump() == {"domain": "dadata.ru"}
    assert app.trace["reason"] == "verified"
    assert app.trace["verdicts"][0]["quote"] == TEXT
    assert app.trace["verdicts"][0]["source_url"] == URL


def test_verification_uses_original_excerpt_without_llm_transcription():
    text = "ПАО «Пример» (Оператор) ИНН 7721581040 / КПП 770401001"
    app = pipeline(evidence=[hit(text=text)])
    assert app.run(INN).domain == "dadata.ru"
    assert app.trace["verdicts"][0]["quote"] == text
    payload = app.llm.structured.call_args.args[1]
    assert payload["evidence"]["e1"]["text"] == text


@pytest.mark.parametrize(
    "updates",
    [
        {"evidence_id": "made-up"},
        {"evidence_id": None},
        {"domain": "invented.ru"},
        {"is_official": False},
        {"is_primary": False},
        {"ambiguous": True},
        {"domain": None},
        {"relationship": "customer"},
        {"relationship": "partner"},
        {"relationship": "directory"},
        {"relationship": "unknown"},
    ],
)
def test_unproven_verdict_returns_null(updates):
    assert pipeline(decision=verdict(**updates)).run(INN).domain is None


@pytest.mark.parametrize(
    "evidence",
    [
        [],
        [hit(text="Реквизиты без ИНН")],
        [hit(text="ИНН 177215810400")],
        [hit(url="https://dadata.ru.attacker.com/legal")],
    ],
)
def test_missing_evidence_skips_verifier(evidence):
    app = pipeline(evidence=evidence)
    assert app.run(INN).domain is None
    assert app.llm.structured.call_count == 1


def test_invented_candidate_is_not_searched():
    app = pipeline(candidate=Candidate(domain="invented.ru", source_url=URL))
    assert app.run(INN).domain is None
    assert app.search.search.call_count == 1


def test_empty_search_skips_llm():
    search, llm = Mock(), Mock()
    search.search.return_value = []
    assert Pipeline(search, llm).run(INN).domain is None
    llm.structured.assert_not_called()


def test_reuses_evidence_from_discovery():
    app = pipeline(evidence=[])
    app.search.search.side_effect = [[hit()], []]
    assert app.run(INN).domain == "dadata.ru"


def test_empty_exact_search_retries_without_quotes():
    app = pipeline()
    app.search.search.side_effect = [[], [hit()], [hit()]]
    assert app.run(INN).domain == "dadata.ru"
    assert app.search.search.call_args_list[1].args == (f"{INN} реквизиты",)


def test_long_legal_name_search_falls_back_to_short_name():
    directory = hit(url="https://checko.ru/company/1")
    search, llm = Mock(), Mock()
    search.search.side_effect = [[directory], [], [hit()], [hit()]]
    llm.structured.side_effect = [
        Plan(organization_name="ООО Дейта Кью", locality=None, candidates=[]),
        Plan(
            organization_name="ООО Дейта Кью",
            candidates=[
                Candidate(domain="dadata.ru", source_url=URL),
            ],
        ),
        verdict(),
    ]
    assert Pipeline(search, llm).run(INN).domain == "dadata.ru"
    assert search.search.call_args_list[2].args == ("Дейта Кью официальный сайт",)


def test_extracts_page_when_snippet_has_no_inn():
    app = pipeline(evidence=[hit(text="Краткое описание")])
    extractor = Mock()
    extractor.extract.return_value = [hit()]
    app.extractor = extractor
    assert app.run(INN).domain == "dadata.ru"
    extractor.extract.assert_called_once_with([URL])


def test_extraction_cannot_switch_domains():
    app = pipeline(evidence=[hit(text="Краткое описание")])
    extractor = Mock()
    extractor.extract.return_value = [hit(url="https://attacker.ru/legal")]
    app.extractor = extractor
    assert app.run(INN).domain is None


def test_directory_link_can_propose_but_not_verify_domain():
    directory = hit(url="https://checko.ru/company/1", text=TEXT + " Сайт https://dadata.ru")
    search, llm = Mock(), Mock()
    search.search.side_effect = [[directory], [hit()]]
    llm.structured.side_effect = [
        Plan(
            organization_name=None,
            candidates=[
                Candidate(
                    domain="dadata.ru",
                    source_url=directory.url,
                )
            ],
        ),
        verdict(),
    ]
    assert Pipeline(search, llm).run(INN).domain == "dadata.ru"


def test_missed_shortlist_candidate_still_requires_verification():
    identity = {"name": "ООО Дейта Кью", "locality": "Москва"}
    plan = Plan(organization_name="ООО Дейта Кью", candidates=[])
    assert Pipeline._candidates(plan, [hit()], identity) == ["dadata.ru"]
    assert Pipeline._candidates(plan, [hit(text="Другая компания")], identity) == []


def test_fallback_does_not_use_directory_as_official_site():
    identity = {"name": "ООО Дейта Кью", "locality": "Москва"}
    plan = Plan(organization_name="ООО Дейта Кью", candidates=[])
    assert Pipeline._candidates(plan, [hit(url="https://checko.ru/company/1")], identity) == []


def test_russian_version_of_same_domain_is_preferred():
    second = "https://dadata.com/legal"
    search, llm = Mock(), Mock()
    search.search.side_effect = [[hit(), hit(second)], [hit()], [hit(second)]]
    llm.structured.side_effect = [
        Plan(
            organization_name=None,
            candidates=[
                Candidate(domain="dadata.ru", source_url=URL),
                Candidate(domain="dadata.com", source_url=second),
            ],
        ),
        verdict(),
        verdict(domain="dadata.com"),
    ]
    app = Pipeline(search, llm)
    assert app.run(INN).domain == "dadata.ru"
    assert app.trace["reason"] == "verified_russian_version"


@pytest.mark.parametrize("name", ["Дейта Кью", "«Дейта Кью»"])
def test_search_by_verified_organization_name(name):
    search, llm = Mock(), Mock()
    directory = hit(url="https://checko.ru/company/1", text=TEXT)
    search.search.side_effect = [[directory], [hit()], [hit()]]
    llm.structured.side_effect = [
        Plan(organization_name=name, candidates=[]),
        Plan(
            organization_name="Дейта Кью",
            candidates=[
                Candidate(domain="dadata.ru", source_url=URL),
            ],
        ),
        verdict(),
    ]
    assert Pipeline(search, llm).run(INN).domain == "dadata.ru"
    assert search.search.call_args_list[1].args == (f"{name} официальный сайт",)
    payload = llm.structured.call_args_list[1].args[1]
    assert payload["verified_identity"]["name"] == name
    assert [item["url"] for item in payload["hits"]] == [URL]


def test_invented_organization_name_does_not_trigger_search():
    search, llm = Mock(), Mock()
    search.search.return_value = [hit()]
    llm.structured.return_value = Plan(organization_name="Выдуманное имя", candidates=[])
    assert Pipeline(search, llm).run(INN).domain is None
    assert search.search.call_count == 1


def test_context_keeps_inn_and_respects_budget():
    text = "а" * 4000 + " ИНН " + INN + " реквизиты " + "б" * 4000
    excerpts = context([hit(text=text)] * 10, INN, 4000)
    assert excerpts
    assert all(INN in item["text"] for item in excerpts)
    assert all(item["text"] in text for item in excerpts)
    assert sum(len(value) for item in excerpts for value in item.values()) <= 4000


def test_directory_is_rejected():
    url = "https://rusprofile.ru/company/1"
    search, llm = Mock(), Mock()
    search.search.return_value = [hit(url)]
    llm.structured.return_value = Plan(
        organization_name=None, candidates=[Candidate(domain="rusprofile.ru", source_url=url)]
    )
    assert Pipeline(search, llm).run(INN).domain is None
    assert search.search.call_count == 1


def test_multiple_verified_sites_abstain():
    second = "https://example.ru/legal"
    search, llm = Mock(), Mock()
    search.search.side_effect = [[hit(), hit(second)], [hit()], [hit(second)]]
    llm.structured.side_effect = [
        Plan(
            organization_name=None,
            candidates=[
                Candidate(domain="dadata.ru", source_url=URL),
                Candidate(domain="example.ru", source_url=second),
            ],
        ),
        verdict(),
        verdict(domain="example.ru"),
    ]
    assert Pipeline(search, llm).run(INN).domain is None


@pytest.mark.parametrize("inn", ["7721581040", "7707083893", "500100732259"])
def test_valid_inn(inn):
    assert validate_inn(inn) == inn


@pytest.mark.parametrize("inn", ["7721581041", "0000000000", "123", " 7721581040", "abcdefghij"])
def test_invalid_inn_never_reaches_providers(inn):
    search, llm = Mock(), Mock()
    with pytest.raises(ValueError):
        Pipeline(search, llm).run(inn)
    search.search.assert_not_called()
    llm.structured.assert_not_called()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://WWW.DADATA.RU/legal", "dadata.ru"),
        ("https://sub.example.co.uk/a", "example.co.uk"),
        ("https://пример.рф", "xn--e1afmkfd.xn--p1ai"),
        ("https://foo.github.io", "foo.github.io"),
        ("https://127.0.0.1", None),
        ("https://localhost", None),
        ("https://user:password@example.ru", None),
        ("file:///etc/passwd", None),
        ("https://-bad.ru", None),
    ],
)
def test_domain_normalization(value, expected):
    assert domain_of(value) == expected
