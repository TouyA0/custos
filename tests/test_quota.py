import json
from datetime import datetime
from pathlib import Path

from custos import claude, quota

NOW = datetime(2026, 10, 24, 21, 50).timestamp()
FIVE = datetime(2026, 10, 24, 23, 30).timestamp()
SEVEN = datetime(2026, 10, 29, 9, 0).timestamp()
EVENT = {
    "status": "allowed",
    "unifiedWindows": {
        "seven_day": {"utilization": 0.45, "resetsAt": SEVEN},
        "five_hour": {"utilization": 0.63, "resetsAt": FIVE},
    },
}


def test_parse_rate_limit_event() -> None:
    line = json.dumps({"type": "rate_limit_event", "rate_limit_info": EVENT})
    assert claude.parse_rate_limit(line) == EVENT
    assert claude.parse_rate_limit(json.dumps({"type": "result"})) is None
    assert claude.parse_rate_limit("pas du json") is None


def test_describe_windows_in_order() -> None:
    current = quota.Quota.from_event(EVENT, NOW)
    assert current.lines(NOW) == ["Quota 5 h : 63 % · jusqu'à 23:30", "Quota 7 j : 45 % · jusqu'à jeu. 09:00"]
    assert current.describe(NOW) == "Quota 5 h : 63 % · jusqu'à 23:30 ; Quota 7 j : 45 % · jusqu'à jeu. 09:00"


def test_window_reset_since_and_tomorrow() -> None:
    current = quota.Quota.from_event(EVENT, NOW)
    later = datetime(2026, 10, 24, 23, 45).timestamp()
    assert current.lines(later)[:2] == ["Quota 5 h : remis à zéro", "Quota 7 j : 45 % · jusqu'à jeu. 09:00"]
    assert quota.when(datetime(2026, 10, 25, 3, 0).timestamp(), NOW) == "demain 03:00"


def test_suggests_vite_when_five_hours_almost_used() -> None:
    event = {**EVENT, "unifiedWindows": {"five_hour": {"utilization": 0.86, "resetsAt": FIVE}}}
    assert quota.Quota.from_event(event, NOW).lines(NOW)[-1] == "Presque au bout : /vite pour l'économiser"
    rejected = {**event, "status": "rejected"}
    assert quota.Quota.from_event(rejected, NOW).lines(NOW)[0] == "Quota atteint"


def test_event_without_usage() -> None:
    assert quota.Quota.from_event({"status": "allowed"}) is None
    assert quota.Quota.from_event({"unifiedWindows": {"five_hour": {"resetsAt": 1}}}) is None


def test_saved_between_restarts(tmp_path: Path) -> None:
    file = tmp_path / "quota.json"
    current = quota.Quota.from_event(EVENT, NOW)
    quota.save(current, file)
    assert quota.load(file) == current
    file.write_text("abîmé")
    assert quota.load(file) is None
