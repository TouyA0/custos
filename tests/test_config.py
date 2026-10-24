from pathlib import Path

from custos import config


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    cfg = config.load(tmp_path / "absent.toml")
    assert cfg.claude.model == "claude-opus-5-5"
    assert cfg.shell.react_on_failure is True


def test_example_config_loads() -> None:
    example = Path(__file__).parent.parent / "config" / "config.example.toml"
    cfg = config.load(example)
    assert cfg.mascot.position == "bottom-right"
    assert "Bitwarden" in cfg.privacy.blocked_classes


def test_partial_file_keeps_other_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[mascot]\nsize = 128\n')
    cfg = config.load(path)
    assert cfg.mascot.size == 128
    assert cfg.mascot.margin == 24
    assert cfg.claude.timeout == 120


def test_persona_without_comments_and_capped(tmp_path: Path) -> None:
    from custos.config import MAX_PERSONA, load_persona

    path = tmp_path / "custos.md"
    assert load_persona(path) == ""
    path.write_text("<!-- explication\nsur deux lignes -->\nTu tutoies.\n")
    assert load_persona(path) == "Tu tutoies."
    path.write_text("x" * (MAX_PERSONA + 50))
    assert len(load_persona(path)) == MAX_PERSONA


def test_unknown_or_invalid_options_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'claude = "opus"\n'
        '[mascot]\nsize = 128\nsise = 64\nmargin = "grand"\noutline = 1\n'
        '[privacy]\nblocked_classes = ["KeePassXC", 3]\n'
        '[mascotte]\nsize = 10\n'
    )
    cfg = config.load(path)
    assert cfg.mascot.size == 128
    assert cfg.mascot.margin == 24 and cfg.mascot.outline is True
    assert cfg.privacy.blocked_classes == []
    assert cfg.claude.model == "claude-opus-5-5"
    assert len(cfg.problems) == 6
    assert any("sise" in p for p in cfg.problems)
    assert any("[mascotte]" in p for p in cfg.problems)


def test_broken_toml_gives_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[mascot\nsize = \n")
    cfg = config.load(path)
    assert cfg.mascot.size == 96
    assert cfg.problems and "illisible" in cfg.problems[0]


def test_valid_file_has_no_problems() -> None:
    example = Path(__file__).parent.parent / "config" / "config.example.toml"
    assert config.load(example).problems == []


def test_custom_commands(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[commands]\n'
        'traduis = "Traduis en français."\n'
        '"résume" = { prompt = "  Résume.  ", fast = true }\n'
        'h = "Pris par /h"\n'
        '"deux mots" = "Ignorée"\n'
        'vide = ""\n'
        'nombre = 3\n'
        'lente = { prompt = "Lente.", fast = "oui", couleur = "bleu" }\n'
    )
    cfg = config.load(path)
    assert cfg.commands == {
        "traduis": config.CommandConfig("Traduis en français."),
        "résume": config.CommandConfig("Résume.", fast=True),
        "lente": config.CommandConfig("Lente."),
    }
    assert len(cfg.problems) == 6
    assert any("déjà une commande" in p for p in cfg.problems)
    assert any("deux mots" in p for p in cfg.problems)
    assert any("lente.couleur" in p for p in cfg.problems)


def test_example_config_has_commands() -> None:
    example = Path(__file__).parent.parent / "config" / "config.example.toml"
    assert "traduis" in config.load(example).commands
