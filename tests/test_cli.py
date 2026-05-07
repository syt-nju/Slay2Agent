from __future__ import annotations

import pytest

from slay2agent.cli import _mask, build_parser, main


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_TIMEOUT",
        "STS2MCP_BASE_URL",
        "STS2MCP_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_mask_unset() -> None:
    assert _mask(None) == "<unset>"
    assert _mask("") == "<unset>"


def test_mask_short_token_replaced() -> None:
    assert _mask("short") == "***"


def test_mask_long_token_keeps_prefix_suffix() -> None:
    masked = _mask("sk-or-1234567890abcdef")
    assert masked.startswith("sk-o")
    assert masked.endswith("cdef")
    assert "…" in masked


def test_parser_requires_subcommand() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_known_subcommands() -> None:
    parser = build_parser()
    for sub in ("config", "smoke", "inspect", "run"):
        args = parser.parse_args([sub])
        assert args.command == sub


def test_config_subcommand_prints_masked_values(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-1234567890abcdef")
    rc = main(["config"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "sk-or-1234567890abcdef" not in out
    assert "127.0.0.1:15526" in out  # default STS2MCP base url
    assert "model" in out


def test_inspect_reports_game_unreachable(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point at an unused localhost port so the connection fails fast.
    monkeypatch.setenv("STS2MCP_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("STS2MCP_TIMEOUT", "0.5")
    rc = main(["inspect"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "inspect failed" in err


def test_run_is_stub(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["run"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "F-005" in err
