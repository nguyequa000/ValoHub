"""Generate ValoHub product icons with Pillow; geometry mirrors brand/mark.svg.

Run from any directory: py -3.12 desktop/electron/build/make_icons.py
No SVG rasteriser, network, or font dependency is required.

Mark: "Converge" - three feed bars (9u/6u/9u) end flush at the left edge of a
9x15 hub slab. Everything is axis-aligned on a 24-unit grid, so at 16 px the
bars are exactly 2 px with 2 px gaps and the hub is 6 x 10 px: no curves, no
counters, nothing to weld shut. The app icon plates the mark on a solid
#111725 tile (radius 16.7%) with the hub in lifted violet #8B72F5.
"""
from pathlib import Path
import math
from PIL import Image, ImageDraw

BUILD = Path(__file__).resolve().parent
BRAND = BUILD.parents[2] / 'bot/web/static/replays_app/brand'
SIZES = (16, 24, 32, 48, 64, 128, 256)

INK = (231, 236, 245, 255)     # #E7ECF5, 15.1:1 on the plate
ACCENT = (139, 114, 245, 255)  # #8B72F5, 4.96:1 on the plate
PLATE = (17, 23, 37, 255)      # #111725
TILE_RADIUS = 4 / 24           # 16.7% of the tile

# Geometry in the 24-unit viewBox, shared with mark.svg.
BARS = ((3.0, 4.5, 9.0, 3.0), (6.0, 10.5, 6.0, 3.0), (3.0, 16.5, 9.0, 3.0))  # x, y, w, h
NODE = (12.0, 4.5, 9.0, 15.0)                                                # x, y, w, h
NODE_RADIUS = 2.0


def _r(v):
    return math.floor(v + 0.5)  # round half up; Python's round() is half-even and uneven at 24 px


def _plate(size):
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=max(2, _r(size * TILE_RADIUS)), fill=PLATE)
    return image


def _draw_mark(draw, scale, ink, node):
    for x, y, w, h in BARS:
        draw.rectangle((_r(x * scale), _r(y * scale),
                        _r((x + w) * scale) - 1, _r((y + h) * scale) - 1), fill=ink)
    x, y, w, h = NODE
    draw.rounded_rectangle((_r(x * scale), _r(y * scale),
                            _r((x + w) * scale) - 1, _r((y + h) * scale) - 1),
                           radius=max(1, _r(NODE_RADIUS * scale)),
                           # Square left corners: the feed bars meet the hub there, and a
                           # rounded joint leaves a 1 px plate notch at 16 px.
                           corners=(False, True, True, False), fill=node)


def icon(size):
    """Rounded plate + two-tone mark, used for icon.png / icon.ico / taskbar."""
    ss = 1 if size <= 24 else 4
    px = size * ss
    image = _plate(px)
    _draw_mark(ImageDraw.Draw(image), px / 24, INK, ACCENT)
    return image if ss == 1 else image.resize((size, size), Image.Resampling.LANCZOS)


def mark(size, colour=INK):
    """Transparent monochrome mark (titlebar mask, About, splash, docs)."""
    ss = 4
    px = size * ss
    image = Image.new('RGBA', (px, px), (0, 0, 0, 0))
    _draw_mark(ImageDraw.Draw(image), px / 24, colour, colour)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def ascii_preview(size=24):
    """Print a low-res silhouette so the geometry can be eyeballed in a terminal."""
    alpha = mark(size).getchannel('A')
    ramp = ' .:-=+*#%@'
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            value = int(alpha.getpixel((x, y)))
            row.append(ramp[min(len(ramp) - 1, value * len(ramp) // 256)])
        rows.append(''.join(row))
    return '\n'.join(rows)


def main():
    BRAND.mkdir(parents=True, exist_ok=True)
    (BUILD / 'mark.svg').write_bytes((BRAND / 'mark.svg').read_bytes())

    tile = icon(512)
    tile.save(BUILD / 'icon.png')
    tile.save(BUILD / 'icon.ico', sizes=[(s, s) for s in SIZES],
              append_images=[icon(s) for s in SIZES])
    mark(512).save(BRAND / 'mark-512.png')

    for file in (BUILD / 'icon.png', BRAND / 'mark-512.png', BUILD / 'icon.ico'):
        with Image.open(file) as asset:
            asset.load()
            if file.suffix == '.ico':
                assert asset.ico.sizes() == {(s, s) for s in SIZES}, asset.ico.sizes()
            else:
                assert asset.size == (512, 512), asset.size
        print(f'OK {file}: {file.stat().st_size} bytes')

    # 16 px is the hard case: bars exactly 2 px, two clean 2 px gaps, hub 6 x 10.
    small = icon(16).convert('RGB')
    for joint in ((8, 3), (8, 12)):
        assert small.getpixel(joint) == ACCENT[:3], f'16px joint {joint} is {small.getpixel(joint)}, not ACCENT (notch)'
    mark_rows = []
    for y in range(16):
        count = 0
        for x in range(16):
            r, g, b = small.getpixel((x, y))
            if (abs(r - INK[0]) < 30 and abs(g - INK[1]) < 30 and abs(b - INK[2]) < 30) or \
               (abs(r - ACCENT[0]) < 30 and abs(g - ACCENT[1]) < 30 and abs(b - ACCENT[2]) < 30):
                count += 1
        mark_rows.append(count)
    runs = [row for row in mark_rows if row]
    assert len(runs) == 10, f'16px mark should be 10 rows tall, got {len(runs)}: {mark_rows}'
    assert min(runs) >= 6, f'16px mark has a thin row: {mark_rows}'
    print(f'OK 16px mark rows: {mark_rows}')


if __name__ == '__main__':
    main()
