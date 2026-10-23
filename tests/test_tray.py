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
