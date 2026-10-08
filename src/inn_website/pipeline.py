import re
from typing import Protocol, TypeVar

from pydantic import BaseModel

from inn_website.evidence import context, is_directory, merge_hits, name_key, verified_identity
from inn_website.models import Hit, Plan, Result, Verdict, contains_inn, domain_of, validate_inn

T = TypeVar("T", bound=BaseModel)


class Search(Protocol):
    def search(self, query: str) -> list[Hit]: ...


class Extractor(Protocol):
    def extract(self, urls: list[str]) -> list[Hit]: ...


class LLM(Protocol):
    def structured(self, instructions: str, payload: dict, schema: type[T]) -> T: ...


class Pipeline:
    def __init__(self, search: Search, llm: LLM, extractor: Extractor | None = None):
        self.search = search
        self.llm = llm
        self.extractor = extractor
        self.trace: dict = {}
        self.checked: set[str] = set()

    def run(self, inn: str) -> Result:
        validate_inn(inn)
        self.checked = set()
        self.trace = {"inn": inn, "searches": [], "extractions": [], "verdicts": []}
        discovery = self._search(f'"{inn}" ИНН')
        if not discovery:
            discovery = self._search(f"{inn} реквизиты")
        if not discovery:
            return self._finish(None, "empty_search")
        plan = self._plan(inn, discovery)
        self.trace["plan"] = plan.model_dump()
        identity = verified_identity(plan, discovery, inn)
        self.trace["identity"] = identity
        accepted = self._verify_candidates(inn, plan, discovery, identity)
        if not accepted and identity:
            terms = [identity["name"], identity["locality"] or "", "официальный сайт"]
            short_name = re.sub(
                r"^(общество с ограниченной ответственностью|публичное акционерное общество|"
                r"акционерное общество|закрытое акционерное общество|открытое акционерное общество|"
                r"ООО|ПАО|ОАО|ЗАО|АО|ИП)\s+",
                "",
                identity["name"],
                flags=re.IGNORECASE,
            ).strip(' «»"')
            queries = list(
                dict.fromkeys(
                    [
                        " ".join(term for term in terms if term),
                        f"{short_name} официальный сайт",
                    ]
                )
            )
            if identity.get("brand"):
                queries.append(f"{identity['brand']} официальный сайт")
            for query in queries:
                expanded = self._search(query)
                if not expanded:
                    continue
                plan = self._plan(inn, expanded, identity)
                self.trace.setdefault("expanded_plans", []).append(plan.model_dump())
                brand = plan.brand
                if (
                    brand
                    and len(name_key(brand)) >= 3
                    and len(queries) < 3
                    and any(
                        name_key(brand) in name_key(hit.title + " " + hit.text)
                        and name_key(short_name) in name_key(hit.title + " " + hit.text)
                        for hit in expanded
                    )
                ):
                    brand_query = f"{brand} официальный сайт"
                    if brand_query not in queries:
                        queries.append(brand_query)
                discovery = merge_hits(discovery, expanded)
                accepted = self._verify_candidates(inn, plan, discovery, identity)
                if accepted:
                    break
        if len(accepted) == 1:
            return self._finish(accepted[0], "verified")
        if len(accepted) > 1:
            labels = {domain.rsplit(".", 1)[0] for domain in accepted}
            russian = [domain for domain in accepted if domain.endswith((".ru", ".xn--p1ai"))]
            if len(labels) == 1 and len(russian) == 1:
                self.trace["alternatives"] = accepted
                return self._finish(russian[0], "verified_russian_version")
        return self._finish(None, "ambiguous" if accepted else "insufficient_evidence")

    def _verify_candidates(self, inn: str, plan: Plan, discovery: list[Hit], identity) -> list[str]:
        accepted = []
        for domain in self._candidates(plan, discovery, identity):
            if domain in self.checked or len(self.checked) >= 5:
                continue
            self.checked.add(domain)
            evidence = self._search(f'site:{domain} "{inn}"')
            evidence = merge_hits(evidence, [h for h in discovery if domain_of(h.url) == domain])
            evidence = [
                h for h in evidence if domain_of(h.url) == domain and not is_directory(h.url)
            ]
            eligible = [h for h in evidence if contains_inn(h.text, inn)]
            if not eligible and self.extractor and evidence:
                urls = [h.url for h in evidence[:3]]
                extracted = self.extractor.extract(urls)
                self.trace["extractions"].append(
                    {"urls": urls, "hits": [h.model_dump() for h in extracted]}
                )
                eligible = [
                    h
                    for h in extracted
                    if domain_of(h.url) == domain
                    and not is_directory(h.url)
                    and contains_inn(h.text, inn)
                ]
            if not eligible:
                self.trace["verdicts"].append({"domain": domain, "rejected": "no_exact_inn"})
                continue
            excerpts = context(eligible, inn, 3500)
            evidence_by_id = {f"e{i}": item for i, item in enumerate(excerpts, start=1)}
            verdict = self.llm.structured(
                "Проверь принадлежность основного сайта организации с указанным ИНН. "
                "URL каждого evidence уже проверен кодом: он находится на домене candidate "
                "или его поддомене. Корпоративный и региональный поддомены — части этого сайта. "
                "Реквизиты самой организации или её договор на таком URL с точным ИНН — "
                "достаточное подтверждение; отдельная фраза о владении доменом не нужна. "
                "verified_identity — установленное по исходным источникам название и город. "
                "Одноимённая организация, другое юрлицо группы, клиент, партнёр, поставщик, "
                "банк в платёжных реквизитах чужой организации или карточка справочника "
                "НЕ являются владельцем. Собственные платёжные реквизиты владельца подходят. "
                "relationship=owner только когда фрагмент прямо связывает ИНН с владельцем, "
                "оператором или продавцом данного сайта в его реквизитах, контактах, оферте, "
                "политике либо договоре услуг. Новость об организации или её дочерних компаниях "
                "сама по себе недостаточна. "
                "is_primary=true для основного корпоративного сайта или основной версии сайта "
                "для одной из стран/языков; false для отдельного продукта, промо или партнёра. "
                "При недостатке доказательств domain=null; при конфликте ambiguous=true. "
                "В evidence_id верни идентификатор подходящего фрагмента из evidence. "
                "domain должен точно совпадать с candidate. reason напиши кратко по-русски.",
                {
                    "inn": inn,
                    "candidate": domain,
                    "verified_identity": identity,
                    "evidence": evidence_by_id,
                },
                Verdict,
            )
            source = evidence_by_id.get(verdict.evidence_id)
            valid = (
                verdict.domain == domain
                and verdict.is_official
                and verdict.is_primary
                and verdict.relationship == "owner"
                and not verdict.ambiguous
                and source is not None
                and contains_inn(source["text"], inn)
            )
            self.trace["verdicts"].append(
                {
                    **verdict.model_dump(),
                    "candidate": domain,
                    "accepted": valid,
                    "source_url": source["url"] if source else None,
                    "quote": source["text"] if source else None,
                }
            )
            if valid:
                accepted.append(domain)
        return accepted

    def _plan(self, inn: str, hits: list[Hit], identity: dict | None = None) -> Plan:
        relevant = [h for h in hits if contains_inn(h.title + " " + h.text, inn)]
        relevant.sort(key=lambda h: not bool(re.search(r"[А-Яа-яЁё]", h.title)))
        ordered = sorted(hits, key=lambda h: (is_directory(h.url), h not in relevant))
        return self.llm.structured(
            "Составь план поиска сайта юридического лица или ИП по ИНН. "
            "Сначала установи organization_name и locality ТОЛЬКО по identity_evidence: "
            "полное или краткое название с ООО/ПАО/ИП, и город. Скопируй их из источника. "
            "Если есть русское название, используй его, а не английскую транслитерацию. "
            "В brand укажи потребительский бренд, если источник явно связывает его с этой "
            "организацией: например, название сети или сервиса, отличающееся от юрлица. "
            "Скопируй бренд из текста; иначе brand=null. "
            "Если передана verified_identity, используй её. Не заменяй ООО на одноимённый банк. "
            "Нет подтверждённого названия — organization_name=null, locality=null. "
            "Выбери до трёх потенциальных основных сайтов именно этой организации. "
            "Справочники и карточки чужих организаций, соцсети, агрегаторы не подходят. "
            "source_url скопируй из hits, domain — домен этой ссылки. Разрешён домен, "
            "прямо указанный в тексте source_url как сайт организации. Не выдумывай домены. "
            "Кандидату пока не обязательно иметь ИНН в тексте: последует проверка реквизитов. "
            "Если принадлежность ничем не подкреплена, candidates=[].",
            {
                "inn": inn,
                "verified_identity": identity,
                "identity_evidence": context(relevant, inn, 2000) if identity is None else [],
                "hits": context(ordered, inn, 4500),
            },
            Plan,
        )

    @staticmethod
    def _candidates(plan: Plan, hits: list[Hit], identity: dict | None = None) -> list[str]:
        by_url = {hit.url: hit for hit in hits}
        candidates = []
        for candidate in plan.candidates:
            domain = domain_of(candidate.domain)
            source = by_url.get(candidate.source_url)
            if not domain or not source or domain in candidates:
                continue
            same_site = domain_of(source.url) == domain and not is_directory(source.url)
            explicit_link = re.search(
                rf"(?<![\w.-])(?:https?://)?(?:www\.)?{re.escape(domain)}(?![\w.-])",
                source.text,
                re.IGNORECASE,
            )
            if same_site or explicit_link:
                candidates.append(domain)
        if not candidates and identity:
            for hit in hits:
                domain = domain_of(hit.url)
                if (
                    domain
                    and domain not in candidates
                    and not is_directory(hit.url)
                    and name_key(identity["name"]) in name_key(hit.title + " " + hit.text)
                ):
                    candidates.append(domain)
        return candidates[:3]

    def _search(self, query: str) -> list[Hit]:
        hits = self.search.search(query)
        self.trace["searches"].append({"query": query, "hits": [h.model_dump() for h in hits]})
        return hits

    def _finish(self, domain: str | None, reason: str) -> Result:
        self.trace["reason"] = reason
        self.trace["result"] = {"domain": domain}
        return Result(domain=domain)
