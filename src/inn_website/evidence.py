import re
from urllib.parse import urlsplit

from inn_website.models import Hit, contains_inn, domain_of

DIRECTORIES = {
    "rusprofile.ru",
    "checko.ru",
    "list-org.com",
    "zachestnyibiznes.ru",
    "spark-interfax.ru",
    "fbc.ru",
    "vbankcenter.ru",
    "xfirm.ru",
    "companies.rbc.ru",
    "focus.kontur.ru",
    "kontragent.vbr.ru",
    "ev.economy.gov.ru",
    "org-info.com",
    "yell.ru",
    "companium.ru",
    "b2book.ru",
    "2gis.ru",
    "wikipedia.org",
    "startpack.ru",
    "platforms.su",
}
DIRECTORY_PATHS = {
    "tbank.ru": ("/business/contractor/",),
    "saby.ru": ("/profile/",),
    "sbis.ru": ("/contragents/",),
    "klerk.ru": ("/tool/ocompany/",),
    "rbc.ru": ("/companies/", "/id/"),
    "audit-it.ru": ("/contragent/",),
    "yandex.ru": ("/maps/", "/search/"),
}


def is_directory(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    domain = domain_of(url)
    if host in DIRECTORIES or domain in DIRECTORIES:
        return True
    return any(parsed.path.startswith(prefix) for prefix in DIRECTORY_PATHS.get(domain, ()))


def name_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold().replace("ё", "е"))


def merge_hits(*groups: list[Hit]) -> list[Hit]:
    merged = {}
    for group in groups:
        for hit in group:
            if hit.url not in merged or len(hit.text) > len(merged[hit.url].text):
                merged[hit.url] = hit
    return list(merged.values())


def context(hits: list[Hit], inn: str, budget: int) -> list[dict]:
    result = []
    for hit in hits:
        remaining = budget - len(hit.url) - min(len(hit.title), 160)
        if remaining < 100:
            continue
        length = min(1100, remaining)
        position = hit.text.find(inn)
        start = max(0, position - length // 2) if position >= 0 else 0
        text = hit.text[start : start + length]
        result.append({"url": hit.url, "title": hit.title[:160], "text": text})
        budget = remaining - len(text)
    return result


def verified_identity(plan, hits: list[Hit], inn: str) -> dict | None:
    name = plan.organization_name
    if not name or len(name_key(name)) < 3:
        return None
    for hit in hits:
        text = hit.title + " " + hit.text
        if contains_inn(text, inn) and name_key(name) in name_key(text):
            locality = plan.locality
            if not locality or not name_key(locality) or name_key(locality) not in name_key(text):
                locality = None
            identity = {"name": name, "locality": locality, "source_url": hit.url}
            if (
                plan.brand
                and len(name_key(plan.brand)) >= 3
                and any(
                    contains_inn(item.title + " " + item.text, inn)
                    and name_key(plan.brand) in name_key(item.title + " " + item.text)
                    for item in hits
                )
            ):
                identity["brand"] = plan.brand
            return identity
    return None
