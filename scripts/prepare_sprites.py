"""Prépare les sprites de la mascotte à partir des images générées.

Toutes les images sont recadrées avec le même cadre (pour que la mascotte ne
bouge pas d'un état à l'autre), mises au carré et réduites.

    python scripts/prepare_sprites.py ~/Downloads
"""

import argparse
from collections import deque
from pathlib import Path

from PIL import Image

# nom du fichier source -> chemin dans assets/sprites
SOURCES = {
    "repos": "idle/0.png",
    "clignement": "idle/1.png",
    "endormi": "sleeping/0.png",
    "réfléchit": "thinking/0.png",
    "parle": "talking/0.png",
    "inquiet": "worried/0.png",
    # Assise sur le bord de la fenêtre où elle est accrochée
    "assis": "sitting/0.png",
    "assis-clignement": "sitting/1.png",
    "assis-endormi": "sitting-sleeping/0.png",
}

SIZE = 512
MARGIN = 0.04  # marge autour du personnage, en proportion du cadre
SOLID = 128  # alpha minimal pour faire partie du personnage (exclut les halos légers)
HAZE_ALPHA = 64  # en dessous, un pixel gris est considéré comme du voile
HAZE_SATURATION = 24  # écart max entre canaux pour qu'un pixel soit « gris »
BLACK = 10  # fond noir des images sans transparence
DARK_GLOW = 90  # lueur sombre et chaude autour de la lanterne, sur ce fond noir

OUTPUT = Path(__file__).resolve().parent.parent / "assets" / "sprites"
ICON = OUTPUT.parent / "icon.png"
ICON_SIZE = 256
ICON_HEAD = 0.5  # sans icone.png : on garde le haut du personnage (tête + écharpe)


def find_source(folder: Path, name: str) -> Path:
    for suffix in (".png", ".webp"):
        path = folder / f"{name}{suffix}"
        if path.exists():
            return path
    raise SystemExit(f"image introuvable : {folder / name}.png")


def remove_black_background(image: Image.Image) -> Image.Image:
    """Détoure une image dont le générateur a perdu la transparence (fond noir opaque).

    Remplissage depuis les coins : le noir devient transparent, et les lueurs sombres
    « chaudes » (lanterne) deviennent semi-transparentes. Le contour bleu marine du
    personnage, plus froid, n'est jamais touché.
    """
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    background = bytearray(width * height)
    queue = deque([(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)])
    for x, y in queue:
        background[y * width + x] = 1
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height and not background[ny * width + nx]:
                r, g, b = pixels[nx, ny]
                if max(r, g, b) <= BLACK or (b <= r and max(r, g, b) <= DARK_GLOW):
                    background[ny * width + nx] = 1
                    queue.append((nx, ny))

    out = Image.new("RGBA", (width, height))
    result = out.load()
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            brightest = max(r, g, b)
            if not background[y * width + x]:
                result[x, y] = (r, g, b, 255)
            elif brightest > BLACK:  # lueur sur fond noir : « couleur vers alpha »
                result[x, y] = (r * 255 // brightest, g * 255 // brightest, b * 255 // brightest, brightest)
    return out


def remove_haze(image: Image.Image) -> Image.Image:
    """Efface le voile gris quasi transparent que le générateur ajoute parfois.

    Seuls les pixels très transparents ET gris (peu saturés) disparaissent : le
    contour bleu marine et la lueur jaune de la lanterne sont conservés.
    """
    def keep(pixel: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        r, g, b, a = pixel
        if a < HAZE_ALPHA and max(r, g, b) - min(r, g, b) <= HAZE_SATURATION:
            return (r, g, b, 0)
        return pixel

    cleaned = Image.new("RGBA", image.size)
    cleaned.putdata([keep(p) for p in image.get_flattened_data()])
    return cleaned


def load(path: Path) -> Image.Image:
    image = Image.open(path)
    if image.mode != "RGBA" or image.getpixel((0, 0))[3] == 255:
        print(f"{path.name} : pas de transparence, détourage du fond noir…")
        image = remove_black_background(image)
    return remove_haze(image.convert("RGBA"))


def common_box(images: list[Image.Image]) -> tuple[int, int, int, int]:
    """Plus petit carré qui contient le personnage sur toutes les images."""
    boxes = [im.getchannel("A").point(lambda v: 255 if v >= SOLID else 0).getbbox() for im in images]
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[2] for b in boxes)
    bottom = max(b[3] for b in boxes)

    side = int(max(right - left, bottom - top) * (1 + 2 * MARGIN))
    cx, cy = (left + right) // 2, (top + bottom) // 2
    return cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side


def make_icon(source: Path, idle: Image.Image) -> None:
    """Icône de la barre : `icone.png` si fourni, sinon la tête découpée dans le sprite au repos."""
    path = source / "icone.png"
    if path.exists():
        icon = load(path)
    else:
        left, top, right, bottom = idle.getchannel("A").point(lambda v: 255 if v >= SOLID else 0).getbbox()
        icon = idle.crop((left, top, right, top + int((bottom - top) * ICON_HEAD)))
    icon = icon.crop(icon.getchannel("A").point(lambda v: 255 if v >= SOLID else 0).getbbox())
    side = max(icon.size)
    square = Image.new("RGBA", (side, side))
    square.paste(icon, ((side - icon.width) // 2, (side - icon.height) // 2))
    square.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS).save(ICON, optimize=True)
    print(f"{'icone' if path.exists() else 'tête':11} -> {ICON.relative_to(OUTPUT.parent.parent)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="dossier contenant repos.png, clignement.png…")
    args = parser.parse_args()

    images = {name: load(find_source(args.source, name)) for name in SOURCES}
    if len({im.size for im in images.values()}) != 1:
        raise SystemExit("les images n'ont pas toutes la même taille")

    box = common_box(list(images.values()))
    for name, target in SOURCES.items():
        out = OUTPUT / target
        out.parent.mkdir(parents=True, exist_ok=True)
        # crop() complète en transparent si le cadre déborde de l'image.
        images[name].crop(box).resize((SIZE, SIZE), Image.LANCZOS).save(out, optimize=True)
        print(f"{name:11} -> {out.relative_to(OUTPUT.parent.parent)}")
    make_icon(args.source, images["repos"])


if __name__ == "__main__":
    main()
