import threading
from pathlib import Path

import pytest

from custos import claude, cli, context, daemon, watch
from custos.config import Config
from custos.context import Context
from custos.context.hyprland import ActiveWindow
from custos.states import MascotState

WINDOW = ActiveWindow("foot", "~", 0, 0, 100, 100)


class FakeUI:
    def __init__(self) -> None:
        self.states: list[MascotState] = []
        self.prompts: list[tuple[str, str]] = []
        self.questions: list[str] = []
        self.chunks: list[str] = []
        self.answers: list[str] = []
        self.errors: list[str] = []
        self.notices: list[str | None] = []
        self.hidden: list[bool] = []
        self.attached: list = []
        self.toggles = 0
        self.reopened: list[tuple[str, str, str]] = []

    def set_state(self, state: MascotState) -> None:
        self.states.append(state)

    def open_prompt(self, summary: str, hint: str) -> None:
        self.prompts.append((summary, hint))

    def start_answer(self, question: str) -> None:
        self.questions.append(question)

    def append_answer(self, text: str) -> None:
        self.chunks.append(text)

    def end_answer(self, answer: str) -> None:
        self.answers.append(answer)

    def show_error(self, text: str) -> None:
        self.errors.append(text)

    def show_notice(self, kind: str | None) -> None:
        self.notices.append(kind)

    def set_hidden(self, hidden: bool) -> None:
        self.hidden.append(hidden)

    def attach(self, window) -> None:
        self.attached.append(window)

    def detach(self) -> None:
        self.attached.append(None)

    def toggle_stowed(self) -> None:
        self.toggles += 1

    def reopen(self, summary: str, question: str, answer: str) -> None:
        self.reopened.append((summary, question, answer))


@pytest.fixture
def ui() -> FakeUI:
    return FakeUI()


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list | None]]:
    """Remplace Claude : renvoie toujours « Bonjour ! » et note les prompts reçus."""
    sent: list[tuple[str, list | None]] = []

    def fake_ask(prompt, config, images=None, resume=None, on_session=None, cancel=None):
        sent.append((prompt, images))
        if on_session:
            on_session("session-1")
        yield "Bon"
        yield "jour ! "

    monkeypatch.setattr(claude, "ask", fake_ask)
    return sent


def use_context(monkeypatch: pytest.MonkeyPatch, ctx: Context) -> None:
    monkeypatch.setattr(context, "collect", lambda *a, **k: ctx)


def test_question_from_cli_streams_answer(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    daemon.Daemon(Config(), ui).handle({"type": "ask", "question": "salut ?"})
    assert ui.questions == ["salut ?"]
    assert ui.chunks == ["Bon", "jour ! "]
    assert ui.answers == ["Bonjour !"]
    assert ui.states == [MascotState.THINKING, MascotState.TALKING]
    assert prompts[0][0].endswith("Question : salut ?")


def test_shortcut_without_question_opens_prompt(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW, selection="ls -la"))
    daemon.Daemon(Config(), ui).handle({"type": "ask", "selection": True})
    assert ui.prompts == [("sélection : 6 car.", "Entrée : aide sur la sélection")]
    assert prompts == []


def test_empty_question_uses_default_for_selection(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW, selection="ls -la"))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "selection": True})
    d.answer("  ")
    assert ui.questions == [context.DEFAULT_QUESTION]
    assert "ls -la" in prompts[0][0]


def test_empty_question_without_context_is_refused(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("")
    assert prompts == []
    assert ui.errors == ["Écris ta question."]


def test_follow_up_resumes_claude_session(monkeypatch, ui) -> None:
    calls = []

    def fake_ask(prompt, config, images=None, resume=None, on_session=None, cancel=None):
        calls.append((prompt, resume))
        on_session("session-1")
        yield "ok"

    monkeypatch.setattr(claude, "ask", fake_ask)
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("première ?")
    d.answer("et ensuite ?")
    assert calls[0][1] is None and "Fenêtre active" in calls[0][0]  # contexte à la 1re
    assert calls[1] == ("et ensuite ?", "session-1")  # puis juste la question, en reprise


def test_new_shortcut_starts_new_conversation(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "question": "un"})
    use_context(monkeypatch, Context(window=WINDOW))
    d.handle({"type": "ask", "question": "deux"})
    assert "Fenêtre active" in prompts[1][0]  # nouvelle session : le contexte est renvoyé


def test_screenshot_sent_once_then_deleted(monkeypatch, ui, prompts, tmp_path) -> None:
    shot = tmp_path / "custos.png"
    shot.write_bytes(b"png")
    use_context(monkeypatch, Context(window=WINDOW, screenshot=shot))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "screen": True})
    d.answer("")
    d.answer("et là ?")
    assert prompts[0][1] == [shot]
    assert prompts[1][1] is None
    assert not shot.exists()


def test_closing_deletes_unused_screenshot(monkeypatch, ui, prompts, tmp_path) -> None:
    shot = tmp_path / "custos.png"
    shot.write_bytes(b"png")
    use_context(monkeypatch, Context(window=WINDOW, screenshot=shot))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "screen": True})
    d.close_session()  # bulle fermée sans rien demander
    assert not shot.exists()
    assert prompts == []


def test_blocked_window_never_calls_claude(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW, blocked=True))
    daemon.Daemon(Config(), ui).handle({"type": "ask", "question": "salut ?"})
    assert prompts == []
    assert ui.prompts == []
    assert "protégée" in ui.errors[0]


def test_claude_error_is_shown_and_lock_released(monkeypatch, ui) -> None:
    def failing_ask(*a, **k):
        raise claude.ClaudeError("Not logged in")
        yield  # pragma: no cover

    monkeypatch.setattr(claude, "ask", failing_ask)
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "question": "?"})
    assert ui.errors == ["Not logged in"]
    assert ui.states[-1] is MascotState.IDLE
    d.answer("?")
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
    d.answer("3")
    release.set()
    first.join(2)
    assert ui.answers == ["fini"]
    assert len(ui.errors) == 2
    assert all("déjà" in e for e in ui.errors)


def test_submit_answers_in_background(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    done = threading.Event()
    original = ui.end_answer
    monkeypatch.setattr(ui, "end_answer", lambda a: (original(a), done.set()))
    d.submit("salut ?")
    assert done.wait(2)
    assert ui.answers == ["Bonjour !"]


def test_failed_command_worries_without_calling_claude(ui, prompts) -> None:
    d = daemon.Daemon(Config(), ui)
    event = {"type": "shell-event", "exit_code": 127, "command": "pacmna -Syu", "cwd": "/"}
    d.handle(event)
    assert ui.states == [MascotState.WORRIED]
    assert d.last_failure == event
    assert prompts == []


@pytest.mark.parametrize("exit_code", [130, 148])
def test_interrupted_command_is_not_a_failure(ui, exit_code) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "shell-event", "exit_code": exit_code, "command": "sleep 100"})
    assert ui.states == []
    assert d.last_failure is None


@pytest.fixture
def collected(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Remplace la collecte du contexte et note ses arguments."""
    calls: list[dict] = []

    def fake_collect(
        privacy,
        selection=False,
        screenshot=False,
        failure=None,
        image=None,
        image_kind="window",
        window=None,
        attached=False,
    ):
        calls.append(
            {"selection": selection, "screenshot": screenshot, "failure": failure, "image": image}
        )
        if window:
            calls[-1]["window"] = window
        return Context(
            window=window or WINDOW,
            attached=attached,
            failure=failure,
            selection="du texte" if selection else "",
            screenshot=image,
            image_kind=image_kind,
        )

    monkeypatch.setattr(context, "collect", fake_collect)
    return calls


FAILURE = {"type": "shell-event", "exit_code": 127, "command": "pacmna -Syu", "cwd": "/home/q"}


def test_click_after_failure_offers_to_explain(ui, prompts, collected) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(FAILURE)
    d.open_from_mascot()
    assert collected == [{"selection": False, "screenshot": True, "failure": FAILURE, "image": None}]
    assert ui.prompts == [("commande en échec : pacmna -Syu", "Entrée : pourquoi ça a échoué ?")]

    d.answer("")  # Entrée sans rien écrire
    prompt = prompts[0][0]
    assert "code de sortie 127, dossier /home/q" in prompt
    assert "```\npacmna -Syu\n```" in prompt
    assert prompt.endswith(f"Question : {context.FAILURE_QUESTION}")


def test_failure_is_offered_only_once(ui, collected) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(FAILURE)
    d.open_from_mascot()
    d.open_from_mascot()
    assert [c["failure"] for c in collected] == [FAILURE, None]
    assert collected[1]["screenshot"] is False


def test_old_failure_is_forgotten(monkeypatch, ui, collected) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(FAILURE)
    later = daemon.time.monotonic() + daemon.RECENT_FAILURE + 1
    monkeypatch.setattr(daemon.time, "monotonic", lambda: later)
    d.open_from_mascot()
    assert collected[0]["failure"] is None
    assert ui.prompts[0][1] == "Demande à Custos…"


def test_failure_summary_hides_secrets(ui, collected) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle({**FAILURE, "command": "curl --token abcdef123456 x"})
    d.open_from_mascot()
    assert "abcdef123456" not in ui.prompts[0][0]


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fenêtre active et sélection simulées pour les événements de surveillance."""
    state = {"window": WINDOW, "selection": "une phrase sélectionnée"}
    monkeypatch.setattr(daemon.hyprland, "active_window", lambda: state["window"])
    monkeypatch.setattr(daemon.clipboard, "selection", lambda: state["selection"])
    return state


def test_selection_lights_badge_then_click_uses_it(ui, collected, desktop) -> None:
    d = daemon.Daemon(Config(), ui)
    d.on_watch_event(watch.SELECTION)
    assert ui.notices == [watch.SELECTION]
    d.open_from_mascot()
    assert ui.notices == [watch.SELECTION, None]  # badge éteint au clic
    assert collected[0]["selection"] is True
    assert ui.prompts[0][1] == "Entrée : aide sur la sélection"


def test_same_or_tiny_selection_is_ignored(ui, desktop) -> None:
    d = daemon.Daemon(Config(), ui)
    d.on_watch_event(watch.SELECTION)
    d.on_watch_event(watch.SELECTION)  # même texte
    desktop["selection"] = " a "
    d.on_watch_event(watch.SELECTION)
    assert ui.notices == [watch.SELECTION]


def test_nothing_noticed_in_blocked_window(ui, desktop) -> None:
    config = Config()
    config.privacy.blocked_classes = ["foot"]
    d = daemon.Daemon(config, ui)
    d.on_watch_event(watch.SELECTION)
    d.on_watch_event(watch.CLIPBOARD_IMAGE)
    assert ui.notices == []


def test_nothing_noticed_while_bubble_is_open(monkeypatch, ui, collected, desktop) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})  # bulle ouverte
    d.on_watch_event(watch.SELECTION)
    assert ui.notices == []
    d.close_session()
    d.on_watch_event(watch.SELECTION)
    assert ui.notices == [watch.SELECTION]


def test_old_notice_is_ignored(monkeypatch, ui, collected, desktop) -> None:
    d = daemon.Daemon(Config(), ui)
    d.on_watch_event(watch.SELECTION)
    later = daemon.time.monotonic() + Config().notice.duration + 1
    monkeypatch.setattr(daemon.time, "monotonic", lambda: later)
    d.open_from_mascot()
    assert collected[0]["selection"] is False


def test_caelestia_screenshot_is_copied_not_moved(ui, collected, desktop, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    original = tmp_path / "caelestia-picker-1.png"
    original.write_bytes(b"png")
    d = daemon.Daemon(Config(), ui)
    d.on_watch_event(watch.SCREENSHOT, str(original))
    d.open_from_mascot()
    sent = collected[0]["image"]
    assert sent != original and sent.read_bytes() == b"png"
    assert ui.prompts[0][0] == "capture d'écran"
    d.close_session()
    assert original.exists() and not sent.exists()


def test_clipboard_image(ui, collected, desktop, tmp_path, monkeypatch) -> None:
    image = tmp_path / "custos-x.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(daemon.clipboard, "image_to_file", lambda: image)
    d = daemon.Daemon(Config(), ui)
    d.on_watch_event(watch.CLIPBOARD_IMAGE)
    d.open_from_mascot()
    assert collected[0]["image"] == image
    assert ui.prompts[0] == ("image copiée", "Entrée : que vois-tu ?")


def test_vanished_image_shows_error(ui, collected, desktop) -> None:
    d = daemon.Daemon(Config(), ui)
    d.on_watch_event(watch.SCREENSHOT, "/tmp/n-existe-pas.png")
    d.open_from_mascot()
    assert collected == []
    assert ui.errors


def test_failure_has_priority_over_notice(ui, collected, desktop) -> None:
    d = daemon.Daemon(Config(), ui)
    d.on_watch_event(watch.SELECTION)
    d.handle(FAILURE)
    d.open_from_mascot()
    assert collected[0]["failure"] == FAILURE


def test_fullscreen_hides_and_shows_mascot(monkeypatch, ui) -> None:
    fullscreen = {"on": False}
    monkeypatch.setattr(daemon.hyprland, "active_is_fullscreen", lambda: fullscreen["on"])
    d = daemon.Daemon(Config(), ui)
    d.on_hyprland_event("activewindowv2", "abc")
    assert ui.hidden == []  # rien n'a changé
    fullscreen["on"] = True
    d.on_hyprland_event("fullscreen", "1")
    d.on_hyprland_event("activewindowv2", "abc")  # toujours en plein écran : pas de doublon
    fullscreen["on"] = False
    d.on_hyprland_event("workspacev2", "2,2")
    assert ui.hidden == [True, False]


def test_irrelevant_events_do_not_query_hyprland(monkeypatch, ui) -> None:
    calls = []
    monkeypatch.setattr(daemon.hyprland, "active_is_fullscreen", lambda: calls.append(1) or True)
    daemon.Daemon(Config(), ui).on_hyprland_event("openlayer", "custos")
    assert calls == []


def test_fullscreen_hiding_can_be_disabled(monkeypatch, ui) -> None:
    monkeypatch.setattr(daemon.hyprland, "active_is_fullscreen", lambda: True)
    config = Config()
    config.mascot.hide_in_fullscreen = False
    daemon.Daemon(config, ui).on_hyprland_event("fullscreen", "1")
    assert ui.hidden == []


BROWSER = ActiveWindow("brave-browser", "Docs", 71, 21, 1228, 773, address="0xb", workspace=1)


def test_attach_command_attaches_active_window(monkeypatch, ui) -> None:
    monkeypatch.setattr(daemon.hyprland, "active_window", lambda: BROWSER)
    daemon.Daemon(Config(), ui).handle({"type": "attach"})
    assert ui.attached == [BROWSER]


def test_attached_window_is_captured_even_if_not_active(monkeypatch, ui, collected) -> None:
    monkeypatch.setattr(daemon.hyprland, "find_window", lambda address: BROWSER)
    d = daemon.Daemon(Config(), ui)
    d.set_attached(BROWSER)  # l'overlay confirme l'accrochage
    d.open_from_mascot()
    assert collected[0]["window"] == BROWSER
    assert collected[0]["screenshot"] is True
    assert ui.prompts[0][0] == "📌 Brave browser"


def test_selection_ignores_attached_window(monkeypatch, ui, collected) -> None:
    monkeypatch.setattr(daemon.hyprland, "find_window", lambda address: BROWSER)
    d = daemon.Daemon(Config(), ui)
    d.set_attached(BROWSER)
    d.handle({"type": "ask", "selection": True})
    assert "window" not in collected[0]
    assert collected[0]["screenshot"] is False


def test_closed_attached_window_falls_back_to_active(monkeypatch, ui, collected) -> None:
    monkeypatch.setattr(daemon.hyprland, "find_window", lambda address: None)
    d = daemon.Daemon(Config(), ui)
    d.set_attached(BROWSER)
    d.open_from_mascot()
    assert "window" not in collected[0]
    assert collected[0]["screenshot"] is False


def test_blocked_window_cannot_be_attached(ui) -> None:
    config = Config()
    config.privacy.blocked_classes = ["brave-browser"]
    d = daemon.Daemon(config, ui)
    d.set_attached(BROWSER)
    assert d.attached is None
    assert ui.attached == [None]  # l'overlay est détaché aussitôt
    assert "protégée" in ui.errors[0]


def test_detach(ui) -> None:
    d = daemon.Daemon(Config(), ui)
    d.set_attached(BROWSER)
    d.handle({"type": "detach"})
    assert ui.attached == [None]
    d.set_attached(None)  # confirmation de l'overlay
    assert d.attached is None


def test_reopening_soon_resumes_conversation(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW, selection="ls -la"))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "selection": True})
    d.answer("explique")
    d.close_bubble()
    d.handle({"type": "ask"})  # clic sur la mascotte peu après
    assert ui.reopened == [("sélection : 6 car.", "explique", "Bonjour !")]
    assert len(ui.prompts) == 1  # pas de nouvelle conversation


def test_reopening_later_starts_new_conversation(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("salut")
    d.close_bubble()
    later = daemon.time.monotonic() + daemon.RESUME_WINDOW + 1
    monkeypatch.setattr(daemon.time, "monotonic", lambda: later)
    d.handle({"type": "ask"})
    assert ui.reopened == []
    assert len(ui.prompts) == 2


def test_new_content_starts_new_conversation(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW, selection="ls"))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("salut")
    d.close_bubble()
    d.handle({"type": "ask", "selection": True})  # une sélection remarquée : nouveau contenu
    assert ui.reopened == []


def test_plus_button_starts_new_conversation(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("salut")
    d.new_conversation()
    assert len(ui.prompts) == 2
    assert d.session.claude_session is None


def test_closing_interrupts_answer(monkeypatch, ui) -> None:
    started = threading.Event()

    def endless_ask(prompt, config, images=None, resume=None, on_session=None, cancel=None):
        on_session("session-1")
        started.set()
        yield "Il était une fois"
        cancel.wait(2)  # comme claude.ask : s'arrête quand on annule

    monkeypatch.setattr(claude, "ask", endless_ask)
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    worker = threading.Thread(target=d.answer, args=("raconte",))
    worker.start()
    started.wait(2)
    d.close_bubble()
    worker.join(2)
    assert not worker.is_alive()
    assert ui.answers == []  # pas de réponse affichée après la fermeture
    assert ui.states[-1] is MascotState.IDLE


def test_closing_without_answer_forgets_conversation(monkeypatch, ui, tmp_path) -> None:
    shot = tmp_path / "custos.png"
    shot.write_bytes(b"png")
    use_context(monkeypatch, Context(window=WINDOW, screenshot=shot))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "screen": True})
    d.close_bubble()
    assert d.session is None
    assert not shot.exists()


def test_toggle_command(ui) -> None:
    daemon.Daemon(Config(), ui).handle({"type": "toggle"})
    assert ui.toggles == 1


def test_open_full_launches_claude_in_terminal(monkeypatch, ui, prompts, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    launched = []
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda cmd, **kw: launched.append((cmd, kw)))
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "question": "salut ?"})
    d.open_full()
    cmd, options = launched[0]
    conversations = str(tmp_path / "custos" / "conversations")
    assert cmd == [
        "foot", f"--working-directory={conversations}",
        "claude", "--resume", "session-1", "--model", "claude-opus-5-5",
    ]
    assert options["start_new_session"] is True


def test_open_full_without_conversation(ui) -> None:
    daemon.Daemon(Config(), ui).open_full()
    assert ui.errors == ["Pose d'abord une question."]


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
    assert "custos start" in capsys.readouterr().out
    assert len(notified) == 1
