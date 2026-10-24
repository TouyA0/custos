from custos.ui import slash


def test_parse_known_commands_and_aliases() -> None:
    command, rest = slash.parse("/h caté anges")
    assert command.action == slash.HISTORY and rest == "caté anges"
    assert slash.parse("/historique")[0].action == slash.HISTORY
    assert slash.parse("/n")[0].action == slash.NEW
    assert slash.parse("/grand")[0].action == slash.OPEN


def test_paths_and_questions_are_not_commands() -> None:
    assert slash.parse("/etc/fstab c'est quoi ?") is None
    assert slash.parse("/home") is None
    assert slash.parse("salut") is None


def test_completions() -> None:
    assert len(slash.completions("/")) == len(slash.COMMANDS)
    assert [c.name for c in slash.completions("/hi")] == ["historique"]
    assert slash.completions("/hx") == []
    assert slash.completions("/h caté") == []


def test_fast_command() -> None:
    command, rest = slash.parse("/vite c'est quoi ls -la ?")
    assert command.action == slash.FAST and rest == "c'est quoi ls -la ?"
    assert slash.parse("/v")[0].action == slash.FAST


def test_replay_and_copy_commands() -> None:
    assert slash.parse("/r")[0].action == slash.REPLAY
    assert slash.parse("/rejouer")[0].action == slash.REPLAY
    assert slash.parse("/c")[0].action == slash.COPY
    assert slash.parse("/copie")[0].action == slash.COPY
    assert [c.name for c in slash.completions("/c")] == ["copie"]
