import pytest

from custos.ui.markdown import blocks, runnable, table_markup, to_pango, valid

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
        ("command", "df -h"),
        ("text", "- `-h` : tailles lisibles\n- `/` : racine"),
    ]


def test_blocks_while_streaming_and_without_code() -> None:
    assert blocks("Voici :\n```bash\nsudo pac") == [("text", "Voici :"), ("command", "sudo pac")]
    assert blocks("Juste du texte.") == [("text", "Juste du texte.")]
    assert blocks("") == []


def test_markup_is_valid_pango() -> None:
    for text in (
        ANSWER, "a < b", "```\n<tag>\n```", "**pas fermé", "```bash\nen cours", LINKS, STYLES,
        "*pas fermé", "[lien](https://a.fr/?q=<x>&y=1)", "un * seul et 3 * 4",
    ):
        if not valid(to_pango(text)):  # pragma: no cover
            pytest.fail(f"balisage invalide pour {text!r}")


LINKS = "Voir [la doc](https://wiki.archlinux.org/title/Fstab), ou https://example.com/a_b_c."
STYLES = "*italique*, _aussi_, **gras**, ~~barré~~, et nom_de_fichier.py ou 2 * 3 * 4"


def test_links_are_clickable() -> None:
    markup = to_pango(LINKS)
    assert '<a href="https://wiki.archlinux.org/title/Fstab">la doc</a>' in markup
    # Adresse nue : cliquable, sans le point de la phrase, et ses _ ne deviennent pas de l'italique.
    assert '<a href="https://example.com/a_b_c">https://example.com/a_b_c</a>.' in markup


def test_link_address_is_escaped() -> None:
    assert 'href="https://a.fr/?q=1&amp;y=&quot;2&quot;"' in to_pango("[x](https://a.fr/?q=1&y=\"2\")")


def test_italic_bold_strike() -> None:
    markup = to_pango(STYLES)
    assert "<i>italique</i>" in markup and "<i>aussi</i>" in markup
    assert "<b>gras</b>" in markup and "<s>barré</s>" in markup
    assert "nom_de_fichier.py" in markup  # pas d'italique au milieu d'un mot
    assert "2 * 3 * 4" in markup  # une multiplication n'est pas de l'italique


def test_code_is_left_alone() -> None:
    assert "<i>" not in to_pango("`a *b* c` et `https://x.fr`") and "<a " not in to_pango("`https://x.fr`")


def test_numbered_list() -> None:
    assert to_pango("1. un\n2) deux") == "<b>1.</b> un\n<b>2.</b> deux"


TABLE = "| Commande | Effet |\n|---|--:|\n| `df -h` | disque |\n| `free` | mémoire vive |"


def test_blocks_split_tables() -> None:
    text = f"Voici :\n{TABLE}\nVoilà."
    assert blocks(text) == [("text", "Voici :"), ("table", TABLE), ("text", "Voilà.")]
    assert blocks("a | b sans tableau") == [("text", "a | b sans tableau")]


def test_table_is_aligned() -> None:
    lines = table_markup(TABLE).split("\n")
    assert lines[0] == "<b>Commande</b> │ <b>       Effet</b>"
    assert lines[1] == "─────────┼─────────────"
    assert lines[2].endswith(" │       disque")  # colonne alignée à droite (--:)
    assert lines[3].endswith(" │ mémoire vive")
    assert ">df -h</span>    │" in lines[2]  # le code compte pour sa longueur visible


def test_table_markup_is_valid_pango() -> None:
    assert valid(table_markup(TABLE + "\n| a < b | [x](https://x.fr) |"))
    assert not valid("<b>pas fermé")


def test_only_shell_blocks_are_commands() -> None:
    assert blocks("```fish\nset -Ux EDITOR nvim\n```") == [("command", "set -Ux EDITOR nvim")]
    assert blocks("```ini\n[Unit]\nAfter=x\n```") == [("code", "[Unit]\nAfter=x")]
    assert blocks("```\nlsblk -f\n```") == [("command", "lsblk -f")]  # une ligne sans langue
    assert blocks("```\nUUID=… / ext4\nUUID=… /home ext4\n```")[0][0] == "code"


def test_console_prompts_and_output_are_dropped() -> None:
    assert runnable("$ df -h\nSys. de fichiers  Taille\n$ free -h") == "df -h\nfree -h"
    assert runnable("df -h\n") == "df -h"
