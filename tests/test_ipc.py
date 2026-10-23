import queue
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from custos import ipc


@pytest.fixture(autouse=True)
def runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def received() -> Iterator[queue.Queue]:
    messages: queue.Queue = queue.Queue()
    server = ipc.Server(messages.put)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield messages
    server.shutdown()
    server.server_close()


def test_message_reaches_daemon(received: queue.Queue) -> None:
    ipc.send({"type": "ask", "question": "salut ?"})
    assert received.get(timeout=2) == {"type": "ask", "question": "salut ?"}


def test_socket_is_private(received: queue.Queue, runtime_dir: Path) -> None:
    assert (runtime_dir / "custos.sock").stat().st_mode & 0o777 == 0o600


def test_send_without_daemon_raises() -> None:
    with pytest.raises(ipc.DaemonNotRunning):
        ipc.send({"type": "ask"})


def test_second_daemon_is_refused(received: queue.Queue) -> None:
    with pytest.raises(ipc.DaemonAlreadyRunning):
        ipc.Server(lambda m: None)


def test_stale_socket_is_replaced(runtime_dir: Path) -> None:
    # Socket laissé par un daemon qui a planté : plus personne n'écoute derrière.
    crashed = ipc.Server(lambda m: None)
    crashed.socket.close()
    assert (runtime_dir / "custos.sock").exists()

    server = ipc.Server(lambda m: None)
    server.server_close()
    assert not (runtime_dir / "custos.sock").exists()
