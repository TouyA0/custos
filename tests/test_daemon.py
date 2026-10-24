import threading
import time
from pathlib import Path

import pytest

from custos import claude, cli, context, daemon, system, watch
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
        self.histories: list[list[tuple[str, str]]] = []
        self.attachments: list[int] = []
        self.quotas: list = []
        self.monitor_changes = 0
        self.statuses: list[str] = []
        self.resets = 0
        self.permissions: list[str | None] = []
        self.progress: list[tuple] = []
        self.alerts: list[tuple] = []

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

    def set_history(self, exchanges: list[tuple[str, str]]) -> None:
        self.histories.append(list(exchanges))

    def set_attachments(self, count: int) -> None:
        self.attachments.append(count)

    def set_quota(self, quota) -> None:
        self.quotas.append(quota)

    def reopen(self, summary: str, question: str, answer: str) -> None:
        self.reopened.append((summary, question, answer))

    def monitors_changed(self) -> None:
        self.monitor_changes += 1

    def set_status(self, text: str) -> None:
        self.statuses.append(text)

    def reset_answer(self) -> None:
        self.resets += 1

    def ask_permission(self, description: str) -> None:
        self.permissions.append(description)

    def permission_done(self) -> None:
        self.permissions.append(None)

    def show_progress(self, question: str, status: str, answer: str, permission: str) -> None:
        self.progress.append((question, status, answer, permission))

    def set_alert(self, alert: str | None, text: str) -> None:
        self.alerts.append((alert, text))


@pytest.fixture(autouse=True)
def no_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ni détection de la vraie machine, ni lecture du vrai custos.md."""
    monkeypatch.setattr(daemon.machine, "describe", lambda _command: "- Distribution : Arch")
    monkeypatch.setattr(daemon, "load_persona", lambda: "")


@pytest.fixture
def ui() -> FakeUI:
    return FakeUI()


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list | None]]:
    """Remplace Claude : renvoie toujours « Bonjour ! » et note les prompts reçus."""
    sent: list[tuple[str, list | None]] = []

    def fake_ask(
        prompt, config, images=None, system_prompt="", resume=None, on_session=None, cancel=None,
        **_kwargs,
    ):
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

    def fake_ask(
        prompt, config, images=None, system_prompt="", resume=None, on_session=None, cancel=None,
        **_kwargs,
    ):
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


def test_shortcut_while_busy_shows_progress(monkeypatch, ui) -> None:
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
    d.handle({"type": "ask", "question": "2"})  # la carte reprend là où il en est
    d.answer("3")  # une autre question pendant qu'il travaille : refusée
    release.set()
    first.join(2)
    assert ui.answers == ["fini"]
    assert ui.progress == [("1", "", "", "")]
    assert len(ui.errors) == 1 and "déjà" in ui.errors[0]


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
        problem=None,
        image=None,
        image_kind="window",
        window=None,
        attached=False,
    ):
        calls.append(
            {"selection": selection, "screenshot": screenshot, "failure": failure, "image": image}
        )
        if problem:
            calls[-1]["problem"] = problem
            return Context(problem=problem)
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
    d.close_bubble()
    d.on_watch_event(watch.SELECTION)
    assert ui.notices == [watch.SELECTION]


def test_noticed_again_after_a_conversation(ui, prompts, collected, desktop) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("salut")
    d.close_bubble()  # la session reste, pour la reprendre
    d.on_watch_event(watch.SELECTION)
    assert ui.notices[-1] == watch.SELECTION


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
    assert ui.prompts[0][0] == "accrochée à Brave browser"


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


def endless(started: threading.Event):
    def endless_ask(
        prompt, config, images=None, system_prompt="", resume=None, on_session=None, cancel=None,
        **_kwargs,
    ):
        on_session("session-1")
        started.set()
        yield "Il était une fois"
        cancel.wait(2)  # comme claude.ask : s'arrête quand on annule

    return endless_ask


def test_stop_button_interrupts_answer(monkeypatch, ui) -> None:
    started = threading.Event()
    monkeypatch.setattr(claude, "ask", endless(started))
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    worker = threading.Thread(target=d.answer, args=("raconte",))
    worker.start()
    started.wait(2)
    d.stop()
    worker.join(2)
    assert not worker.is_alive()
    assert ui.answers == ["Il était une fois"]  # ce qu'il avait déjà écrit
    assert ui.states[-1] is MascotState.IDLE


def test_closing_keeps_working_then_badge_and_click_reopens(monkeypatch, ui) -> None:
    def ask_then_close(*_a, on_session=None, **_k):
        on_session("session-1")
        yield "Bon"
        d.close_bubble()  # tu fermes la carte pendant qu'il répond
        yield "jour !"

    monkeypatch.setattr(claude, "ask", ask_then_close)
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("salut ?")
    assert ui.answers == ["Bonjour !"]
    assert ui.notices[-1] == daemon.ANSWER_READY
    d.open_from_mascot()
    assert ui.reopened == [("", "salut ?", "Bonjour !")]
    assert ui.notices[-1] is None


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


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Terminal foot actif ; note les raccourcis envoyés, sans attendre le délai."""
    sent: list[tuple] = []
    window = ActiveWindow("foot", "~", 0, 0, 100, 100, address="0xabc")
    monkeypatch.setattr(daemon.hyprland, "active_window", lambda: window)
    monkeypatch.setattr(daemon.hyprland, "send_shortcut", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(daemon, "_later", lambda _delay, action, *args: action(*args))
    return sent


def capturing_config() -> Config:
    config = Config()
    config.shell.capture_output = True
    return config


def test_failure_output_is_requested_and_joined(ui, prompts, collected, terminal) -> None:
    d = daemon.Daemon(capturing_config(), ui)
    d.handle(dict(FAILURE))
    assert terminal == [("CTRL SHIFT ALT", "F12", "0xabc")]
    d.handle({"type": "shell-output", "output": "pacmna : commande introuvable\n"})
    assert prompts == []  # rien n'est envoyé à Claude avant que tu le demandes

    d.open_from_mascot()
    assert ui.prompts[0][0] == "commande en échec : pacmna -Syu · sortie jointe"
    d.answer("")
    assert "Sa sortie :\n```\npacmna : commande introuvable\n```" in prompts[0][0]


def test_output_is_not_requested_by_default(ui, terminal) -> None:
    daemon.Daemon(Config(), ui).handle(dict(FAILURE))
    assert terminal == []


def test_output_is_not_requested_from_other_apps_or_blocked_terminals(
    monkeypatch, ui, terminal
) -> None:
    config = capturing_config()
    config.privacy.blocked_titles = ["*secret*"]
    d = daemon.Daemon(config, ui)
    monkeypatch.setattr(daemon.hyprland, "active_window", lambda: BROWSER)
    d.handle(dict(FAILURE))
    blocked = ActiveWindow("foot", "ssh serveur-secret", 0, 0, 100, 100, address="0xdef")
    monkeypatch.setattr(daemon.hyprland, "active_window", lambda: blocked)
    d.handle(dict(FAILURE))
    assert terminal == []


def test_unrequested_output_is_ignored(ui, terminal) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(dict(FAILURE))
    d.handle({"type": "shell-output", "output": "autre chose"})
    assert "output" not in d.last_failure


def test_cli_shell_output_reads_stdin(monkeypatch) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(cli.ipc, "send", sent.append)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("x" * 200_000 + "fin"))
    assert cli.main(["shell-output"]) == 0
    assert sent[0]["type"] == "shell-output"
    assert len(sent[0]["output"]) == cli.MAX_OUTPUT
    assert sent[0]["output"].endswith("fin")


def test_monitor_events_are_forwarded_and_fullscreen_is_per_monitor(monkeypatch, ui) -> None:
    monkeypatch.setattr(daemon.hyprland, "fullscreen_on", lambda name: name == "HDMI-A-1")
    d = daemon.Daemon(Config(), ui)
    d.on_hyprland_event("monitoraddedv2", "1,HDMI-A-1,")
    assert ui.monitor_changes == 1
    d.set_monitor("eDP-1")
    assert d.hidden is False
    d.set_monitor("HDMI-A-1")
    assert ui.hidden == [True]


def test_system_prompt_has_machine_and_persona(monkeypatch, ui) -> None:
    monkeypatch.setattr(daemon, "load_persona", lambda: "Tu es taquin.")
    prompt = daemon.Daemon(Config(), ui)._system_prompt()
    assert "Sa machine (détectée automatiquement) :\n- Distribution : Arch" in prompt
    assert "Tu es taquin." in prompt
    assert prompt.endswith(claude.BREVITY)


@pytest.fixture
def agents(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Sessions Claude Code toujours vivantes, fenêtre trouvée par PID, focus noté."""
    state: dict = {"focused": [], "window": ActiveWindow("foot", "claude", 0, 0, 9, 9, address="0xf00")}
    monkeypatch.setattr(daemon.Agent, "alive", lambda self: True)
    monkeypatch.setattr(daemon.hyprland, "window_of", lambda pids: state["window"])
    monkeypatch.setattr(
        daemon.hyprland, "focus_window", lambda address: state["focused"].append(address) or True
    )
    return state


def agent(event: str, session: str = "s1", **extra) -> dict:
    return {"type": "agent-event", "event": event, "session": session, "cwd": "/home/q/projet",
            "pids": [10, 11, 12], **extra}


def test_waiting_agent_badge_and_click_goes_to_its_window(ui, agents) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(agent("UserPromptSubmit"))
    assert ui.notices == [None]
    d.handle(agent("Notification", notification="permission_prompt"))
    assert ui.notices[-1] == daemon.AGENT_WAITING
    d.on_watch_event(watch.SCREENSHOT, "/tmp/x.png")
    assert ui.notices[-1] == daemon.AGENT_WAITING  # passe avant une capture remarquée
    d.open_from_mascot()
    assert agents["focused"] == ["0xf00"]
    assert ui.prompts == []  # pas de bulle : on va dans sa fenêtre
    d.handle(agent("PostToolUse"))  # permission donnée, il reprend
    assert ui.notices[-1] == watch.SCREENSHOT


def test_idle_notification_is_not_waiting(ui, agents) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(agent("Notification", notification="idle_prompt"))
    assert d.agents == {}


def test_done_agent_badge_stays_until_its_window_is_seen(ui, agents) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(agent("Stop"))
    assert ui.notices[-1] == daemon.AGENT_DONE
    d.on_hyprland_event("activewindowv2", "abc")  # une autre fenêtre
    assert d.notice is not None
    d.on_hyprland_event("activewindowv2", "f00")  # la sienne
    assert d.notice is None and ui.notices[-1] is None


def test_click_on_done_agent_goes_to_its_window(ui, agents) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(agent("Stop"))
    d.open_from_mascot()
    assert agents["focused"] == ["0xf00"]
    assert d.notice is None


def test_agent_without_window_explains(ui, agents) -> None:
    agents["window"] = None
    d = daemon.Daemon(Config(), ui)
    d.handle(agent("Notification", message="Claude needs your permission to use Bash"))
    d.open_from_mascot()
    assert ui.errors == ["Claude Code (projet) attend ta réponse, mais je ne trouve pas sa fenêtre."]


def test_session_end_and_custos_own_conversation(ui, agents) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(agent("Notification", notification="permission_prompt"))
    d.handle(agent("SessionEnd"))
    assert d.agents == {} and ui.notices[-1] is None
    d.handle(agent("Stop", cwd=str(claude.conversations_dir())))
    assert d.agents == {}


def test_agents_can_be_disabled(ui, agents) -> None:
    config = Config()
    config.agents.enabled = False
    d = daemon.Daemon(config, ui)
    d.handle(agent("Stop"))
    assert d.agents == {}


def test_cli_agent_event_from_hook_input() -> None:
    raw = '{"session_id": "s1", "hook_event_name": "Notification", "cwd": "/p",' \
          ' "notification_type": "permission_prompt", "message": "besoin de permission"}'
    message = cli.agent_event(raw)
    assert message["event"] == "Notification" and message["notification"] == "permission_prompt"
    assert message["pids"] and all(isinstance(p, int) for p in message["pids"])
    assert cli.agent_event("pas du json") is None
    assert cli.agent_event('{"hook_event_name": "Stop"}') is None


def agent_ask(steps):
    """Faux Claude en mode agent : `steps` mêle du texte, des étapes et des permissions."""
    def ask(prompt, config, *, on_session=None, on_tool=None, on_permission=None, agent=False,
            **_kwargs):
        on_session("session-1")
        for kind, value in steps:
            if kind == "text":
                yield value
            elif kind == "tool":
                on_tool(value)
            else:
                yield f"[{on_permission(claude.Permission('Bash', {'command': value}))}]"
    return ask


def answer_permission_when_asked(d, ui, choice) -> threading.Thread:
    def run() -> None:
        for _ in range(200):
            if d.work and d.work.permission:
                d.permission_answer(choice)
                return
            threading.Event().wait(0.01)
    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_agent_keeps_only_conclusion(monkeypatch, ui) -> None:
    monkeypatch.setattr(claude, "ask", agent_ask(
        [("text", "Je regarde le journal."), ("tool", "$ journalctl -b -1"), ("text", "C'est le PSR.")]
    ))
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "question": "pourquoi ça a crashé ?"})
    assert ui.statuses == ["$ journalctl -b -1"] and ui.resets == 1
    assert ui.answers == ["C'est le PSR."]


def test_sensitive_action_waits_for_answer_and_always_is_remembered(monkeypatch, ui) -> None:
    monkeypatch.setattr(claude, "ask", agent_ask([("permission", "rm -rf ~/x"), ("permission", "rm -rf ~/x")]))
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    helper = answer_permission_when_asked(d, ui, claude.ALWAYS)
    d.handle({"type": "ask", "question": "nettoie"})
    helper.join(2)
    assert ui.permissions == ["rm -rf ~/x", None]  # demandé une seule fois
    assert ui.answers == ["[always][allow]"]


def test_closed_card_shows_waiting_badge_and_click_shows_request(monkeypatch, ui) -> None:
    monkeypatch.setattr(claude, "ask", agent_ask([("tool", "$ ls"), ("permission", "rm x")]))
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    worker = threading.Thread(target=d.handle, args=({"type": "ask", "question": "range"},))
    worker.start()
    for _ in range(200):
        if d.work and d.work.permission:
            break
        threading.Event().wait(0.01)
    d.close_bubble()
    assert ui.notices[-1] == daemon.CUSTOS_WAITING
    d.open_from_mascot()
    assert ui.progress[-1] == ("range", "$ ls", "", "rm x")
    d.permission_answer(claude.DENY)
    worker.join(2)
    assert ui.answers == ["[deny]"]


def test_stop_while_waiting_denies(monkeypatch, ui) -> None:
    monkeypatch.setattr(claude, "ask", agent_ask([("permission", "rm x")]))
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    worker = threading.Thread(target=d.handle, args=({"type": "ask", "question": "range"},))
    worker.start()
    for _ in range(200):
        if d.work and d.work.permission:
            break
        threading.Event().wait(0.01)
    d.stop()
    worker.join(2)
    assert not worker.is_alive()


def test_no_tools_without_card_or_when_disabled(monkeypatch, ui) -> None:
    used: list[bool] = []

    def ask(prompt, config, *, agent=False, on_session=None, **_kwargs):
        used.append(agent)
        yield "ok"

    monkeypatch.setattr(claude, "ask", ask)
    use_context(monkeypatch, Context(window=WINDOW))
    daemon.Daemon(Config(), ui).handle({"type": "ask", "question": "?"})
    ui.interactive = False  # repli par notifications : personne pour valider une action
    daemon.Daemon(Config(), ui).handle({"type": "ask", "question": "?"})
    config = Config()
    config.claude.tools = False
    ui.interactive = True
    daemon.Daemon(config, ui).handle({"type": "ask", "question": "?"})
    assert used == [True, False, False]


def test_cli_terminal_prefills_fish_command() -> None:
    from custos import terminal

    argv = terminal.terminal_command("sudo rm 'a b'", "foot claude")
    if argv[1] == "fish":
        assert argv == ["foot", "fish", "-C", "commandline -- 'sudo rm \\'a b\\''"]


def test_open_history_reopens_conversation_for_follow_up(monkeypatch, ui, prompts) -> None:
    from custos import history

    found = history.Conversation("ancienne", 0.0, "le catéchisme", "et les anges ?", "CEC §328")
    monkeypatch.setattr(daemon.history, "find", lambda session: found if session == "ancienne" else None)
    monkeypatch.setattr(daemon.history, "search", lambda query: [found])
    d = daemon.Daemon(Config(), ui)
    assert d.history_items("caté")[0][0] == "ancienne"
    d.open_history("ancienne")
    assert ui.reopened[-1][1:] == ("et les anges ?", "CEC §328")
    d.answer("et saint Michel ?")
    assert prompts[-1][0] == "et saint Michel ?"  # suite de la session, sans contexte
    d.open_history("disparue")
    assert ui.errors == ["Je ne retrouve plus cette conversation."]


def test_fast_command_uses_fast_model_for_this_question_only(monkeypatch, ui) -> None:
    models: list = []

    def ask(prompt, config, *, model=None, on_session=None, **_kwargs):
        models.append((prompt, model))
        on_session("session-1")
        yield "ok"

    monkeypatch.setattr(claude, "ask", ask)
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("/vite c'est quoi ls -la ?")
    d.answer("et ls -lh ?")
    assert models[0][1] == "claude-sonnet-5-5" and models[0][0].endswith("Question : c'est quoi ls -la ?")
    assert models[1] == ("et ls -lh ?", None)  # la suivante : le modèle habituel
    assert ui.questions == ["c'est quoi ls -la ? · sonnet", "et ls -lh ?"]
    d.answer("/vite")
    assert ui.errors == ["Écris ta question après /vite."]


def test_short_model() -> None:
    assert daemon.short_model("claude-sonnet-5-5") == "sonnet"
    assert daemon.short_model("claude-haiku-5-5") == "haiku"


def test_tray_alert_follows_badges(monkeypatch, ui, agents) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(agent("Notification", notification="permission_prompt"))
    assert ui.alerts[-1] == ("waiting", "Claude Code (projet) attend ta permission")
    d.handle(agent("Stop"))
    assert ui.alerts[-1] == ("done", "Claude Code (projet) a fini")
    d.open_from_mascot()  # on y va : l'alerte s'éteint
    assert ui.alerts[-1] == (None, "")


def test_tray_alert_for_custos_waiting_with_card_closed(monkeypatch, ui) -> None:
    monkeypatch.setattr(claude, "ask", agent_ask([("permission", "rm -rf ~/vieux")]))
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    worker = threading.Thread(target=d.handle, args=({"type": "ask", "question": "range"},))
    worker.start()
    for _ in range(200):
        if d.work and d.work.permission:
            break
        threading.Event().wait(0.01)
    assert ui.alerts[-1] == (None, "")  # carte ouverte : la demande s'y affiche
    d.close_bubble()
    assert ui.alerts[-1] == ("waiting", "Custos attend ton accord : rm -rf ~/vieux")
    d.permission_answer(claude.DENY)
    worker.join(2)
    assert ui.alerts[-1] == ("done", "Custos a fini : range")


# --- plusieurs threads ---------------------------------------------------------------


def _in_thread(action, timeout: float = 2.0) -> bool:
    """Lance `action` dans un autre thread : True si elle a fini à temps (pas bloquée)."""
    thread = threading.Thread(target=action, daemon=True)
    thread.start()
    thread.join(timeout)
    return not thread.is_alive()


def test_state_is_free_while_claude_answers(monkeypatch, ui, agents) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    free: list[bool] = []

    def slow_ask(*_args, **_kwargs):
        yield "Bon"
        # Pendant la réponse, les autres threads touchent à l'état sans attendre.
        free.append(_in_thread(lambda: d.handle(agent("Stop"))))
        free.append(_in_thread(d.close_bubble))
        yield "jour"

    monkeypatch.setattr(claude, "ask", slow_ask)
    d.handle({"type": "ask", "question": "salut ?"})
    assert free == [True, True]
    assert ui.answers == ["Bonjour"]


def test_state_is_free_while_waiting_for_permission(monkeypatch, ui) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    choices: list[str] = []

    def asking(*_args, on_permission=None, **_kwargs):
        choices.append(on_permission(claude.Permission("Bash", {"command": "rm x"})))
        yield "fait"

    monkeypatch.setattr(claude, "ask", asking)
    answering = threading.Thread(target=d.answer, args=("range",), daemon=True)
    answering.start()
    while d.work is None or d.work.permission is None:
        time.sleep(0.01)
    assert _in_thread(lambda: d.permission_answer(claude.ALLOW))  # le bouton de la carte
    answering.join(2)
    assert choices == [claude.ALLOW]


def test_agents_from_many_threads(ui, agents) -> None:
    d = daemon.Daemon(Config(), ui)
    errors: list[Exception] = []

    def burst(n: int) -> None:
        try:
            for i in range(1000):
                event = "Notification" if i % 2 else "Stop"
                d.handle(agent(event, session=f"s{n}-{i % 7}", notification="permission_prompt"))
                d.handle(agent("SessionEnd", session=f"s{n}-{i % 5}"))
        except RuntimeError as exc:  # « dictionary changed size during iteration »
            errors.append(exc)

    threads = [threading.Thread(target=burst, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_command_from_answer_opens_in_terminal(monkeypatch, ui) -> None:
    opened: list[tuple[str, str]] = []
    monkeypatch.setattr(daemon.terminal, "open_with", lambda command, open_command: opened.append((command, open_command)))
    config = Config()
    daemon.Daemon(config, ui).run_in_terminal("df -h")
    assert opened == [("df -h", config.claude.open_command)]


def test_command_without_terminal_explains(monkeypatch, ui) -> None:
    def missing(*_args):
        raise FileNotFoundError("foot")

    monkeypatch.setattr(daemon.terminal, "open_with", missing)
    daemon.Daemon(Config(), ui).run_in_terminal("df -h")
    assert ui.errors and "terminal" in ui.errors[-1]


def test_follow_up_keeps_earlier_exchanges_above(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    assert ui.histories == [[]]  # nouvelle conversation : rien au-dessus
    d.answer("un")
    d.answer("deux")
    d.answer("trois")
    assert ui.histories[-1] == [("un", "Bonjour !"), ("deux", "Bonjour !")]
    d.close_bubble()
    d.handle({"type": "ask"})  # reprise : la dernière réponse, les autres au-dessus
    assert ui.histories[-1] == [("un", "Bonjour !"), ("deux", "Bonjour !")]
    assert ui.reopened[-1][1:] == ("trois", "Bonjour !")


def test_reopened_history_shows_its_exchanges(monkeypatch, ui) -> None:
    from custos import history

    exchanges = (("le catéchisme", "CEC"), ("et les anges ?", "CEC §328"))
    found = history.Conversation("ancienne", 0.0, "le catéchisme", "et les anges ?", "CEC §328", exchanges)
    monkeypatch.setattr(daemon.history, "find", lambda session: found)
    d = daemon.Daemon(Config(), ui)
    d.open_history("ancienne")
    assert ui.histories[-1] == [("le catéchisme", "CEC")]
    assert ui.reopened[-1][1:] == ("et les anges ?", "CEC §328")


def test_tray_menu_lists_recent_conversations_and_agents(monkeypatch, ui, agents) -> None:
    from custos import history

    found = history.Conversation("ancienne", 0.0, "le catéchisme", "et les anges ?", "CEC §328")
    monkeypatch.setattr(daemon.history, "search", lambda query, limit=8: [found])
    opened: list[str] = []
    monkeypatch.setattr(daemon.Daemon, "open_history", lambda self, session: opened.append(session))
    d = daemon.Daemon(Config(), ui)
    d.handle(agent("Notification", notification="permission_prompt"))
    labels = [entry[0] if entry else None for entry in d.tray_menu()]
    assert labels == [
        "Poser une question", "Conversations récentes", None, "Sessions Claude Code", "projet · attend ton accord",
    ]
    assert d.tray_menu()[4][2] == ("attention", "#e8743b")  # icône orange, pas d'emoji

    menu = d.tray_menu()
    conversation = menu[1][1][0]
    assert "le catéchisme" in conversation[0]
    conversation[1]()  # dans un thread
    time.sleep(0.1)
    assert opened == ["ancienne"]
    menu[4][1]()
    time.sleep(0.1)
    assert agents["focused"] == ["0xf00"]


def test_tray_menu_without_history_or_agents(monkeypatch, ui) -> None:
    monkeypatch.setattr(daemon.history, "search", lambda query, limit=8: [])
    assert [entry[0] for entry in daemon.Daemon(Config(), ui).tray_menu()] == ["Poser une question"]


def test_pasted_image_goes_with_the_next_question(monkeypatch, ui, prompts, tmp_path) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    image = tmp_path / "jointe.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(daemon.images, "prepare", lambda source: image)
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.add_image(tmp_path / "source.png")
    assert ui.attachments[-1] == 1
    d.answer("")  # Entrée sans texte : que vois-tu ?
    prompt, sent = prompts[-1]
    assert sent == [image] and "Je joins aussi 1 image" in prompt and "Que vois-tu" in prompt
    assert ui.attachments[-1] == 0 and not image.exists()  # envoyée puis supprimée


def test_image_in_follow_up(monkeypatch, ui, prompts, tmp_path) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    image = tmp_path / "jointe.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(daemon.images, "prepare", lambda source: image)
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("salut")
    d.add_image(tmp_path / "source.png")
    d.answer("et ça ?")
    prompt, sent = prompts[-1]
    assert sent == [image] and prompt.startswith("Je joins aussi 1 image") and prompt.endswith("et ça ?")


def test_dropped_image_opens_the_card(monkeypatch, ui, tmp_path) -> None:
    image = tmp_path / "jointe.png"
    monkeypatch.setattr(daemon.images, "prepare", lambda source: image)
    asked: list[dict] = []
    monkeypatch.setattr(daemon.Daemon, "ask", lambda self, message: asked.append(message))
    source = tmp_path / "copie-du-navigateur.png"
    source.write_bytes(b"png")
    daemon.Daemon(Config(), ui).add_image(source, temporary=True)
    assert asked == [{"type": "ask", "image": str(image), "image_kind": "dropped"}]
    assert not source.exists()  # copie temporaire supprimée


def test_unreadable_image_explains(monkeypatch, ui, tmp_path) -> None:
    monkeypatch.setattr(daemon.images, "prepare", lambda source: None)
    daemon.Daemon(Config(), ui).add_image(tmp_path / "notes.txt")
    assert ui.errors == ["Je ne sais pas lire cette image."]


def test_attachments_can_be_removed(monkeypatch, ui, tmp_path) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    image = tmp_path / "jointe.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(daemon.images, "prepare", lambda source: image)
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.add_image(tmp_path / "source.png")
    d.remove_attachments()
    assert ui.attachments[-1] == 0 and not image.exists() and d.session.context.attachments == []


def test_replay_asks_the_last_question_again(monkeypatch, ui, prompts) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("/r")
    assert ui.errors == ["Pas encore de question à reposer."] and prompts == []
    d.answer("/vite c'est quoi fstab ?")
    d.answer("/r")
    assert prompts[-1][0] == "c'est quoi fstab ?"  # suivi : juste la question
    assert ui.questions[-1] == "c'est quoi fstab ? · sonnet"  # même modèle rapide


def test_replay_after_a_failed_question(monkeypatch, ui) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    calls: list[str] = []

    def failing_then_ok(prompt, *_args, **_kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            raise claude.ClaudeError("pas de réponse après 120 s")
        yield "ok"

    monkeypatch.setattr(claude, "ask", failing_then_ok)
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask"})
    d.answer("pourquoi ça crashe ?")
    d.answer("/r")
    assert calls[1].endswith("Question : pourquoi ça crashe ?") and ui.answers[-1] == "ok"



SERVICE_PROBLEM = system.Problem(system.SERVICE, "service:user:sync.service", "service utilisateur en échec : sync.service", "× sync.service\ntoken=abcdef123456")


def test_problem_lights_amber_badge_and_click_opens_it(ui, collected) -> None:
    d = daemon.Daemon(Config(), ui)
    d.on_problem(SERVICE_PROBLEM)
    assert ui.notices[-1] == daemon.SYSTEM_PROBLEM and ui.states[-1] == MascotState.WORRIED
    assert ui.alerts[-1] == (daemon.ALERT_PROBLEM, "service utilisateur en échec : sync.service")
    d.open_from_mascot()
    assert collected[-1]["problem"]["title"] == "service utilisateur en échec : sync.service"
    assert d.problems == {} and ui.notices[-1] is None  # regardé : le badge s'éteint
    assert ui.prompts[-1] == ("service utilisateur en échec : sync.service", "Entrée : explique et aide-moi à réparer")


def test_problem_prompt_has_redacted_evidence() -> None:
    ctx = Context(problem=SERVICE_PROBLEM.as_message())
    prompt = context.build_prompt(None, ctx)
    assert "Custos a remarqué ce problème sur ma machine : service utilisateur en échec" in prompt
    assert "× sync.service" in prompt and "abcdef123456" not in prompt
    assert prompt.endswith("Question : Explique ce problème et aide-moi à le réparer.")


def test_resolved_problem_turns_badge_off(ui) -> None:
    d = daemon.Daemon(Config(), ui)
    d.on_problem(SERVICE_PROBLEM)
    d.on_resolved(SERVICE_PROBLEM.key)
    assert d.problems == {} and ui.notices[-1] is None


def test_recent_failure_comes_before_problem(monkeypatch, ui, collected) -> None:
    d = daemon.Daemon(Config(), ui)
    d.on_problem(SERVICE_PROBLEM)
    d.shell_event({"exit_code": 1, "command": "make", "cwd": "/tmp"})
    d.open_from_mascot()
    assert collected[-1]["failure"]["command"] == "make"
    assert SERVICE_PROBLEM.key in d.problems  # toujours là pour le clic suivant


def code_message(count: int, file: str = "/home/q/projet/main.py") -> dict:
    errors = [{"line": i + 1, "message": f"erreur {i}", "source": "Pylance"} for i in range(count)]
    return {"type": "code-problem", "file": file, "workspace": "/home/q/projet", "count": count, "errors": errors}


def test_code_errors_only_when_they_increase(ui) -> None:
    d = daemon.Daemon(Config(), ui)
    d.handle(code_message(2))
    problem = d.problems["code:/home/q/projet/main.py"]
    assert problem.title == "2 erreurs dans main.py" and "ligne 2 [Pylance] : erreur 1" in problem.details
    d.problems.clear()
    d.handle(code_message(2))  # pas plus d'erreurs qu'avant : rien
    d.handle(code_message(1))
    assert d.problems == {}
    d.handle(code_message(3))
    assert d.problems["code:/home/q/projet/main.py"].title == "3 erreurs dans main.py"
    d.handle(code_message(0))  # corrigé : l'alerte s'éteint
    assert d.problems == {}


def test_code_problems_can_be_disabled(ui) -> None:
    config = Config()
    config.system.code = False
    d = daemon.Daemon(config, ui)
    d.handle(code_message(2))
    assert d.problems == {}


def test_tray_menu_lists_problems_to_look_at(monkeypatch, ui) -> None:
    monkeypatch.setattr(daemon.history, "search", lambda query, limit=8: [])
    d = daemon.Daemon(Config(), ui)
    d.on_problem(SERVICE_PROBLEM)
    menu = d.tray_menu()
    assert [e[0] if e else None for e in menu] == [
        "Poser une question", None, "À regarder", "service utilisateur en échec : sync.service", None,
    ]
    assert menu[3][2] == ("alert", "#d9a33a")



def test_quota_from_answer_is_saved_and_shown(monkeypatch, ui, tmp_path) -> None:
    use_context(monkeypatch, Context(window=WINDOW))
    monkeypatch.setattr(daemon.quota, "path", lambda: tmp_path / "quota.json")
    info = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.5, "resetsAt": 2e9}}}

    def answering(*_args, on_rate_limit=None, **_kwargs):
        on_rate_limit(info)
        yield "ok"

    monkeypatch.setattr(claude, "ask", answering)
    d = daemon.Daemon(Config(), ui)
    d.handle({"type": "ask", "question": "salut"})
    assert ui.quotas[-1].windows[0].utilization == 0.5
    ui.quotas.clear()
    daemon.Daemon(Config(), ui).show_saved_quota()  # après un redémarrage
    assert ui.quotas[-1].windows[0].name == "five_hour"
