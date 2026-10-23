"""Chargement de la configuration depuis ~/.config/custos/config.toml."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "custos" / "config.toml"


@dataclass
class ClaudeConfig:
    model: str = "claude-opus-5-5"
    timeout: int = 120


@dataclass
class MascotConfig:
    position: str = "bottom-right"
    margin: int = 24
    size: int = 96
    sleep_after: int = 300
    outline: bool = True
    sprites_dir: str = ""


@dataclass
class PrivacyConfig:
    blocked_classes: list[str] = field(default_factory=list)


@dataclass
class ShellConfig:
    react_on_failure: bool = True


@dataclass
class Config:
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    mascot: MascotConfig = field(default_factory=MascotConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)


def load(path: Path | None = None) -> Config:
    """Lit le fichier de config ; les valeurs absentes gardent leur défaut."""
    path = path or config_path()
    if not path.exists():
        return Config()

    with path.open("rb") as f:
        data = tomllib.load(f)

    return Config(
        claude=ClaudeConfig(**data.get("claude", {})),
        mascot=MascotConfig(**data.get("mascot", {})),
        privacy=PrivacyConfig(**data.get("privacy", {})),
        shell=ShellConfig(**data.get("shell", {})),
    )
