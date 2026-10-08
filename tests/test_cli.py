import io
import json
from unittest.mock import Mock

from inn_website.cli import main
from inn_website.models import Result


def test_cli_json_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["inn-website"])
    monkeypatch.setattr("sys.stdin", io.StringIO('{"inn":"7721581040"}'))
    monkeypatch.setattr(
        "inn_website.cli.settings",
        lambda: {
            "search_key": "test",
            "llm_key": "test",
            "base_url": "https://example.com",
            "model": "test",
        },
    )
    pipeline = Mock()
    pipeline.run.return_value = Result(domain="dadata.ru")
    monkeypatch.setattr("inn_website.cli.Pipeline", lambda *args: pipeline)
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {"domain": "dadata.ru"}


def test_invalid_input_has_no_success_json(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["inn-website", "123"])
    assert main() == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "error" in json.loads(output.err)
