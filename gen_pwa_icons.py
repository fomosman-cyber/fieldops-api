"""Genereer FieldOps PWA icons in alle vereiste maten."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT = Path(__file__).parent / "static" / "icons"
OUT.mkdir(parents=True, exist_ok=True)

# FieldOps brand colors (uit portaal.html)
ACCENT = (0, 212, 255)       # #00d4ff
ACCENT_DARK = (0, 153, 204)  # #0099cc
BG = (10, 15, 30)            # #0a0f1e
WHITE = (255, 255, 255)


def make_icon(size: int, maskable: bool = False, transparent_bg: bool = False) -> Image.Image:
    """Maak vierkant FieldOps icoon. Maskable = veilige zone in midden."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0) if transparent_bg else BG)
    draw = ImageDraw.Draw(img)

    # Achtergrond gradient simulatie via cirkel
    if not transparent_bg:
        # afgeronde rechthoek achtergrond (volledig vlak)
        pass

    # Maskable: laat 10% padding voor safe zone (iOS rounded corners knippen niet, Android wel)
    pad = int(size * 0.18) if maskable else int(size * 0.12)
    box = (pad, pad, size - pad, size - pad)

    # Logo: stylized "F" + onderstreping = veld/horizon symbool
    # Cirkel achtergrond met accent gradient
    circle_pad = int(size * 0.08)
    draw.ellipse(
        (circle_pad, circle_pad, size - circle_pad, size - circle_pad),
        fill=ACCENT_DARK,
    )
    # Lichtere binnencirkel voor diepte
    inner_pad = int(size * 0.14)
    draw.ellipse(
        (inner_pad, inner_pad, size - inner_pad, size - inner_pad),
        fill=ACCENT,
    )

    # "F" letter centraal
    try:
        font_size = int(size * 0.55)
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(size * 0.55))
        except OSError:
            font = ImageFont.load_default()

    text = "F"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1] - int(size * 0.03)
    draw.text((tx, ty), text, font=font, fill=WHITE)

    # Horizon-streep onderaan (FieldOps = veld)
    line_y = int(size * 0.78)
    line_w = int(size * 0.06)
    line_pad = int(size * 0.28)
    draw.rounded_rectangle(
        (line_pad, line_y - line_w // 2, size - line_pad, line_y + line_w // 2),
        radius=line_w // 2,
        fill=WHITE,
    )

    return img


# Alle iconen voor PWA (Android + iOS) + favicons
SIZES = [
    ("icon-72.png",   72,  False),
    ("icon-96.png",   96,  False),
    ("icon-128.png",  128, False),
    ("icon-144.png",  144, False),
    ("icon-152.png",  152, False),
    ("icon-192.png",  192, False),
    ("icon-256.png",  256, False),
    ("icon-384.png",  384, False),
    ("icon-512.png",  512, False),
    ("icon-maskable-192.png", 192, True),
    ("icon-maskable-512.png", 512, True),
    ("apple-touch-icon.png",  180, False),
    ("favicon-32.png",  32, False),
    ("favicon-16.png",  16, False),
]

for name, size, maskable in SIZES:
    icon = make_icon(size, maskable=maskable)
    icon.save(OUT / name, "PNG", optimize=True)
    print(f"  {name}  {size}x{size}")

# iOS splash screens (simpele versies — gecentreerd logo op donkere bg)
SPLASH_SIZES = [
    ("splash-640x1136.png",  640, 1136),    # iPhone SE
    ("splash-750x1334.png",  750, 1334),    # iPhone 8
    ("splash-1170x2532.png", 1170, 2532),   # iPhone 13/14/15
    ("splash-1284x2778.png", 1284, 2778),   # iPhone Pro Max
    ("splash-1290x2796.png", 1290, 2796),   # iPhone 15 Pro Max
]

for name, w, h in SPLASH_SIZES:
    splash = Image.new("RGBA", (w, h), BG)
    logo_size = min(w, h) // 3
    logo = make_icon(logo_size, transparent_bg=False)
    splash.paste(logo, ((w - logo_size) // 2, (h - logo_size) // 2), logo)
    splash.save(OUT / name, "PNG", optimize=True)
    print(f"  {name}  {w}x{h}")

print(f"\nAlle iconen geschreven naar {OUT}")
