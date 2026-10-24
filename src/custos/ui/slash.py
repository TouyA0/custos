"""Commandes « / » tapées dans la carte (sans GTK, testé).

`/h caté` rouvre une conversation passée, `/v …` pose une question avec le modèle rapide,
`/n` commence une nouvelle conversation, `/g` l'ouvre en grand. Ce qui n'est pas une
commande connue (`/etc/fstab c'est quoi ?`) reste une question ordinaire.
"""

from dataclasses import dataclass

HISTORY, FAST, NEW, OPEN = "history", "fast", "new", "open"


@dataclass(frozen=True)
class Command:
    name: str
    alias: str
    description: str
    action: str


COMMANDS = [
    Command("historique", "h", "rouvrir une conversation passée", HISTORY),
    Command("vite", "v", "cette question avec un modèle plus rapide et économe", FAST),
    Command("nouveau", "n", "nouvelle conversation", NEW),
    Command("grand", "g", "ouvrir la conversation en grand", OPEN),
]


def parse(text: str) -> tuple[Command, str] | None:
    """`/h caté` → (historique, « caté ») ; None si ce n'est pas une commande connue."""
    if not text.startswith("/"):
        return None
    word, _, rest = text[1:].partition(" ")
    for command in COMMANDS:
        if word in (command.name, command.alias):
            return command, rest.strip()
    return None


def completions(text: str) -> list[Command]:
    """Commandes qui commencent par ce qui est tapé (`/` seul : toutes)."""
    if not text.startswith("/") or " " in text:
        return []
    word = text[1:]
    return [c for c in COMMANDS if c.name.startswith(word) or c.alias.startswith(word)]
