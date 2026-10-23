"""Appels à Claude via `claude -p` (abonnement Claude, pas de clé API).

Claude n'est appelé que sur une action de l'utilisateur ; les réactions
automatiques de la mascotte restent locales.
"""

from collections.abc import Iterator

from custos.config import ClaudeConfig


def ask(prompt: str, config: ClaudeConfig, images: list[str] | None = None) -> Iterator[str]:
    """Envoie `prompt` à Claude et renvoie la réponse morceau par morceau."""
    raise NotImplementedError("claude.ask")
