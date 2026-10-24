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
        self.feed = "<rss/>"
        self.fetched = 0

    def fetch(self, url: str) -> str:
        self.fetched += 1
        if self.feed is None:
            raise OSError("hors ligne")
        return self.feed

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
    monkeypatch.setattr(system, "PACMAN_LOCK", tmp_path / "db.lck")
    monkeypatch.setattr(system, "MODULES_DIR", tmp_path / "modules")
    monkeypatch.setattr(system, "ETC", tmp_path / "etc")
    fake = FakeSystem()
    events: list = []
    w = system.SystemWatcher(
        SystemConfig(ignore_units=["bruyant.service"]),
        events.append,
        lambda key: events.append(("résolu", key)),
        fake,
        fetch=fake.fetch,
        memory=tmp_path / "system.json",
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
    w.config = SystemConfig(
        services=False, disk=False, updates=False, crashes=False, kernel=False, pacnew=False, news=False
    )
    fake.failed["system"] = "nginx.service loaded failed failed X\n"
    w.check()
    assert events == []


def test_kernel_missing(tmp_path: Path) -> None:
    modules = tmp_path / "modules"
    assert not system.kernel_missing("7.2.11-arch1-1", modules)  # pas de /usr/lib/modules : on ne sait pas
    (modules / "7.2.12-arch1-1").mkdir(parents=True)
    assert system.kernel_missing("7.2.11-arch1-1", modules)
    assert not system.kernel_missing("7.2.12-arch1-1", modules)


def test_kernel_alert_once_then_resolved(watcher) -> None:
    w, _fake, events = watcher
    system.PACMAN_LOG.write_text(
        "[2026-10-23T15:43:50+0200] [ALPM] upgraded linux-firmware (1-1 -> 2-1)\n"
        "[2026-10-23T15:43:51+0200] [ALPM] upgraded linux (7.2.11.arch1-1 -> 7.2.12.arch1-1)\n"
    )
    (system.MODULES_DIR / "7.2.12-arch1-1").mkdir(parents=True)
    system.PACMAN_LOCK.touch()
    w.check_kernel("7.2.11-arch1-1")
    assert events == []  # pacman travaille encore
    system.PACMAN_LOCK.unlink()
    w.check_kernel("7.2.11-arch1-1")
    w.check_kernel("7.2.11-arch1-1")
    assert len(events) == 1 and events[0].kind == system.KERNEL
    assert "7.2.12-arch1-1" in events[0].details and "upgraded linux (" in events[0].details
    assert "linux-firmware" not in events[0].details
    (system.MODULES_DIR / "7.2.11-arch1-1").mkdir()
    w.check_kernel("7.2.11-arch1-1")
    assert events[-1] == ("résolu", "kernel")


def test_pacnew_diff(tmp_path: Path) -> None:
    conf = tmp_path / "pacman.conf"
    conf.write_text("[options]\nColor\n")
    (tmp_path / "pacman.conf.pacnew").write_text("[options]\nColor\nParallelDownloads = 5\n")
    assert "+ParallelDownloads = 5" in system.pacnew_diff(tmp_path / "pacman.conf.pacnew")
    (tmp_path / "same.pacnew").write_text("x\n")
    (tmp_path / "same").write_text("x\n")
    assert "identique" in system.pacnew_diff(tmp_path / "same.pacnew")
    assert "illisible" in system.pacnew_diff(tmp_path / "absent.pacnew")


def test_pacnew_signaled_once_across_restarts(watcher) -> None:
    w, fake, events = watcher
    system.PACMAN_LOG.write_text("")
    etc = system.ETC
    (etc / "pacman.d").mkdir(parents=True)
    (etc / "pacman.d" / "mirrorlist").write_text("Server = a\n")
    (etc / "pacman.d" / "mirrorlist.pacnew").write_text("Server = b\n")
    w.check_pacnew()
    w.check_pacnew()
    assert [e.title for e in events] == ["1 fichier .pacnew à fusionner dans /etc"]
    assert "+Server = b" in events[0].details

    again: list = []  # Custos relancé : déjà signalé, pas de nouvelle alerte
    w2 = system.SystemWatcher(SystemConfig(), again.append, again.append, fake, memory=w._memory_path)
    w2.check_pacnew()
    assert again == []

    (etc / "locale.gen.pacnew").write_text("fr_FR.UTF-8\n")
    w.check_pacnew()
    assert events[-1].title == "2 fichiers .pacnew à fusionner dans /etc"
    for path in etc.rglob("*.pacnew"):
        path.unlink()
    w.check_pacnew()
    assert events[-1] == ("résolu", "pacnew")
    (etc / "pacman.d" / "mirrorlist.pacnew").write_text("Server = c\n")  # revenu à la mise à jour suivante
    w.check_pacnew()
    assert events[-1].kind == system.PACNEW


def test_pacnew_checked_after_an_update(watcher) -> None:
    w, _fake, _events = watcher
    system.PACMAN_LOG.write_text("")
    w.check()
    assert not w._pacman_changed
    with system.PACMAN_LOG.open("a") as f:
        f.write("\n".join(PACMAN[:4]) + "\n")
    w.check_updates()
    w.check_updates()
    assert w._pacman_changed


FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item><title>Intervention manuelle pour  glibc</title><link>https://archlinux.org/news/glibc/</link>
<description>&lt;p&gt;Lancez d&amp;#x27;abord &lt;code&gt;pacman -Syu glibc&lt;/code&gt;.&lt;/p&gt;</description>
<pubDate>Sat, 24 Oct 2026 09:00:00 +0000</pubDate><guid isPermaLink="false">tag:glibc</guid></item>
<item><title>Ancienne</title><link>https://archlinux.org/news/vieille/</link><description>déjà lue</description>
<pubDate>Wed, 21 Oct 2026 13:58:42 +0000</pubDate><guid isPermaLink="false">tag:vieille</guid></item>
<item><title>Sans date</title><pubDate>hier</pubDate></item>
</channel></rss>"""


def test_parse_news() -> None:
    news = system.parse_news(FEED)
    assert [n.guid for n in news] == ["tag:glibc", "tag:vieille"]
    assert news[0].title == "Intervention manuelle pour glibc"
    assert news[0].text == "Lancez d'abord pacman -Syu glibc."
    assert system.parse_news("pas du xml") == []


def test_last_full_upgrade() -> None:
    lines = [
        "[2026-10-22T23:24:02+0200] [PACMAN] starting full system upgrade",
        "[2026-10-23T15:43:44+0200] [PACMAN] starting full system upgrade",
        "[2026-10-23T16:00:00+0200] [PACMAN] Running 'pacman -S htop'",
    ]
    assert system.last_full_upgrade(lines).isoformat() == "2026-10-23T15:43:44+02:00"
    assert system.last_full_upgrade(lines[2:]) is None


def test_news_since_last_upgrade(watcher, monkeypatch: pytest.MonkeyPatch) -> None:
    w, fake, events = watcher
    system.PACMAN_LOG.write_text("[2026-10-23T15:43:44+0200] [PACMAN] starting full system upgrade\n")
    fake.feed = FEED
    w.check_news()
    w.check_news()  # relu seulement toutes les quelques heures
    assert fake.fetched == 1
    assert [e.title for e in events] == ["news Arch à lire avant de mettre à jour : Intervention manuelle pour glibc"]
    assert "https://archlinux.org/news/glibc/" in events[0].details

    again: list = []  # Custos relancé : déjà signalée
    w2 = system.SystemWatcher(SystemConfig(), again.append, again.append, fake, fetch=fake.fetch, memory=w._memory_path)
    w2.check_news()
    assert again == []

    with system.PACMAN_LOG.open("a") as f:  # mise à jour faite : l'alerte s'éteint
        f.write("[2026-10-24T12:00:00+0200] [PACMAN] starting full system upgrade\n")
    w.check_news()
    assert events[-1] == ("résolu", "news:tag:glibc")


def test_news_offline_retries_later(watcher, monkeypatch: pytest.MonkeyPatch) -> None:
    w, fake, events = watcher
    system.PACMAN_LOG.write_text("[2026-10-23T15:43:44+0200] [PACMAN] starting full system upgrade\n")
    fake.feed = None
    clock = [1000.0]
    monkeypatch.setattr(system.time, "monotonic", lambda: clock[0])
    w.check_news()
    w.check_news()
    assert fake.fetched == 1 and events == []
    fake.feed = FEED
    clock[0] += system.NEWS_RETRY + 1
    w.check_news()
    assert fake.fetched == 2 and len(events) == 1
