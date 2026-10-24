from pathlib import Path

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
