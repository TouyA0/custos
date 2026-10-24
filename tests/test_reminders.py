from datetime import datetime
from pathlib import Path

import pytest

from custos import cli, ipc, reminders

NOW = datetime(2026, 10, 24, 15, 0)


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        ("20m", "2026-10-24 15:20"),
        ("20 min", "2026-10-24 15:20"),
        ("45", "2026-10-24 15:45"),
        ("dans 10", "2026-10-24 15:10"),
        ("1h30", "2026-10-24 16:30"),  # sans « à » : une durée
        ("1h30m", "2026-10-24 16:30"),
        ("dans 2h", "2026-10-24 17:00"),
        ("2 heures", "2026-10-24 17:00"),
        ("1h 5m", "2026-10-24 16:05"),
        ("15:30", "2026-10-24 15:30"),
        ("à 15h30", "2026-10-24 15:30"),
        ("8:00", "2026-10-25 08:00"),  # déjà passée : demain
        ("a 8h", "2026-10-25 08:00"),
        ("demain 9h", "2026-10-25 09:00"),
        ("Demain à 9:00", "2026-10-25 09:00"),
        ("2026-10-30T09:00", "2026-10-30 09:00"),
    ],
)
def test_parse_when(when: str, expected: str) -> None:
    assert f"{reminders.parse_when(when, NOW):%Y-%m-%d %H:%M}" == expected


def test_seconds() -> None:
    assert reminders.parse_when("90s", NOW) == datetime(2026, 10, 24, 15, 1, 30)
    assert reminders.parse_when("1h30s", NOW) == datetime(2026, 10, 24, 16, 0, 30)


@pytest.mark.parametrize("when", ["", "bof", "à 25h", "demain", "2026-10-01T09:00", "0", "2030-01-01T00:00"])
def test_parse_when_errors(when: str) -> None:
    with pytest.raises(reminders.WhenError):
        reminders.parse_when(when, NOW)


def test_split() -> None:
    assert reminders.split("20m sortir le linge") == ("20m", "sortir le linge")
    assert reminders.split("demain à 9h appeler maman") == ("demain à 9h", "appeler maman")
    assert reminders.split("dans 1h30 thé") == ("dans 1h30", "thé")
    assert reminders.split("demain") == ("demain", "")


def test_create() -> None:
    reminder = reminders.create("20m", "  sortir   le linge ", NOW)
    assert reminder.text == "sortir le linge" and reminder.at == datetime(2026, 10, 24, 15, 20).timestamp()
    with pytest.raises(reminders.WhenError, match="De quoi"):
        reminders.create("20m", " ", NOW)


def test_describe() -> None:
    def at(*args: int) -> float:
        return datetime(*args).timestamp()

    assert reminders.describe(at(2026, 10, 24, 15, 20), NOW) == "à 15:20 (dans 20 min)"
    assert reminders.describe(at(2026, 10, 24, 17, 5), NOW) == "à 17:05 (dans 2 h 05)"
    assert reminders.describe(at(2026, 10, 24, 17, 0), NOW) == "à 17:00 (dans 2 h)"
    assert reminders.describe(at(2026, 10, 25, 9, 0), NOW) == "demain à 09:00"
    assert reminders.describe(at(2026, 10, 30, 9, 0), NOW) == "le 30/10 à 09:00"


def test_store_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "reminders.json"
    assert reminders.load(path) == []
    later, sooner = reminders.Reminder("b", 200.0, "plus tard"), reminders.Reminder("a", 100.0, "bientôt")
    reminders.save([later, sooner], path)
    assert reminders.load(path) == [sooner, later]
    path.write_text('[{"id": "x"}, {"id": "a", "at": 1, "text": "ok"}]')
    assert reminders.load(path) == [reminders.Reminder("a", 1.0, "ok")]
    path.write_text("abîmé")
    assert reminders.load(path) == []


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict]:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    messages: list[dict] = []
    monkeypatch.setattr(ipc, "send", messages.append)
    return messages


def test_cli_remind(sent: list[dict], capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["remind", "20m", "sortir le linge"]) == 0
    message = sent[-1]
    assert message["type"] == "remind" and message["text"] == "sortir le linge"
    out = capsys.readouterr().out
    assert out.startswith("Rappel programmé à ") and f"(identifiant {message['id']})" in out
    assert cli.main(["remind", "demain", "à", "9h", "appeler", "maman"]) == 0
    assert sent[-1]["text"] == "appeler maman"
    assert cli.main(["remind", "bof", "rien"]) == 1
    assert "Je ne comprends pas" in capsys.readouterr().out and len(sent) == 2


def test_cli_remind_list_and_cancel(sent: list[dict], capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["remind", "--list"]) == 0
    assert capsys.readouterr().out == "Aucun rappel en attente.\n"
    reminder = reminders.create("1h", "thé")
    reminders.save([reminder])  # ce qu'aurait fait le daemon
    cli.main(["remind"])
    out = capsys.readouterr().out
    assert out.startswith(f"{reminder.id}  ") and out.endswith("  thé\n")
    assert cli.main(["remind", "--cancel", "inconnu"]) == 1 and sent == []
    assert cli.main(["remind", "--cancel", reminder.id]) == 0
    assert sent == [{"type": "remind-cancel", "id": reminder.id}]
