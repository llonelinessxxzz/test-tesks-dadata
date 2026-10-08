import argparse
import json
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from inn_website.models import Request
from inn_website.pipeline import Pipeline
from inn_website.providers import LanguageModel, ProviderError, TavilySearch, Transport, settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Найти основной сайт организации по ИНН")
    parser.add_argument("inn", nargs="?", help="ИНН; без аргумента читается JSON из stdin")
    parser.add_argument("--trace", type=Path, help="Сохранить доказательства и причины решения")
    parser.add_argument("--explain", action="store_true", help="Объяснить результат в stderr")
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    try:
        request = (
            Request(inn=args.inn) if args.inn else Request.model_validate_json(sys.stdin.read())
        )
        config = settings()
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            transport = Transport(client)
            search = TavilySearch(transport, config["search_key"])
            pipeline = Pipeline(
                search,
                LanguageModel(transport, config["llm_key"], config["base_url"], config["model"]),
                search,
            )
            try:
                result = pipeline.run(request.inn)
            except ProviderError as error:
                pipeline.trace["error"] = str(error)
                raise
            finally:
                if args.trace:
                    args.trace.parent.mkdir(parents=True, exist_ok=True)
                    args.trace.write_text(
                        json.dumps(pipeline.trace, ensure_ascii=False, indent=2), "utf-8"
                    )
        print(result.model_dump_json())
        if args.explain:
            reasons = {
                "verified": "Сайт подтверждён реквизитами организации.",
                "verified_russian_version": "Выбрана российская версия подтверждённого сайта.",
                "empty_search": "Поисковый API не вернул результатов по этому ИНН.",
                "ambiguous": "Подтверждено несколько сайтов; основной не установлен.",
                "insufficient_evidence": "Найденных данных недостаточно для подтверждения сайта.",
            }
            print(reasons.get(pipeline.trace.get("reason"), "Результат сохранён."), file=sys.stderr)
            for verdict in pipeline.trace.get("verdicts", []):
                if verdict.get("accepted"):
                    print(verdict["source_url"], file=sys.stderr)
        return 0
    except (ValueError, ValidationError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (ProviderError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
