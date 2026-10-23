import json
from pathlib import Path

import pytest

from custos import claude
from custos.config import ClaudeConfig


def test_command_uses_configured_model_and_no_tools() -> None:
    cmd = claude.build_command(ClaudeConfig(model="claude-opus-5-5"))
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert cmd[cmd.index("--tools") + 1] == ""


def test_message_puts_images_before_text(tmp_path: Path) -> None:
    image = tmp_path / "capture.png"
    image.write_bytes(b"\x89PNG")
    content = claude.build_message("c'est quoi ?", [image])["message"]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[1] == {"type": "text", "text": "c'est quoi ?"}


def test_message_rejects_unknown_image_format(tmp_path: Path) -> None:
    with pytest.raises(claude.ClaudeError):
        claude.build_message("?", [tmp_path / "capture.bmp"])


def test_parse_text_delta() -> None:
    line = json.dumps(
        {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "Salut"}}}
    )
    assert claude.parse_line(line) == "Salut"


def test_parse_ignores_other_events() -> None:
    assert claude.parse_line(json.dumps({"type": "system", "subtype": "init"})) is None
    assert claude.parse_line("pas du json") is None


def test_parse_error_result_raises() -> None:
    line = json.dumps({"type": "result", "is_error": True, "result": "Not logged in"})
    with pytest.raises(claude.ClaudeError, match="Not logged in"):
        claude.parse_line(line)
