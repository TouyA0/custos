import threading
from pathlib import Path

import pytest

from custos import claude, cli, context, daemon
from custos.config import Config
from custos.context import Context
from custos.context.hyprland import ActiveWindow
from custos.states import MascotState

WINDOW = ActiveWindow("foot", "~", 0, 0, 100, 100)


class FakeUI:
    def __init__(self) -> None:
        self.states: list[MascotState] = []
        self.answers: list[str] = []
        self.errors: list[str] = []

    def set_state(self, state: MascotState) -> None:
        self.states.append(state)

    def show_answer(self, text: str) -> None:
        self.answers.append(text)

    def show_error(self, text: str) -> None:
        self.errors.append(text)


@pytest.fixture
def ui() -> FakeUI:
    return FakeUI()


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list | None]]:
    """Remplace Claude : renvoie toujours « Bonjour ! » et note les prompts reçus."""
    sent: list[tuple[str, list | None]] = []

    def fake_ask(prompt, config, images=None):
        sent.append((prompt, images))
        yield "Bon"
        yield "jour ! "

    monkeypatch.setattr(claude, "ask", fake_ask)
    return sent


def use_context(monkeypatch: pytest.MonkeyPatch, ctx: Context) -> None:
    monkeypatch.setattr(context, "collect", lambda *a, **k: ctx)


def test_ask_shows_answer(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    daemon.Daemon(Config(), ui).handle({"type": "ask", "question": "salut ?"})
    assert ui.states == [MascotState.THINKING]
    assert ui.answers == ["Bonjour !"]
    assert prompts[0][0].endswith("Question : salut ?")


def test_ask_with_screenshot_sends_image_then_deletes_it(monkeypatch, ui, prompts, tmp_path) -> None:
    shot = tmp_path / "custos.png"
    shot.write_bytes(b"png")
    use_context(monkeypatch, Context(window=WINDOW, screenshot=shot))
    daemon.Daemon(Config(), ui).handle({"type": "ask", "screen": True})
    assert prompts[0][1] == [shot]
    assert not shot.exists()


def test_blocked_window_never_calls_claude(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW, blocked=True))
    daemon.Daemon(Config(), ui).handle({"type": "ask", "question": "salut ?"})
    assert prompts == []
    assert "protégée" in ui.errors[0]


def test_nothing_to_ask(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    daemon.Daemon(Config(), ui).handle({"type": "ask"})
    assert prompts == []
    assert ui.errors


def test_claude_error_is_shown(monkeypatch, ui) -> None:
    def failing_ask(*a, **k):
        raise claude.ClaudeError("Not logged in")
        yield  # pragma: no cover

    monkeypatch.setattr(claude, "ask", failing_ask)
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "question": "?"})
    assert ui.errors == ["Not logged in"]
    # Le verrou est bien relâché après une erreur.
    d.handle({"type": "ask", "question": "?"})
    assert len(ui.errors) == 2


def test_second_question_while_busy_is_refused(monkeypatch, ui) -> None:
    started, release = threading.Event(), threading.Event()

    def slow_ask(*a, **k):
        started.set()
        release.wait(2)
        yield "fini"

    monkeypatch.setattr(claude, "ask", slow_ask)
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    first = threading.Thread(target=d.handle, args=({"type": "ask", "question": "1"},))
    first.start()
    started.wait(2)
    d.handle({"type": "ask", "question": "2"})
    release.set()
    first.join(2)
    assert ui.answers == ["fini"]
    assert "déjà" in ui.errors[0]


def test_failed_command_worries_without_calling_claude(ui, prompts) -> None:
    d = daemon.Daemon(Config(), ui)
    event = {"type": "shell-event", "exit_code": 127, "command": "pacmna -Syu", "cwd": "/"}
    d.handle(event)
    assert ui.states == [MascotState.WORRIED]
    assert d.last_failure == event
    assert prompts == []


def test_successful_command_is_ignored(ui) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "shell-event", "exit_code": 0, "command": "ls"})
    assert ui.states == []
    assert d.last_failure is None


def test_shell_reaction_can_be_disabled(ui) -> None:
    config = Config()
    config.shell.react_on_failure = False
    daemon.Daemon(config, ui).handle({"type": "shell-event", "exit_code": 1})
    assert ui.states == []


def test_cli_without_daemon(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    notified: list[str] = []
    monkeypatch.setattr("custos.ui.notify.notify", lambda title, body: notified.append(body))

    assert cli.main(["shell-event", "--exit-code", "1"]) == 0  # silencieux
    assert cli.main(["ask", "salut"]) == 1
    assert "custos daemon" in capsys.readouterr().out
    assert len(notified) == 1
