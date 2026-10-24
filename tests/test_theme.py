import json
from pathlib import Path

from custos.ui import theme
from custos.ui.markdown import to_pango

SCHEME = {
    "name": "dynamic",
    "mode": "dark",
    "colours": {
        "surfaceContainer": "090603",
        "outlineVariant": "4f4739",
        "onSurface": "f1e4d1",
        "primary": "e3c287",
        "onPrimary": "523d0e",
        "error": "f97758",
    },
}


def test_caelestia_palette(tmp_path: Path) -> None:
    path = tmp_path / "scheme.json"
    path.write_text(json.dumps(SCHEME))
    colors = theme.load(theme.AUTO, path)
    assert colors.card == "#090603" and colors.text == "#f1e4d1" and colors.accent == "#e3c287"


def test_without_caelestia_falls_back_to_light(tmp_path: Path) -> None:
    assert theme.load(theme.AUTO, tmp_path / "absent.json") == theme.LIGHT_THEME
    broken = tmp_path / "scheme.json"
    broken.write_text('{"colours": {"onSurface": "pas une couleur"}}')
    assert theme.load(theme.CAELESTIA, broken) == theme.LIGHT_THEME


def test_fixed_themes_ignore_caelestia(tmp_path: Path) -> None:
    path = tmp_path / "scheme.json"
    path.write_text(json.dumps(SCHEME))
    assert theme.load(theme.LIGHT, path) == theme.LIGHT_THEME
    assert theme.load(theme.DARK, path) == theme.DARK_THEME
    assert not theme.follows_caelestia(theme.DARK)


def test_colour_helpers() -> None:
    assert theme.rgba("#1a1f2b", 0.5) == "rgba(26, 31, 43, 0.5)"
    assert theme.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert theme.LIGHT_THEME.code == theme.mix("#f8f8fa", "#1a1f2b", 0.1)


def test_inline_code_takes_theme_colours() -> None:
    markup = to_pango("lance `ls`", "#f1e4d1", "#231f19")
    assert 'foreground="#f1e4d1" background="#231f19"' in markup
