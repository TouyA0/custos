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


def test_custom_commands() -> None:
    traduis = slash.custom("traduis", "Traduis en français.")
    résume = slash.custom("résume", "Résume en trois points.", fast=True)
    custom = [traduis, résume]
    command, rest = slash.parse("/traduis good morning", custom)
    assert command is traduis and rest == "good morning"
    assert slash.parse("/résume", custom)[0].fast is True
    assert slash.parse("/traduis") is None  # sans la config : une question ordinaire
    assert [c.name for c in slash.completions("/r", custom)] == ["rejouer", "rappel", "résume"]
    assert slash.completions("/", custom)[-2:] == custom


def test_expand_appends_what_is_typed() -> None:
    traduis = slash.custom("traduis", "Traduis en français.")
    assert slash.expand(traduis, "") == "Traduis en français."
    assert slash.expand(traduis, "good morning") == "Traduis en français.\n\ngood morning"


def test_a_lone_slash_is_not_a_command() -> None:
    assert slash.parse("/", [slash.custom("traduis", "Traduis.")]) is None
