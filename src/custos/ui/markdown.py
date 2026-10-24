"""Markdown des réponses de Claude : découpage en texte / code / tableaux, et balisage Pango.

Volontairement minimal : blocs de code, `code`, **gras**, *italique*, ~~barré~~, liens,
titres, listes (à puces et numérotées) et tableaux. Pendant le streaming, un bloc de code
pas encore refermé est traité comme du code.
"""

import html
import re

# Couleurs du code par défaut (thème clair) ; la carte passe celles de son thème.
CODE_BG = "#e4e6eb"
CODE_FG = "#1a1f2b"

_FENCE_LANG = re.compile(r"[\w+#.-]*")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_URL = re.compile(r"<?(https?://[^\s<>\"'`]+)>?")
_URL_END = ".,;:!?»'\""  # ponctuation de la phrase, pas de l'adresse
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_TAG = re.compile(r"<[^>]+>")


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _split_code(part: str) -> str:
    """Retire la ligne de langage (```bash) et les retours à la ligne autour du code."""
    first, _, rest = part.partition("\n")
    if rest and _FENCE_LANG.fullmatch(first.strip()):
        part = rest
    return part.strip("\n")


def _link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{_escape(label)}</a>'


def _bare_url(match: re.Match, keep) -> str:
    """Adresse écrite telle quelle : sans la ponctuation qui la suit dans la phrase."""
    url = match[1]
    end = ""
    while url and (url[-1] in _URL_END or (url[-1] == ")" and "(" not in url)):
        url, end = url[:-1], url[-1] + end
    return keep(_link(url, url)) + end


def _inline(text: str, code_fg: str, code_bg: str) -> str:
    # Code et liens d'abord, mis de côté : le reste des règles ne doit pas y toucher
    # (un `*` dans du code, un `_` dans une adresse).
    kept: list[str] = []

    def keep(markup: str) -> str:
        kept.append(markup)
        return f"\0{len(kept) - 1}\0"

    text = re.sub(
        r"`([^`\n]+)`",
        lambda m: keep(
            f'<span font_family="monospace" foreground="{code_fg}" background="{code_bg}">'
            f"{_escape(m[1])}</span>"
        ),
        text,
    )
    text = _LINK.sub(lambda m: keep(_link(m[2], m[1])), text)
    text = _URL.sub(lambda m: _bare_url(m, keep), text)

    text = _escape(text)
    text = re.sub(r"(?m)^#{1,6} +(.+)$", r"<b>\1</b>", text)
    text = re.sub(r"(?m)^(\s*)[-*+] ", r"\1• ", text)
    text = re.sub(r"(?m)^(\s*)(\d+)[.)] ", r"\1<b>\2.</b> ", text)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", r"<i>\1</i>", text)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", text)
    return re.sub("\0(\\d+)\0", lambda m: kept[int(m[1])], text)


def valid(markup: str) -> bool:
    """Balisage accepté par un label GTK : celui de Pango, plus les liens <a href>, que le
    label gère lui-même (Pango, lui, ne les connaît pas)."""
    import gi

    gi.require_version("Pango", "1.0")
    from gi.repository import GLib, Pango

    try:
        Pango.parse_markup(re.sub(r"</?a\b[^>]*>", "", markup), -1, "\0")
    except GLib.Error:
        return False
    return True


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


# --- tableaux -----------------------------------------------------------------------


def _cells(line: str) -> list[str]:
    line = line.strip()
    line = line.removeprefix("|")
    line = line[:-1] if line.endswith("|") and not line.endswith("\\|") else line
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line)]


def _split_tables(text: str) -> list[tuple[str, str]]:
    """Morceaux ("text", …) et ("table", …) d'un texte : un tableau est une ligne d'en-tête
    suivie d'une ligne de tirets (`|---|---|`), puis ses lignes qui contiennent des `|`."""
    lines = text.split("\n")
    out: list[tuple[str, str]] = []
    plain: list[str] = []
    i = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
            end = i + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            if "\n".join(plain).strip():
                out.append(("text", "\n".join(plain).strip("\n")))
            plain = []
            out.append(("table", "\n".join(lines[i:end])))
            i = end
        else:
            plain.append(lines[i])
            i += 1
    if "\n".join(plain).strip():
        out.append(("text", "\n".join(plain).strip("\n")))
    return out


def table_markup(table: str, code_fg: str = CODE_FG, code_bg: str = CODE_BG) -> str:
    """Tableau aligné en colonnes (la carte est en police à chasse fixe) : en-tête en gras,
    trait dessous, colonnes séparées par │, alignement des `:--:` respecté."""
    lines = table.split("\n")
    rule = _cells(lines[1])
    rows = [_cells(line) for i, line in enumerate(lines) if i != 1]
    columns = max(len(row) for row in rows)
    rows = [row + [""] * (columns - len(row)) for row in rows]
    rendered = [[_inline(cell.replace("\\|", "|"), code_fg, code_bg) for cell in row] for row in rows]
    widths = [
        max(len(html.unescape(_TAG.sub("", row[c]))) for row in rendered) for c in range(columns)
    ]
    align = [
        (rule[c].startswith(":") and rule[c].endswith(":")) and "center"
        or rule[c].endswith(":") and "right"
        or "left"
        if c < len(rule)
        else "left"
        for c in range(columns)
    ]

    def pad(markup: str, c: int) -> str:
        space = widths[c] - len(html.unescape(_TAG.sub("", markup)))
        if align[c] == "right":
            return " " * space + markup
        if align[c] == "center":
            return " " * (space // 2) + markup + " " * (space - space // 2)
        return markup + " " * space

    out = []
    for r, row in enumerate(rendered):
        cells = [pad(cell, c) for c, cell in enumerate(row)]
        out.append(" │ ".join(f"<b>{cell}</b>" for cell in cells) if r == 0 else " │ ".join(cells))
        if r == 0:
            out.append("─┼─".join("─" * width for width in widths))
    return "\n".join(out)


def blocks(text: str) -> list[tuple[str, str]]:
    """Découpe une réponse en morceaux ("text", markdown), ("code", code brut) et
    ("table", tableau markdown)."""
    out = []
    for i, part in enumerate(text.split("```")):
        if i % 2:
            out.append(("code", _split_code(part)))
        elif part.strip():
            out.extend(_split_tables(part.strip("\n")))
    return out
