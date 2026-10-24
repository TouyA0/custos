import json
import os
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


def test_session_is_saved_and_can_be_resumed() -> None:
    config = ClaudeConfig()
    assert "--no-session-persistence" not in claude.build_command(config)
    assert "--resume" not in claude.build_command(config)
    cmd = claude.build_command(config, resume="abc-123")
    assert cmd[cmd.index("--resume") + 1] == "abc-123"
    assert cmd[cmd.index("--tools") + 1] == ""  # toujours aucun outil dans la bulle


def test_parse_session() -> None:
    init = json.dumps({"type": "system", "subtype": "init", "session_id": "abc-123"})
    assert claude.parse_session(init) == "abc-123"
    assert claude.parse_session(json.dumps({"type": "system", "subtype": "status"})) is None
    assert claude.parse_session("pas du json") is None


def test_agent_command_has_tools_in_auto_mode() -> None:
    cmd = claude.build_command(ClaudeConfig(), agent=True)
    assert "--tools" not in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "auto"
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "stdio"
    assert "Bash(sudo *)" in cmd and "Bash(pkexec *)" in cmd and "Read(~/.ssh/**)" in cmd
    # Les listes d'options s'arrêtent avant ce qui suit (le prompt n'est pas pris pour une règle).
    assert cmd[cmd.index("--disallowedTools") + len(claude.DISALLOWED_TOOLS) + 1].startswith("--")


def test_agent_runs_commands_in_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude, "sandbox_available", lambda: True)
    cmd = claude.build_command(ClaudeConfig(), agent=True)
    sandbox = json.loads(cmd[cmd.index("--settings") + 1])["sandbox"]
    assert sandbox["enabled"] and not sandbox["autoAllowBashIfSandboxed"]
    assert str(Path.home() / ".ssh") in sandbox["filesystem"]["denyRead"]
    assert sandbox["filesystem"]["allowWrite"] == [str(Path.home())]
    assert "xdg-open *" in sandbox["excludedCommands"]


def test_no_sandbox_when_disabled_or_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude, "sandbox_available", lambda: True)
    assert "--settings" not in claude.build_command(ClaudeConfig(sandbox=False), agent=True)
    assert "--settings" not in claude.build_command(ClaudeConfig(), agent=False)
    monkeypatch.setattr(claude, "sandbox_available", lambda: False)
    assert "--settings" not in claude.build_command(ClaudeConfig(), agent=True)


def test_describe_tool_steps() -> None:
    home = str(Path.home())
    assert claude.describe_tool("Bash", {"command": "journalctl -b -1\n| tail"}) == "$ journalctl -b -1"
    assert claude.describe_tool("Read", {"file_path": f"{home}/.config/x"}) == "lit ~/.config/x"
    assert claude.describe_tool("WebSearch", {"query": "CEC anges"}) == "cherche sur le web : CEC anges"
    assert claude.describe_tool("Inconnu", {}) == "Inconnu"


def test_parse_tools_from_assistant_message() -> None:
    line = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Je regarde."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "uptime"}},
    ]}})
    assert claude.parse_tools(line) == ["$ uptime"]
    assert claude.parse_tools(json.dumps({"type": "user"})) == []


def test_permission_request_and_responses() -> None:
    line = json.dumps({"type": "control_request", "request_id": "r1", "request": {
        "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "rm -rf ~/x"}}})
    request_id, permission = claude.parse_permission(line)
    assert request_id == "r1" and permission.description == "rm -rf ~/x"

    allow = claude.permission_response("r1", permission, claude.ALWAYS)["response"]
    assert allow["request_id"] == "r1"
    assert allow["response"] == {"behavior": "allow", "updatedInput": {"command": "rm -rf ~/x"}}
    deny = claude.permission_response("r1", permission, claude.DENY)["response"]["response"]
    assert deny["behavior"] == "deny"
    assert claude.parse_permission(json.dumps({"type": "control_request", "request": {}})) is None


FAKE_CLAUDE = """\
#!/usr/bin/env python3
import json, sys
sys.stdin.readline()
sys.stderr.write("x" * 300_000)  # bien plus qu'un tube (64 Ko)
sys.stderr.flush()
delta = {"type": "text_delta", "text": "Bonjour"}
print(json.dumps({"type": "stream_event", "event": {"delta": delta}}), flush=True)
print(json.dumps({"type": "result", "is_error": False}), flush=True)
sys.stdin.read()
"""


def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    program = tmp_path / "bin" / "claude"
    program.parent.mkdir()
    program.write_text(script)
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{program.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def test_verbose_stderr_does_not_block_the_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_claude(tmp_path, monkeypatch, FAKE_CLAUDE)
    answer = "".join(claude.ask("salut", ClaudeConfig(timeout=5)))
    assert answer == "Bonjour"


def test_failure_shows_end_of_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = (
        "#!/usr/bin/env python3\nimport sys\n"
        "sys.stderr.write('x' * 300_000 + 'pas connecté')\nsys.exit(1)\n"
    )
    fake_claude(tmp_path, monkeypatch, script)
    with pytest.raises(claude.ClaudeError) as error:
        list(claude.ask("salut", ClaudeConfig(timeout=5)))
    assert str(error.value).endswith("pas connecté")
    assert len(str(error.value)) <= claude.MAX_ERROR
