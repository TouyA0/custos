import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from custos import history


def line(kind: str, content, **extra) -> str:
    return json.dumps({"type": kind, "message": {"content": content}, **extra}) + "\n"


@pytest.fixture
def sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(history.Path, "home", lambda: tmp_path)
    folder = history.sessions_dir(Path("/home/q/.local/state/custos/conversations"))
    folder.mkdir(parents=True)
    return folder


def write(folder: Path, name: str, lines: list[str], mtime: float) -> None:
    path = folder / f"{name}.jsonl"
    path.write_text("".join(lines))
    os.utime(path, (mtime, mtime))


WORKDIR = Path("/home/q/.local/state/custos/conversations")


def test_sessions_dir_is_encoded_like_claude_code() -> None:
    assert history.sessions_dir(WORKDIR).name == "-home-q--local-state-custos-conversations"


def test_reads_questions_and_final_answer(sessions: Path) -> None:
    write(sessions, "a", [
        json.dumps({"type": "queue-operation"}) + "\n",
        line("user", [{"type": "text", "text": "Fenêtre active : foot\n\nQuestion : pourquoi ça crashe ?"}]),
        line("assistant", [{"type": "text", "text": "Je regarde."}, {"type": "tool_use", "name": "Bash"}]),
        line("user", [{"type": "tool_result", "content": "..."}]),
        line("assistant", [{"type": "text", "text": "C'est le PSR."}]),
        line("user", "et comment je règle ?"),
        line("assistant", [{"type": "text", "text": "i915.enable_psr=0"}]),
        line("assistant", [{"type": "text", "text": "sous-agent"}], isSidechain=True),
    ], 1000)
    [conversation] = history.conversations(WORKDIR)
    assert conversation.title == "pourquoi ça crashe ?"
    assert conversation.last_question == "et comment je règle ?"
    assert conversation.last_answer == "i915.enable_psr=0"


def test_newest_first_filter_and_unanswered_skipped(sessions: Path) -> None:
    write(sessions, "old", [line("user", "le catéchisme"), line("assistant", [{"type": "text", "text": "ok"}])], 100)
    write(sessions, "new", [line("user", "spotify"), line("assistant", [{"type": "text", "text": "yay"}])], 200)
    write(sessions, "empty", [line("user", "interrompue")], 300)
    assert [c.session for c in history.conversations(WORKDIR)] == ["new", "old"]
    assert [c.session for c in history.search("CATÉ", workdir=WORKDIR)] == ["old"]
    assert history.find("new", WORKDIR).title == "spotify"


def test_when() -> None:
    now = datetime(2026, 10, 24, 12, 0).timestamp()
    assert history.when(datetime(2026, 10, 24, 9, 10).timestamp(), now) == "09:10"
    assert history.when(datetime(2026, 10, 23, 23, 33).timestamp(), now) == "hier 23:33"
    assert history.when(datetime(2026, 10, 22, 19, 54).timestamp(), now) == "jeu. 19:54"
    assert history.when(datetime(2026, 10, 1, 8, 0).timestamp(), now) == "01/10"
