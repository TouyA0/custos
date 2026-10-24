import json
from collections import namedtuple
from pathlib import Path

import pytest

from custos import system
from custos.config import SystemConfig

PACMAN = """\
[2026-10-22T19:03:40+0200] [PACMAN] Running 'pacman -Syu'
[2026-10-22T19:03:45+0200] [ALPM] warning: /etc/pacman.d/mirrorlist installed as /etc/pacman.d/mirrorlist.pacnew
[2026-10-22T19:05:00+0200] [ALPM-SCRIPTLET] /usr/bin/quickshell: symbol lookup error: undefined symbol: _ZN23Q
[2026-10-22T19:05:01+0200] [ALPM] transaction completed
[2026-10-22T19:10:00+0200] [PACMAN] Running 'pacman -S htop'
[2026-10-22T19:10:02+0200] [ALPM] installed htop (3.4.1-1)
[2026-10-22T19:12:00+0200] [PACMAN] Running 'pacman -U paquet.zst'
[2026-10-22T19:12:01+0200] [ALPM] error: could not open file paquet.zst
""".splitlines()


def test_update_errors_ignore_pacnew_and_clean_transactions() -> None:
    assert system.update_errors(PACMAN) == [
        ("pacman -Syu", [PACMAN[2]]),
        ("pacman -U paquet.zst", [PACMAN[7]]),
    ]


def test_failed_units() -> None:
    output = "nginx.service loaded failed failed A high performance web server\n● backup.timer loaded failed failed Sauvegarde\n"
    assert system.failed_units(output) == ["nginx.service", "backup.timer"]
    assert system.failed_units("") == []


def test_real_mounts_one_per_device_and_writable() -> None:
    mounts = """\
/dev/nvme0n1p2 / ext4 rw,relatime 0 0
/dev/nvme0n1p1 /boot vfat rw 0 0
tmpfs /tmp tmpfs rw 0 0
/dev/sda1 /mnt/cd iso9660 ro 0 0
/dev/sdb1 /mnt/save btrfs ro 0 0
/dev/sdc1 /data btrfs rw,subvol=/a 0 0
/dev/sdc1 /data/b btrfs rw,subvol=/b 0 0
/dev/sdd1 /mnt/mon\\040disque ext4 rw 0 0
"""
    assert system.real_mounts(mounts) == ["/", "/boot", "/data", "/mnt/mon disque"]


def test_disk_thresholds() -> None:
    go = 1024**3
    assert system.disk_low(1000 * go, 9 * go)  # gros disque : plafonné à 10 Go
    assert not system.disk_low(1000 * go, 30 * go)
    assert system.disk_low(1 * go, 40 * 1024**2)  # petit /boot : 5 %
    assert not system.disk_low(1 * go, 900 * 1024**2)
    assert not system.disk_recovered(1000 * go, 12 * go) and system.disk_recovered(1000 * go, 16 * go)


def test_new_crashes_and_signal_names() -> None:
    output = json.dumps([{"time": 100, "pid": 1, "sig": 6, "exe": "/usr/bin/fish"}, {"time": 300, "pid": 2, "sig": 11, "exe": "/usr/bin/foot"}])
    assert [c["pid"] for c in system.new_crashes(output, 200)] == [2]
    assert system.new_crashes("pas du json", 0) == []
    assert system.signal_name(11) == "SIGSEGV" and system.signal_name(99) == "signal 99"


class FakeSystem:
    """Remplace systemctl et coredumpctl."""

    def __init__(self) -> None:
        self.failed = {"system": "", "user": ""}
        self.crashes = "[]"

    def __call__(self, argv: list[str]) -> str:
        if argv[0] == "systemctl":
            scope = "user" if "--user" in argv else "system"
            return self.failed[scope] if "--failed" in argv else f"× {argv[-1]} - échec\nmot_de_passe=secret123456"
        if argv[:2] == ["coredumpctl", "list"]:
            return self.crashes
        return "Stack trace of thread 1"


@pytest.fixture
def watcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(system, "PACMAN_LOG", tmp_path / "pacman.log")
    fake = FakeSystem()
    events: list = []
    w = system.SystemWatcher(
        SystemConfig(ignore_units=["bruyant.service"]), events.append, lambda key: events.append(("résolu", key)), fake
    )
    return w, fake, events


def test_failed_service_once_then_resolved(watcher) -> None:
    w, fake, events = watcher
    fake.failed["user"] = "sync.service loaded failed failed Synchro\nbruyant.service loaded failed failed X\n"
    w.check_services()
    w.check_services()  # toujours en échec : pas de deuxième alerte
    assert len(events) == 1
    problem = events[0]
    assert problem.kind == system.SERVICE and problem.title == "service utilisateur en échec : sync.service"
    assert "sync.service" in problem.details
    fake.failed["user"] = ""
    w.check_services()
    assert events[-1] == ("résolu", "service:user:sync.service")


def test_disk_alert_and_recovery(watcher, monkeypatch: pytest.MonkeyPatch) -> None:
    w, _fake, events = watcher
    usage = namedtuple("usage", "total used free")
    go = 1024**3
    state = {"free": 5 * go}
    monkeypatch.setattr(system.shutil, "disk_usage", lambda mount: usage(500 * go, 0, state["free"]))
    mounts = "/dev/nvme0n1p2 / ext4 rw 0 0\n"
    w.check_disks(mounts)
    w.check_disks(mounts)
    assert len(events) == 1 and events[0].title == "disque presque plein : / (5,0 Go libres)"
    state["free"] = 100 * go
    w.check_disks(mounts)
    assert events[-1] == ("résolu", "disk:/")


def test_update_errors_only_after_pacman_finished(watcher) -> None:
    w, _fake, events = watcher
    log = system.PACMAN_LOG
    log.write_text("[2026-10-22T18:00:00+0200] [ALPM] error: ancienne erreur\n")
    w.check_updates()  # au démarrage : seulement ce qui arrive ensuite
    with log.open("a") as f:
        f.write("\n".join(PACMAN[:4]) + "\n")
    w.check_updates()  # le fichier vient de changer : pacman écrit peut-être encore
    assert events == []
    w.check_updates()
    assert len(events) == 1
    assert events[0].title == "mise à jour : 1 erreur (pacman -Syu)"
    assert "symbol lookup error" in events[0].details and "ancienne" not in events[0].details
    w.check_updates()
    assert len(events) == 1


def test_crash_reported_once_per_program(watcher) -> None:
    w, fake, events = watcher
    since = w._crashes_since
    fake.crashes = json.dumps([
        {"time": since + 1, "pid": 10, "sig": 11, "exe": "/usr/bin/foot"},
        {"time": since + 2, "pid": 11, "sig": 11, "exe": "/usr/bin/foot"},
    ])
    w.check_crashes()
    w.check_crashes()
    assert [e.title for e in events] == ["foot a planté (SIGSEGV)"]
    assert events[0].key == f"crash:11:{since + 2}"


def test_disabled_sources_are_skipped(watcher) -> None:
    w, fake, events = watcher
    w.config = SystemConfig(services=False, disk=False, updates=False, crashes=False)
    fake.failed["system"] = "nginx.service loaded failed failed X\n"
    w.check()
    assert events == []
