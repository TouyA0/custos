"""Conversion du Markdown des réponses de Claude en balisage Pango (GTK).

Volontairement minimal : blocs de code, `code`, **gras**, titres et listes.
Pendant le streaming, un bloc de code pas encore refermé est affiché comme du code.
"""

import html
import re

CODE_BG = "#0f1526"
CODE_FG = "#efe9df"
INLINE_CODE_FG = "#f6c453"

_FENCE_LANG = re.compile(r"[\w+#.-]*")


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _split_code(part: str) -> str:
    """Retire la ligne de langage (```bash) et les retours à la ligne autour du code."""
    first, _, rest = part.partition("\n")
    if rest and _FENCE_LANG.fullmatch(first.strip()):
        part = rest
    return part.strip("\n")


def _inline(text: str) -> str:
    text = _escape(text)
    text = re.sub(
        r"`([^`\n]+)`",
        rf'<span font_family="monospace" foreground="{INLINE_CODE_FG}">\1</span>',
        text,
    )
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?m)^#{1,6} +(.+)$", r"<b>\1</b>", text)
    return re.sub(r"(?m)^(\s*)[-*] ", r"\1• ", text)


def to_pango(text: str) -> str:
    parts = text.split("```")
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            code = _escape(_split_code(part))
            out.append(
                f'<span font_family="monospace" background="{CODE_BG}" '
                f'foreground="{CODE_FG}">{code}</span>'
            )
        else:
            out.append(_inline(part))
    return "".join(out)


def code_blocks(text: str) -> list[str]:
    """Blocs de code complets (refermés) de la réponse."""
    parts = text.split("```")
    return [_split_code(part) for part in parts[1:-1:2]]
