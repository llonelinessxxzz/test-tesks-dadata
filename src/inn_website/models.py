import re
from html import unescape
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

import tldextract
from pydantic import BaseModel, ConfigDict, Field, field_validator

extract = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)


def validate_inn(value: str) -> str:
    if not re.fullmatch(r"(?:[0-9]{10}|[0-9]{12})", value) or len(set(value)) == 1:
        raise ValueError("ИНН должен содержать 10 или 12 цифр и корректную контрольную сумму")
    digits = list(map(int, value))
    checks = (
        [(9, [2, 4, 10, 3, 5, 9, 4, 6, 8])]
        if len(value) == 10
        else [(10, [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]), (11, [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8])]
    )
    if any(
        sum(d * w for d, w in zip(digits[:i], weights, strict=True)) % 11 % 10 != digits[i]
        for i, weights in checks
    ):
        raise ValueError("Неверная контрольная сумма ИНН")
    return value


def domain_of(value: str) -> str | None:
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        try:
            ip_address(host)
            return None
        except ValueError:
            pass
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            return None
        if any(
            not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
            for label in host.split(".")
        ):
            return None
        parts = extract(host)
        return parts.top_domain_under_public_suffix or None
    except (ValueError, UnicodeError):
        return None


def clean_text(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def contains_inn(text: str, inn: str) -> bool:
    return re.search(rf"(?<!\d){re.escape(inn)}(?!\d)", text) is not None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Request(StrictModel):
    inn: str

    @field_validator("inn")
    @classmethod
    def check_inn(cls, value: str) -> str:
        return validate_inn(value)


class Hit(StrictModel):
    url: str
    title: str
    text: str


class Candidate(StrictModel):
    domain: str
    source_url: str


class Plan(StrictModel):
    candidates: list[Candidate] = Field(max_length=3)
    organization_name: str | None = Field(max_length=200)
    locality: str | None = Field(default=None, max_length=100)
    brand: str | None = Field(default=None, max_length=100)


class Verdict(StrictModel):
    domain: str | None
    is_official: bool
    is_primary: bool
    ambiguous: bool
    evidence_id: str | None
    relationship: Literal["owner", "customer", "partner", "directory", "unknown"]
    reason: str


class Result(StrictModel):
    domain: str | None
