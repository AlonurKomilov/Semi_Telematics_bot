"""Turn any screenshot into what the Chrome Web Store accepts.

The store takes screenshots at exactly 1280×800 (or 640×400), JPEG or
24-bit PNG with no alpha.  A screen capture is never that: a 1920×1080
window, a 4K display, a side panel on its own.  This scales the image to
COVER 1280×800 and centre-crops the overflow, so nothing is stretched
and the middle of the picture survives.  Portrait captures (the panel
alone) are set on a dark canvas instead of being cropped to a sliver.

    python3 store_screenshot.py versions/shot1.png versions/shot2.png
    → versions/store-screenshots/shot1.png, …   (1280×800, RGB)

Pass ``--focus right`` to keep the right edge (the panel) when cropping
a wide capture, ``--focus left`` for the left; default is centre.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from PIL import Image

W, H = 1280, 800
CANVAS = (15, 17, 21)   # the panel's own background (--bg)


def fit(img: Image.Image, focus: str = "center") -> Image.Image:
    img = img.convert("RGB")                       # drops alpha: 24-bit
    w, h = img.size
    if w / h < 1.2:
        # Portrait or square: scale to the height, centre on a canvas.
        scale = H / h
        nw = max(1, round(w * scale))
        resized = img.resize((nw, H), Image.LANCZOS)
        out = Image.new("RGB", (W, H), CANVAS)
        out.paste(resized, ((W - nw) // 2, 0))
        return out
    scale = max(W / w, H / h)                      # cover
    nw, nh = max(W, round(w * scale)), max(H, round(h * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)
    x = {"left": 0, "right": nw - W}.get(focus, (nw - W) // 2)
    y = (nh - H) // 2
    return resized.crop((x, y, x + W, y + H))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("images", nargs="+", type=pathlib.Path)
    p.add_argument("--focus", choices=("left", "center", "right"), default="center")
    p.add_argument("--out", type=pathlib.Path, default=None,
                   help="output folder (default: <first image's folder>/store-screenshots)")
    a = p.parse_args()
    out_dir = a.out or (a.images[0].parent / "store-screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in a.images:
        if not src.is_file():
            print(f"skip: {src} is not a file", file=sys.stderr); continue
        img = Image.open(src)
        result = fit(img, a.focus)
        dst = out_dir / (src.stem + ".png")
        result.save(dst, "PNG", optimize=True)
        assert result.size == (W, H) and result.mode == "RGB"
        print(f"{src.name}  {img.size[0]}×{img.size[1]} → {dst}  {W}×{H} RGB  {dst.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
