"""Filtrage avant envoi : fenêtres bloquées et secrets (clés, tokens)."""

import re
from fnmatch import fnmatchcase

from custos.config import PrivacyConfig

MASK = "[secret masqué]"

SECRET_PATTERNS = [
    # Blocs de clé privée (SSH, PGP, TLS…)
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY( BLOCK)?-----.*?-----END [A-Z ]*PRIVATE KEY( BLOCK)?-----", re.DOTALL),
    # Clés d'API connues
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),           # Anthropic
    re.compile(r"\bsk-(proj-)?[A-Za-z0-9_-]{20,}"),       # OpenAI
    re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),  # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),            # GitLab
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),        # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                  # AWS
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),             # Google
    # JWT
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    # Identifiants dans une URL : scheme://user:motdepasse@hôte
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
]

# `password = xxx`, `API_KEY: "xxx"`, `--token xxx`… : on garde le nom, on masque la valeur.
# Un espace seul ne compte comme séparateur qu'après une option (`--token xxx`),
# pour ne pas masquer une phrase comme « le password est expiré ».
_NAME = r"[\w-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey)[\w-]*"
_VALUE = r"([\"']?)(?!\[secret)([^\s\"']{6,})\3"
ASSIGNMENT = re.compile(rf"(?i)\b({_NAME})(\s*[:=]\s*)" + _VALUE)
CLI_FLAG = re.compile(rf"(?i)(?<!\S)(--?{_NAME})(\s+)" + _VALUE)


def is_blocked(window_class: str, title: str, config: PrivacyConfig) -> bool:
    """Fenêtre dont Custos ne lit rien : appli entière (classe) ou page précise (titre).

    Les motifs de titre sont des jokers (`*Banque*`), sans tenir compte de la casse :
    utile pour le navigateur, où toutes les fenêtres ont la même classe.
    """
    if window_class in config.blocked_classes:
        return True
    title = title.casefold()
    return any(fnmatchcase(title, pattern.casefold()) for pattern in config.blocked_titles)


def redact_secrets(text: str) -> str:
    """Masque ce qui ressemble à une clé ou un token avant l'envoi à Claude."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(MASK, text)
    for pattern in (ASSIGNMENT, CLI_FLAG):
        text = pattern.sub(lambda m: f"{m[1]}{m[2]}{m[3]}{MASK}{m[3]}", text)
    return text
