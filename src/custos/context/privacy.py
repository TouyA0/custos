"""Filtrage avant envoi : fenêtres bloquées et secrets (clés, tokens)."""


def is_blocked(window_class: str, blocked_classes: list[str]) -> bool:
    return window_class in blocked_classes


def redact_secrets(text: str) -> str:
    """Masque ce qui ressemble à une clé ou un token avant l'envoi à Claude."""
    raise NotImplementedError("context.privacy.redact_secrets")
