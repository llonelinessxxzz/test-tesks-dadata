import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from inn_website.pipeline import Pipeline
from inn_website.providers import LanguageModel, ProviderError, TavilySearch, Transport, settings


def evaluate_case(case: dict, domain: str | None) -> str:
    if "domains" in case:
        return "match" if domain in case["domains"] else "miss" if domain is None else "wrong"
    return "wrong" if domain in case["forbidden_domains"] else "not_confused"


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка поиска на размеченных примерах")
    parser.add_argument("cases", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    config = settings()
    cases = json.loads(args.cases.read_text("utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(UTC).isoformat(), "model": config["model"], "cases": []}
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        transport = Transport(client)
        search = TavilySearch(transport, config["search_key"])
        llm = LanguageModel(transport, config["llm_key"], config["base_url"], config["model"])
        for case in cases:
            pipeline = Pipeline(search, llm, search)
            started = time.monotonic()
            entry = {"inn": case["inn"], "name": case["name"]}
            try:
                domain = pipeline.run(case["inn"]).domain
                entry.update(domain=domain, status=evaluate_case(case, domain))
            except (ProviderError, ValueError) as error:
                entry.update(status="error", error=str(error))
                pipeline.trace["error"] = str(error)
            entry["seconds"] = round(time.monotonic() - started, 2)
            trace_path = args.output.parent / f"{args.output.stem}-{case['inn']}.json"
            trace_path.write_text(json.dumps(pipeline.trace, ensure_ascii=False, indent=2), "utf-8")
            entry["trace"] = trace_path.name
            report["cases"].append(entry)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
            print(json.dumps(entry, ensure_ascii=False), file=sys.stderr, flush=True)
    summary = {
        "total": len(cases),
        "known_sites": sum("domains" in case for case in cases),
        "matched": sum(r["status"] == "match" for r in report["cases"]),
        "missed": sum(r["status"] == "miss" for r in report["cases"]),
        "wrong": sum(r["status"] == "wrong" for r in report["cases"]),
        "not_confused": sum(r["status"] == "not_confused" for r in report["cases"]),
        "errors": sum(r["status"] == "error" for r in report["cases"]),
    }
    report["summary"] = summary
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summary))
    return int(any(r["status"] in {"miss", "wrong", "error"} for r in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
