from pathlib import Path

import pytest

from custos import context
from custos.config import PrivacyConfig
from custos.context import Context, build_prompt
from custos.context.hyprland import ActiveWindow, parse

WINDOW = ActiveWindow("foot", "~/code", x=71, y=21, width=1228, height=773)


def test_parse_hyprctl_output() -> None:
    window = parse({"class": "foot", "title": "~/code", "at": [71, 21], "size": [1228, 773]})
    assert window == WINDOW
    assert window.geometry == "71,21 1228x773"


def test_parse_without_window() -> None:
    assert parse({}) is None


@pytest.fixture
def fake_desktop(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Remplace hyprctl, wl-paste et grim ; renvoie la liste des captures demandées."""
    captures: list[str] = []
    monkeypatch.setattr(context.hyprland, "active_window", lambda: WINDOW)
    monkeypatch.setattr(context.clipboard, "selection", lambda: "export TOKEN=abcdef123456")
    monkeypatch.setattr(
        context.screen, "capture", lambda w, _config: captures.append(w.geometry) or Path("/tmp/x.png")
    )
    return captures


def test_collect_reads_and_redacts_selection(fake_desktop: list[str]) -> None:
    ctx = context.collect(PrivacyConfig(), selection=True)
    assert ctx.window == WINDOW
    assert ctx.selection == "export TOKEN=[secret masqué]"
    assert fake_desktop == []


def test_collect_screenshot(fake_desktop: list[str]) -> None:
    ctx = context.collect(PrivacyConfig(), screenshot=True)
    assert ctx.screenshot == Path("/tmp/x.png")
    assert fake_desktop == ["71,21 1228x773"]


def test_blocked_window_reads_nothing(fake_desktop: list[str]) -> None:
    ctx = context.collect(PrivacyConfig(blocked_classes=["foot"]), selection=True, screenshot=True)
    assert ctx.blocked
    assert ctx.selection == ""
    assert ctx.screenshot is None
    assert fake_desktop == []


def test_window_blocked_by_title_reads_nothing(fake_desktop: list[str]) -> None:
    ctx = context.collect(PrivacyConfig(blocked_titles=["~/CODE*"]), selection=True, screenshot=True)
    assert ctx.blocked
    assert ctx.selection == ""
    assert fake_desktop == []


def test_prompt_with_selection_and_default_question() -> None:
    prompt = build_prompt(None, Context(window=WINDOW, selection="ls -la"))
    assert "Fenêtre active : foot — « ~/code »" in prompt
    assert "```\nls -la\n```" in prompt
    assert prompt.endswith(f"Question : {context.DEFAULT_QUESTION}")


def test_prompt_hides_blocked_window() -> None:
    prompt = build_prompt("salut", Context(window=WINDOW, blocked=True))
    assert prompt == "Question : salut"


def test_cleanup_removes_screenshot(tmp_path: Path) -> None:
    shot = tmp_path / "custos.png"
    shot.write_bytes(b"png")
    ctx = Context(screenshot=shot)
    ctx.cleanup()
    assert not shot.exists()
    assert ctx.screenshot is None


@pytest.mark.parametrize(
    ("window_class", "name"),
    [("com.anthropic.Claude", "Claude"), ("brave-browser", "Brave browser"), ("foot", "Foot")],
)
def test_app_name(window_class: str, name: str) -> None:
    assert context.app_name(window_class) == name


def test_describe_says_nothing_for_plain_active_window() -> None:
    assert context.describe(Context(window=WINDOW)) == ""
    assert context.describe(Context(window=WINDOW, screenshot=Path("/tmp/x.png"))) == "capture de Foot"


def test_failure_output_is_redacted_then_truncated() -> None:
    output = "x" * (context.MAX_OUTPUT + 10) + " token=abcdef123456 erreur finale"
    failure = {"command": "make", "exit_code": 2, "output": output}
    prompt = build_prompt(None, Context(window=WINDOW, failure=failure))
    assert "[… début tronqué]" in prompt
    assert "abcdef123456" not in prompt
    assert "erreur finale\n```" in prompt
