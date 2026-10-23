import socket
import threading
from pathlib import Path

import pytest

from custos.context import hyprland


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        (None, False),
        ({"fullscreen": 0}, False),
        ({"fullscreen": 1}, False),  # maximisée : la mascotte reste visible
        ({"fullscreen": 2}, True),
        ({"fullscreen": 3}, True),
        ({}, False),
    ],
)
def test_is_fullscreen(window, expected) -> None:
    assert hyprland.is_fullscreen(window) is expected


def test_listen_reads_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "test")
    path = tmp_path / "hypr" / "test" / ".socket2.sock"
    path.parent.mkdir(parents=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def hyprland_sends() -> None:
        conn, _ = server.accept()
        conn.sendall(b"workspacev2>>2,2\nfullscreen>>1\nactivewindowv2>>")
        conn.sendall(b"55aa\n")  # ligne arrivée en deux morceaux
        conn.close()

    threading.Thread(target=hyprland_sends, daemon=True).start()
    events: list[tuple[str, str]] = []
    stop = threading.Event()

    def on_event(name: str, data: str) -> None:
        events.append((name, data))
        if len(events) == 3:
            stop.set()

    hyprland.listen(on_event, stop)
    server.close()
    assert events == [("workspacev2", "2,2"), ("fullscreen", "1"), ("activewindowv2", "55aa")]


def test_listen_without_hyprland_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    hyprland.listen(lambda *a: None, threading.Event())  # ne bloque pas
