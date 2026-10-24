import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from custos import supervisor


@pytest.fixture(autouse=True)
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_notify_gave_up", lambda: None)
    # supervise() installe des gestionnaires de signaux : on remet ceux de pytest après.
    handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield tmp_path
    for sig, handler in handlers.items():
        signal.signal(sig, handler)


def counting_child(tmp_path: Path, exit_codes: list[int]) -> list[str]:
    """Commande qui note chaque lancement et sort avec le code suivant de la liste."""
    script = tmp_path / "child.py"
    script.write_text(
        "import pathlib, sys\n"
        f"runs = pathlib.Path({str(tmp_path / 'runs')!r})\n"
        "n = len(runs.read_text()) if runs.exists() else 0\n"
        "runs.write_text('x' * (n + 1))\n"
        "print('ligne du daemon', flush=True)\n"
        f"codes = {exit_codes!r}\n"
        "sys.exit(codes[min(n, len(codes) - 1)])\n"
    )
    return [sys.executable, str(script)]


def runs(tmp_path: Path) -> int:
    return len((tmp_path / "runs").read_text())


def test_crashed_daemon_is_restarted(dirs: Path) -> None:
    assert supervisor.supervise(counting_child(dirs, [1, 2, 0]), backoff=0) == 0
    assert runs(dirs) == 3
    log = supervisor.log_path().read_text()
    assert "relance" in log
    assert "ligne du daemon" in log  # la sortie du daemon arrive dans le même log
    assert not supervisor.pid_path().exists()


def test_normal_exit_is_not_restarted(dirs: Path) -> None:
    assert supervisor.supervise(counting_child(dirs, [0]), backoff=0) == 0
    assert runs(dirs) == 1


def test_gives_up_after_too_many_crashes(dirs: Path) -> None:
    assert supervisor.supervise(counting_child(dirs, [1]), backoff=0) == 1
    assert runs(dirs) == supervisor.MAX_CRASHES + 1
    assert "abandon" in supervisor.log_path().read_text()


def test_other_daemon_running_stops_supervisor(dirs: Path) -> None:
    code = supervisor.EXIT_ALREADY_RUNNING
    assert supervisor.supervise(counting_child(dirs, [code]), backoff=0) == 1
    assert runs(dirs) == 1


def test_sigterm_stops_daemon_and_supervisor(dirs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Un gestionnaire de signal ne s'installe que dans le thread principal : on capture
    # celui du superviseur (lancé dans un thread) pour l'appeler nous-mêmes.
    handlers: dict = {}
    monkeypatch.setattr(supervisor.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))
    sleeper = dirs / "sleeper.py"
    sleeper.write_text("import time\ntime.sleep(30)\n")
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(supervisor.supervise([sys.executable, str(sleeper)]))
    )
    started = time.monotonic()
    thread.start()
    while signal.SIGTERM not in handlers or not supervisor.read_pid():
        time.sleep(0.05)
    time.sleep(0.2)  # le temps que le daemon soit lancé
    handlers[signal.SIGTERM]()
    thread.join(5)

    assert result == [0]
    assert time.monotonic() - started < 5
    assert not supervisor.pid_path().exists()


def test_second_supervisor_refused(dirs: Path) -> None:
    supervisor.state_dir().mkdir(parents=True)
    supervisor.write_pid()  # processus vivant
    assert supervisor.supervise(counting_child(dirs, [0]), backoff=0) == 1
    assert not (dirs / "runs").exists()


def test_stale_pid_file_is_ignored(dirs: Path) -> None:
    supervisor.pid_path().write_text("999999999 12345")
    assert supervisor.read_pid() is None


def test_pid_reused_by_another_process_is_ignored(dirs: Path) -> None:
    pid = supervisor.os.getpid()
    supervisor.write_pid()
    assert supervisor.read_pid() == pid
    # Même PID, autre processus : son instant de démarrage diffère.
    supervisor.pid_path().write_text(f"{pid} {int(supervisor.start_time(pid)) - 1}")
    assert supervisor.read_pid() is None


def test_old_pid_file_checks_the_command(dirs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = supervisor.os.getpid()
    supervisor.pid_path().write_text(str(pid))
    assert supervisor.read_pid() is None  # pytest n'est pas `custos daemon`
    cmdline = b"/home/q/custos/.venv/bin/python\0-m\0custos\0daemon\0"
    monkeypatch.setattr(supervisor.Path, "read_bytes", lambda self: cmdline)
    assert supervisor.read_pid() == pid


def test_big_log_is_rotated(dirs: Path) -> None:
    supervisor.state_dir().mkdir(parents=True)
    supervisor.log_path().write_text("x" * (supervisor.MAX_LOG_SIZE + 1))
    supervisor.supervise(counting_child(dirs, [0]), backoff=0)
    assert supervisor.log_path().with_suffix(".log.1").stat().st_size > supervisor.MAX_LOG_SIZE
    assert supervisor.log_path().stat().st_size < 10_000


def test_log_is_rotated_while_running(dirs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "MAX_LOG_SIZE", 5_000)
    chatty = dirs / "chatty.py"
    chatty.write_text("for i in range(500):\n    print(f'ligne {i} du daemon', flush=True)\n")
    supervisor.supervise([sys.executable, str(chatty)], backoff=0)
    log, old = supervisor.log_path(), supervisor.log_path().with_suffix(".log.1")
    assert log.stat().st_size <= 5_000 and old.stat().st_size <= 5_000
    assert "ligne 499 du daemon" in old.read_text() + log.read_text()  # rien de perdu à la fin
    assert "arrêté" in log.read_text()
    assert not supervisor.log_path().with_suffix(".log.2").exists()  # un seul ancien log


def test_worker_env_preloads_layer_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lib = tmp_path / "libgtk4-layer-shell.so"
    lib.write_bytes(b"")
    monkeypatch.setattr(supervisor, "LAYER_SHELL_LIB", lib)
    monkeypatch.setenv("LD_PRELOAD", "/autre.so")
    assert supervisor.worker_env()["LD_PRELOAD"] == f"{lib} /autre.so"
    monkeypatch.setenv("LD_PRELOAD", str(lib))
    assert supervisor.worker_env()["LD_PRELOAD"] == str(lib)


def test_worker_env_renders_without_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GSK_RENDERER", raising=False)
    monkeypatch.delenv("GDK_DISABLE", raising=False)
    env = supervisor.worker_env()
    assert env["GSK_RENDERER"] == "cairo" and env["GDK_DISABLE"] == "gl,vulkan,dmabuf"
    monkeypatch.setenv("GSK_RENDERER", "gl")  # choix explicite : respecté, GPU permis
    env = supervisor.worker_env()
    assert env["GSK_RENDERER"] == "gl" and "GDK_DISABLE" not in env


def test_status_when_stopped(capsys) -> None:
    assert supervisor.status() == 1
    assert "ne tourne pas" in capsys.readouterr().out
