"""请求解析与 CLI 冒烟测试。"""

from __future__ import annotations

import json

from src.cli import main
from src.domain import parsing


def test_parse_date_and_time() -> None:
    result = parsing.parse_request("把 M01 排到 2026-11-02 18:00")
    assert result["date"] == "2026-11-02"
    assert result["start"] == "18:00"
    assert result["material_id"] == "M01"


def test_parse_confirmed_intent() -> None:
    assert parsing.infer_confirmed("我确认用 19:00") is True
    assert parsing.infer_confirmed("先看看能不能排") is False
    assert parsing.infer_confirmed("排一下") is None


def test_parse_request_returns_none_when_absent() -> None:
    result = parsing.parse_request("帮我排一下这个素材")
    assert result["date"] is None
    assert result["start"] is None
    assert result["material_id"] is None


def test_cli_single_request(tmp_path, capsys) -> None:
    code = main([
        "--material", "M01",
        "--request", "把 M01 排到 2026-11-02 18:00",
        "--no-confirm",
        "--json",
        "--db", str(tmp_path / "d.sqlite3"),
    ])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "needs_confirmation"
    assert out["published"] is False


def test_cli_case_runs(tmp_path, capsys) -> None:
    code = main(["--case", "normal_ok", "--json", "--db", str(tmp_path / "d.sqlite3")])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["case"] == "normal_ok"
    assert out["final"]["outcome"] == "needs_confirmation"


def test_cli_list_drafts(tmp_path, capsys) -> None:
    db = str(tmp_path / "d.sqlite3")
    main(["--material", "M01", "--request", "把 M01 排到 2026-11-02 19:00", "--confirm", "--db", db])
    capsys.readouterr()
    code = main(["--list-drafts", "--json", "--db", db])
    assert code == 0
    drafts = json.loads(capsys.readouterr().out)
    assert len(drafts) == 1
    assert drafts[0]["status"] == "draft"


def test_cli_rejects_missing_request() -> None:
    try:
        main(["--material", "M01"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("应当因缺少 --request 而退出")
