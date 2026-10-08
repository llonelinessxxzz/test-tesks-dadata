import pytest

from inn_website.evaluate import evaluate_case


@pytest.mark.parametrize(
    ("case", "domain", "expected"),
    [
        ({"domains": ["example.ru"]}, "example.ru", "match"),
        ({"domains": ["example.ru"]}, None, "miss"),
        ({"domains": ["example.ru"]}, "unrelated.ru", "wrong"),
        ({"forbidden_domains": ["sberbank.ru"]}, None, "not_confused"),
        ({"forbidden_domains": ["sberbank.ru"]}, "sberbank.ru", "wrong"),
    ],
)
def test_evaluation_distinguishes_missing_and_wrong_domains(case, domain, expected):
    assert evaluate_case(case, domain) == expected
