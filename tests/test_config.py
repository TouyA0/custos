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
