"""Genereer alle iOS app icons in vereiste sizes voor Apple App Store.

Apple Guideline 2.3.8 vereist dat het app icon NIET leeg is + alle sizes hebben.
Genereert AppIcon.appiconset/ structure compatible met Capacitor iOS.
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import json

SRC = Path(__file__).parent / "static" / "icons" / "icon-512.png"
OUT = Path(__file__).parent / "ios-app-icons" / "AppIcon.appiconset"
OUT.mkdir(parents=True, exist_ok=True)

# Apple App Icon set — COMPLETE lijst voor iOS 17+
ICONS = [
    # iPhone Notification (20pt)
    ("Icon-App-20x20@2x.png", 40,  "iphone", "20x20", "2x"),
    ("Icon-App-20x20@3x.png", 60,  "iphone", "20x20", "3x"),
    # iPhone Settings (29pt)
    ("Icon-App-29x29@2x.png", 58,  "iphone", "29x29", "2x"),
    ("Icon-App-29x29@3x.png", 87,  "iphone", "29x29", "3x"),
    # iPhone Spotlight (40pt)
    ("Icon-App-40x40@2x.png", 80,  "iphone", "40x40", "2x"),
    ("Icon-App-40x40@3x.png", 120, "iphone", "40x40", "3x"),
    # iPhone App (60pt) — verplicht
    ("Icon-App-60x60@2x.png", 120, "iphone", "60x60", "2x"),
    ("Icon-App-60x60@3x.png", 180, "iphone", "60x60", "3x"),
    # iPad Notification (20pt)
    ("Icon-App-20x20@1x.png", 20,  "ipad", "20x20", "1x"),
    ("Icon-App-20x20-ipad@2x.png", 40, "ipad", "20x20", "2x"),
    # iPad Settings (29pt)
    ("Icon-App-29x29@1x.png", 29,  "ipad", "29x29", "1x"),
    ("Icon-App-29x29-ipad@2x.png", 58, "ipad", "29x29", "2x"),
    # iPad Spotlight (40pt)
    ("Icon-App-40x40@1x.png", 40,  "ipad", "40x40", "1x"),
    ("Icon-App-40x40-ipad@2x.png", 80, "ipad", "40x40", "2x"),
    # iPad App (76pt) — verplicht
    ("Icon-App-76x76@1x.png", 76,  "ipad", "76x76", "1x"),
    ("Icon-App-76x76@2x.png", 152, "ipad", "76x76", "2x"),
    # iPad Pro App (83.5pt)
    ("Icon-App-83.5x83.5@2x.png", 167, "ipad", "83.5x83.5", "2x"),
    # App Store Marketing (1024pt) — KRITIEK voor App Store submission
    ("Icon-App-1024x1024.png", 1024, "ios-marketing", "1024x1024", "1x"),
]


def make_icon(size: int) -> Image.Image:
    """Maak FieldOps icoon op opgegeven size — solid, herkenbaar, NIET LEEG."""
    BG = (10, 15, 30)            # #0a0f1e donker
    ACCENT = (0, 212, 255)       # #00d4ff cyan
    ACCENT_DARK = (0, 153, 204)  # #0099cc
    WHITE = (255, 255, 255)

    img = Image.new("RGB", (size, size), BG)  # RGB, GEEN alpha (Apple eist solid)
    draw = ImageDraw.Draw(img)

    # Accent cirkel achtergrond (gradient simulatie)
    pad_outer = max(int(size * 0.08), 1)
    draw.ellipse(
        (pad_outer, pad_outer, size - pad_outer, size - pad_outer),
        fill=ACCENT_DARK,
    )
    pad_inner = max(int(size * 0.14), 2)
    draw.ellipse(
        (pad_inner, pad_inner, size - pad_inner, size - pad_inner),
        fill=ACCENT,
    )

    # Letter "F"
    font_size = max(int(size * 0.55), 8)
    font = None
    for font_name in ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]:
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    text = "F"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1] - max(int(size * 0.03), 1)
    draw.text((tx, ty), text, font=font, fill=WHITE)

    # Horizon-streep (FieldOps = veld)
    if size >= 40:
        line_y = int(size * 0.78)
        line_w = max(int(size * 0.06), 2)
        line_pad = int(size * 0.28)
        draw.rounded_rectangle(
            (line_pad, line_y - line_w // 2, size - line_pad, line_y + line_w // 2),
            radius=line_w // 2,
            fill=WHITE,
        )

    return img


# Genereer alle iconen
for fname, size, *_ in ICONS:
    icon = make_icon(size)
    icon.save(OUT / fname, "PNG", optimize=True)
    print(f"  {fname}  {size}x{size}")

# Genereer Contents.json (Apple Asset Catalog manifest)
images = []
for fname, size, idiom, sz_str, scale in ICONS:
    images.append({
        "filename": fname,
        "idiom": idiom,
        "scale": scale,
        "size": sz_str,
    })

contents = {
    "images": images,
    "info": {
        "author": "xcode",
        "version": 1,
    },
}

with open(OUT / "Contents.json", "w", encoding="utf-8") as f:
    json.dump(contents, f, indent=2)

print(f"\n✅ {len(ICONS)} iOS icons + Contents.json geschreven naar:")
print(f"   {OUT}")
print("\nDeze folder moet in Capacitor iOS project op:")
print("   ios/App/App/Assets.xcassets/AppIcon.appiconset/")
