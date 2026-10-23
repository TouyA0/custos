import queue
import subprocess
from pathlib import Path

import pytest

from custos import watch


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "POLL_INTERVAL", 0.05)
    monkeypatch.setattr(watch, "STARTUP_GRACE", 0.0)


def test_new_caelestia_screenshot_is_noticed(tmp_path: Path) -> None:
    (tmp_path / "caelestia-picker-old.png").write_bytes(b"ancienne")  # déjà là au démarrage
    events: queue.Queue = queue.Queue()
    watcher = watch.Watcher(
        lambda kind, path: events.put((kind, path)),
        selection=False,
        images=False,
        screenshot_glob=str(tmp_path / "caelestia-picker-*.png"),
    )
    watcher.start()
    try:
        new = tmp_path / "caelestia-picker-new.png"
        new.write_bytes(b"png")
        assert events.get(timeout=2) == (watch.SCREENSHOT, str(new))
        assert events.empty()
    finally:
        watcher.stop()


def test_events_during_startup_grace_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "STARTUP_GRACE", 60.0)
    events: list = []
    watcher = watch.Watcher(lambda kind, path: events.append(kind), selection=False, images=False)
    watcher.start()
    watcher._emit(watch.SELECTION)
    assert events == []


def test_handler_errors_do_not_stop_watching() -> None:
    def broken(kind, path):
        raise RuntimeError("oups")

    watcher = watch.Watcher(broken, selection=False, images=False)
    watcher.start()
    watcher._emit(watch.SELECTION)  # ne lève pas


@pytest.mark.parametrize(
    ("types", "expected"),
    [("image/png\nimage/jpeg\n", True), ("text/plain\nUTF8_STRING\n", False), ("", False)],
)
def test_clipboard_has_image(monkeypatch: pytest.MonkeyPatch, types: str, expected: bool) -> None:
    monkeypatch.setattr(
        watch.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=types),
    )
    assert watch.clipboard_has_image() is expected
