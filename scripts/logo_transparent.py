"""
Rend le fond blanc du logo transparent et sauvegarde en PNG.
Usage: python -m scripts.logo_transparent [chemin_entree] [chemin_sortie]
Par défaut: static/logo.png -> static/logo.png (écrase avec fond transparent)
"""
from pathlib import Path
import sys

try:
    from PIL import Image
except ImportError:
    print("Installation de Pillow nécessaire: pip install Pillow")
    sys.exit(1)

BASE = Path(__file__).resolve().parent.parent
DEFAULT_IN = BASE / "static" / "logo.png"
DEFAULT_OUT = BASE / "static" / "logo.png"


def make_white_transparent(image: Image.Image, threshold: int = 250) -> Image.Image:
    """Rend les pixels blancs ou quasi blancs transparents."""
    img = image.convert("RGBA")
    data = list(img.getdata())
    new_data = [
        (255, 255, 255, 0) if (r >= threshold and g >= threshold and b >= threshold) else (r, g, b, a)
        for r, g, b, a in data
    ]
    img.putdata(new_data)
    return img


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IN
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    if not src.exists():
        print(f"Fichier introuvable: {src}")
        sys.exit(1)
    img = Image.open(src)
    out = make_white_transparent(img)
    out.save(dst, "PNG")
    print(f"Logo sauvegardé avec fond transparent: {dst}")


if __name__ == "__main__":
    main()
