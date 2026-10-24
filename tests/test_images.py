from pathlib import Path

import pytest
from PIL import Image

from custos.context import images


@pytest.fixture(autouse=True)
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "run"
    folder.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(folder))
    return folder


def test_big_photo_is_reduced_to_png(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (4000, 3000), (200, 100, 50)).save(photo)
    copy = images.prepare(photo)
    with Image.open(copy) as image:
        assert image.format == "PNG" and image.size == (images.MAX_SIDE, 1176)
    assert photo.exists()  # l'original n'est pas touché


def test_small_image_keeps_its_size(tmp_path: Path) -> None:
    small = tmp_path / "petit.webp"
    Image.new("RGB", (300, 200)).save(small)
    with Image.open(images.prepare(small)) as image:
        assert image.size == (300, 200)


def test_not_an_image(tmp_path: Path, runtime: Path) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("pas une image")
    assert images.prepare(text) is None
    assert list(runtime.iterdir()) == []
