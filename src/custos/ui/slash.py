"""Commandes « / » tapées dans la carte (sans GTK, testé).

`/h caté` rouvre une conversation passée, `/v …` pose une question avec le modèle rapide,
`/r` repose la dernière question, `/c` copie la dernière réponse, `/n` commence une nouvelle
conversation, `/g` l'ouvre en grand. S'y ajoutent les tiennes, écrites dans la section
`[commands]` de la config (`/traduis`, `/résume`…). Ce qui n'est pas une
commande connue (`/etc/fstab c'est quoi ?`) reste une question ordinaire.
"""

from collections.abc import Sequence
from dataclasses import dataclass

HISTORY, FAST, REPLAY, COPY, NEW, OPEN = "history", "fast", "replay", "copy", "new", "open"
CUSTOM = "custom"  # une commande de la config : sa consigne devient la question


@dataclass(frozen=True)
class Command:
    name: str
    alias: str  # vide pour les tiennes
    description: str
    action: str
    prompt: str = ""  # CUSTOM : la consigne envoyée à Claude
    fast: bool = False  # CUSTOM : avec le modèle rapide, comme `/vite`


COMMANDS = [
    Command("historique", "h", "rouvrir une conversation passée", HISTORY),
    Command("vite", "v", "cette question avec un modèle plus rapide et économe", FAST),
    Command("rejouer", "r", "reposer la dernière question", REPLAY),
    Command("copie", "c", "copier la dernière réponse", COPY),
    Command("nouveau", "n", "nouvelle conversation", NEW),
    Command("grand", "g", "ouvrir la conversation en grand", OPEN),
]


RESERVED = {word for command in COMMANDS for word in (command.name, command.alias)}


def custom(name: str, prompt: str, fast: bool = False) -> Command:
    """Une commande de la config (`traduis = "Traduis en français."`)."""
    return Command(name, "", prompt, CUSTOM, prompt=prompt, fast=fast)


def expand(command: Command, rest: str) -> str:
    """Question d'une commande de la config : sa consigne, puis ce qui est tapé après.

    La sélection et la capture sont jointes comme pour toute question : `/traduis` seul
    traduit la sélection, `/traduis good morning` ce texte-là.
    """
    return f"{command.prompt}\n\n{rest}" if rest else command.prompt


def parse(text: str, extra: Sequence[Command] = ()) -> tuple[Command, str] | None:
    """`/h caté` → (historique, « caté ») ; None si ce n'est pas une commande connue."""
    if not text.startswith("/"):
        return None
    word, _, rest = text[1:].partition(" ")
    if not word:
        return None
    for command in [*COMMANDS, *extra]:
        if word in (command.name, command.alias):
            return command, rest.strip()
    return None


def completions(text: str, extra: Sequence[Command] = ()) -> list[Command]:
    """Commandes qui commencent par ce qui est tapé (`/` seul : toutes)."""
    if not text.startswith("/") or " " in text:
        return []
    word = text[1:]
    return [
        c
        for c in [*COMMANDS, *extra]
        if c.name.startswith(word) or (c.alias and c.alias.startswith(word))
    ]
