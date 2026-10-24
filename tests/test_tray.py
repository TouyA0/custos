from pathlib import Path

from gi.repository import GLib
from PIL import Image

from custos.ui import tray


def test_icon_is_converted_to_argb_big_endian(tmp_path: Path) -> None:
    icon = tmp_path / "icon.png"
    Image.new("RGBA", (64, 64), (10, 20, 30, 200)).save(icon)
    pixmaps = tray.argb_pixmaps(icon)
    assert [(w, h) for w, h, _ in pixmaps] == [(s, s) for s in tray.ICON_SIZES]
    width, height, data = pixmaps[0]
    assert len(data) == width * height * 4
    assert data[:4] == bytes((200, 10, 20, 30))  # A, R, G, B


def test_project_icon_exists() -> None:
    from custos.ui.overlay import DEFAULT_ICON

    assert DEFAULT_ICON.exists()


def test_alert_icons_have_a_colored_dot(tmp_path: Path) -> None:
    icon = tmp_path / "icon.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(icon)
    plain = tray.argb_pixmaps(icon)[-1]
    waiting = tray.argb_pixmaps(icon, tray.WAITING)[-1]
    size = waiting[0]
    radius = size * tray.DOT_RADIUS
    x, y = int(size - radius), int(radius)  # centre de la pastille, en haut à droite
    at = (y * size + x) * 4
    assert waiting[2][at : at + 4] == bytes((255, *tray.DOT_COLORS[tray.WAITING]))
    assert plain[2][at : at + 4] == bytes(4)
    corner = (size * 4) * (size - 1)  # en bas à gauche : intact
    assert waiting[2][corner : corner + 4] == plain[2][corner : corner + 4]


def test_icon_files_one_name_per_state(tmp_path: Path) -> None:
    icon = tmp_path / "icon.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(icon)
    folder = tmp_path / "icons"
    tray.write_icon_files(icon, folder)
    for name in tray.ICON_NAMES.values():
        assert (folder / f"{name}.png").exists() and (folder / name).exists()
    done = Image.open(folder / "custos-tray-done.png").convert("RGBA")
    radius = 64 * tray.DOT_RADIUS
    assert done.getpixel((int(64 - radius), int(radius))) == (*tray.DOT_COLORS[tray.DONE], 255)
    assert Image.open(folder / "custos-tray.png").convert("RGBA").getpixel((int(64 - radius), int(radius)))[3] == 0


def test_menu_layout_and_actions() -> None:
    clicked: list[str] = []
    entries = [
        ("Poser une question", lambda: clicked.append("question")),
        ("Conversations récentes", [("09:10 · fstab", lambda: clicked.append("fstab"))]),
        None,
        ("Sessions Claude Code", None),
        ("✋ mon_projet", lambda: clicked.append("projet")),
    ]
    menu = tray.Menu(lambda: entries)
    assert menu.rebuild() and menu.revision == 1
    number, props, children = GLib.Variant("(ia{sv}av)", menu.layout()).unpack()
    assert number == 0 and props == {"children-display": "submenu"} and len(children) == 5
    labels = [child[1].get("label") for child in children]
    assert labels == ["Poser une question", "Conversations récentes", None, "Sessions Claude Code", "✋ mon__projet"]
    assert children[2][1] == {"type": "separator"}
    assert children[3][1]["enabled"] is False  # titre de section, grisé
    submenu = children[1]
    assert submenu[1]["children-display"] == "submenu" and submenu[2][0][1]["label"] == "09:10 · fstab"
    assert menu.layout(depth=0)[2] == []  # sans les enfants
    # La réponse à GetLayout, telle que la barre la reçoit.
    reply = GLib.Variant("(u(ia{sv}av))", (menu.revision, menu.layout())).unpack()
    assert reply[0] == 1 and len(reply[1][2]) == 5

    assert menu.activate(submenu[2][0][0]) and menu.activate(children[4][0])
    assert clicked == ["fstab", "projet"]
    assert not menu.activate(children[3][0])  # pas d'action


def test_menu_revision_changes_only_with_content() -> None:
    entries = [("Ranger la mascotte", lambda: None)]
    menu = tray.Menu(lambda: entries)
    menu.rebuild()
    assert not menu.rebuild() and menu.revision == 1
    entries[0] = ("Faire réapparaître la mascotte", lambda: None)
    assert menu.rebuild() and menu.revision == 2


def test_broken_menu_keeps_previous() -> None:
    menu = tray.Menu(lambda: 1 / 0)
    assert not menu.rebuild()
    assert menu.layout()[2] == []
