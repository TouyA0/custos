"""Markdown des réponses de Claude : découpage en texte / blocs de code, et balisage Pango.

Volontairement minimal : blocs de code, `code`, **gras**, titres et listes.
Pendant le streaming, un bloc de code pas encore refermé est traité comme du code.
"""

import html
import re

# Couleurs du code par défaut (thème clair) ; la carte passe celles de son thème.
CODE_BG = "#e4e6eb"
CODE_FG = "#1a1f2b"

_FENCE_LANG = re.compile(r"[\w+#.-]*")


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _split_code(part: str) -> str:
    """Retire la ligne de langage (```bash) et les retours à la ligne autour du code."""
    first, _, rest = part.partition("\n")
    if rest and _FENCE_LANG.fullmatch(first.strip()):
        part = rest
    return part.strip("\n")


def _inline(text: str, code_fg: str, code_bg: str) -> str:
    text = _escape(text)
    text = re.sub(
        r"`([^`\n]+)`",
        rf'<span font_family="monospace" foreground="{code_fg}" background="{code_bg}">\1</span>',
        text,
    )
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?m)^#{1,6} +(.+)$", r"<b>\1</b>", text)
    return re.sub(r"(?m)^(\s*)[-*] ", r"\1• ", text)


def to_pango(text: str, code_fg: str = CODE_FG, code_bg: str = CODE_BG) -> str:
    parts = text.split("```")
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            code = _escape(_split_code(part))
            out.append(
                f'<span font_family="monospace" background="{code_bg}" '
                f'foreground="{code_fg}">{code}</span>'
            )
        else:
            out.append(_inline(part, code_fg, code_bg))
    return "".join(out)


def blocks(text: str) -> list[tuple[str, str]]:
    """Découpe une réponse en morceaux ("text", markdown) et ("code", code brut)."""
    out = []
    for i, part in enumerate(text.split("```")):
        if i % 2:
            out.append(("code", _split_code(part)))
        elif part.strip():
            out.append(("text", part.strip("\n")))
    return out
