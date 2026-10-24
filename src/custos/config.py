"""Chargement de la configuration depuis ~/.config/custos/config.toml."""

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "custos" / "config.toml"


MAX_PERSONA = 4_000  # caractères : il est envoyé avec chaque question


def persona_path() -> Path:
    """custos.md : son caractère et ce qu'il doit savoir de toi, écrit par toi."""
    return config_path().parent / "custos.md"


def load_persona(path: Path | None = None) -> str:
    """Contenu de custos.md, sans les commentaires <!-- … --> (les explications du modèle)."""
    try:
        text = (path or persona_path()).read_text()
    except OSError:
        return ""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    return text[:MAX_PERSONA]


@dataclass
class ClaudeConfig:
    model: str = "claude-opus-5-5"
    fast_model: str = "claude-sonnet-5-5"  # `/vite …` dans la carte : pour cette question seulement
    timeout: int = 120
    # Custos agit sur ton PC avec les outils de Claude Code (mode auto) ; false : il répond seulement.
    tools: bool = True
    agent_timeout: int = 1800  # secondes : une vraie tâche peut être longue
    # Ouvrir la conversation en grand (bouton ↗ de la bulle) : {session}, {cwd}, {model}
    open_command: str = "foot --working-directory={cwd} claude --resume {session} --model {model}"


@dataclass
class MascotConfig:
    position: str = "bottom-right"
    margin: int = 24
    size: int = 96
    sleep_after: int = 300
    hide_in_fullscreen: bool = True
    tray: bool = True
    outline: bool = True
    sprites_dir: str = ""


@dataclass
class PrivacyConfig:
    blocked_classes: list[str] = field(default_factory=list)
    blocked_titles: list[str] = field(default_factory=list)  # motifs à jokers : "*Banque*"


@dataclass
class ShellConfig:
    react_on_failure: bool = True
    # Joint la sortie de la commande en échec (foot : voir integrations/terminal/foot.ini).
    capture_output: bool = False


@dataclass
class AgentsConfig:
    # Badges pour les sessions Claude Code (hooks : integrations/claude-code/settings.json).
    enabled: bool = True


@dataclass
class NoticeConfig:
    selection: bool = True
    images: bool = True
    screenshots: bool = True
    screenshot_glob: str = "/tmp/caelestia-picker-*.png"
    duration: int = 10


@dataclass
class Config:
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    mascot: MascotConfig = field(default_factory=MascotConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    notice: NoticeConfig = field(default_factory=NoticeConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)


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
        notice=NoticeConfig(**data.get("notice", {})),
        agents=AgentsConfig(**data.get("agents", {})),
    )
