import gi
import pytest

from custos.ui.markdown import blocks, to_pango

ANSWER = "Utilise **df** :\n\n```bash\ndf -h\n```\n\n- `-h` : tailles lisibles\n- `/` : racine"


def test_code_block_is_monospace_without_language_line() -> None:
    markup = to_pango("```bash\ndf -h\n```")
    assert 'font_family="monospace"' in markup
    assert ">df -h</span>" in markup
    assert "bash" not in markup


def test_inline_styles() -> None:
    markup = to_pango("# Titre\n**gras** et `code`\n- un\n* deux")
    assert "<b>Titre</b>" in markup
    assert "<b>gras</b>" in markup
    assert ">code</span>" in markup
    assert "• un\n• deux" in markup


def test_special_characters_are_escaped() -> None:
    assert to_pango("a < b && c > d") == "a &lt; b &amp;&amp; c &gt; d"
    assert "&lt;stdio.h&gt;" in to_pango("```c\n#include <stdio.h>\n```")


def test_unclosed_block_while_streaming() -> None:
    markup = to_pango("Voici :\n```bash\nsudo pacman -Syu")
    assert ">sudo pacman -Syu</span>" in markup


def test_code_block_with_only_one_line_keeps_its_content() -> None:
    assert ">ls</span>" in to_pango("```ls```")


def test_blocks_split_text_and_code() -> None:
    assert blocks(ANSWER) == [
        ("text", "Utilise **df** :"),
        ("code", "df -h"),
        ("text", "- `-h` : tailles lisibles\n- `/` : racine"),
    ]


def test_blocks_while_streaming_and_without_code() -> None:
    assert blocks("Voici :\n```bash\nsudo pac") == [("text", "Voici :"), ("code", "sudo pac")]
    assert blocks("Juste du texte.") == [("text", "Juste du texte.")]
    assert blocks("") == []


def test_markup_is_valid_pango() -> None:
    gi.require_version("Pango", "1.0")
    from gi.repository import GLib, Pango

    for text in (ANSWER, "a < b", "```\n<tag>\n```", "**pas fermé", "```bash\nen cours"):
        try:
            Pango.parse_markup(to_pango(text), -1, "\0")
        except GLib.Error as exc:  # pragma: no cover
            pytest.fail(f"balisage invalide pour {text!r} : {exc}")
