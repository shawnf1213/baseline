"""Generate the app icon and splash image from the brand: dark ground, the
BASELINE wordmark in Barlow Condensed with the green on LINE, and a green
baseline rule. Expo derives every iOS size from the 1024x1024 icon; the
splash image is drawn at 2x and centred by expo-splash-screen.

    python scripts/make_icons.py
"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "assets", "images")
FONT = os.path.join(ROOT, "node_modules", "@expo-google-fonts", "barlow-condensed",
                    "900Black", "BarlowCondensed_900Black.ttf")
BG = (10, 10, 10)
GREEN = (0, 230, 118)
WHITE = (255, 255, 255)
MUTED = (107, 107, 107)


def font(size):
    return ImageFont.truetype(FONT, size)


def icon(size=1024):
    im = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(im)
    # A soft green glow low-left, like the landing page's mesh.
    glow = Image.new("RGB", (size, size), BG)
    gd = ImageDraw.Draw(glow)
    for r in range(int(size * 0.62), 0, -8):
        a = int(26 * (1 - r / (size * 0.62)))
        gd.ellipse([size * 0.05 - r, size * 0.75 - r, size * 0.05 + r, size * 0.75 + r],
                   fill=(BG[0] + a // 6, BG[1] + a, BG[2] + a // 2))
    im = Image.blend(im, glow, 0.9)
    d = ImageDraw.Draw(im)
    # The mark: a big "B" with the green baseline under it.
    f = font(int(size * 0.66))
    txt = "B"
    bbox = d.textbbox((0, 0), txt, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - w) / 2 - bbox[0]
    y = (size - h) / 2 - bbox[1] - size * 0.04
    d.text((x, y), txt, font=f, fill=WHITE)
    # Baseline rule — the name, literally.
    rule_y = int(y + bbox[1] + h + size * 0.045)
    d.rounded_rectangle([size * 0.24, rule_y, size * 0.76, rule_y + size * 0.045], radius=size * 0.02, fill=GREEN)
    return im


def splash(size=1200):
    im = Image.new("RGBA", (size, int(size * 0.42)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(int(size * 0.2))
    a, b = "BASE", "LINE"
    wa = d.textlength(a, font=f)
    wb = d.textlength(b, font=f)
    x = (im.width - (wa + wb)) / 2
    y = int(im.height * 0.12)
    d.text((x, y), a, font=f, fill=WHITE)
    d.text((x + wa, y), b, font=f, fill=GREEN)
    bbox = d.textbbox((x, y), a, font=f)
    rule_y = bbox[3] + int(size * 0.035)
    d.rounded_rectangle([x, rule_y, x + wa + wb, rule_y + int(size * 0.018)], radius=int(size * 0.009), fill=GREEN)
    return im


def main():
    os.makedirs(OUT, exist_ok=True)
    ic = icon()
    ic.save(os.path.join(OUT, "icon.png"))
    # Android adaptive foreground: the same mark on transparent, with padding.
    fg = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    small = icon(720).convert("RGBA")
    fg.paste(small, (152, 152))
    fg.save(os.path.join(OUT, "adaptive-icon.png"))
    sp = splash()
    sp.save(os.path.join(OUT, "splash-icon.png"))
    # The website-style favicon for the web target.
    ic.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "favicon.png"))
    for n in ("icon.png", "adaptive-icon.png", "splash-icon.png", "favicon.png"):
        p = os.path.join(OUT, n)
        with Image.open(p) as im:
            print(f"{n}: {im.size[0]}x{im.size[1]} {im.mode} {os.path.getsize(p)} bytes")


if __name__ == "__main__":
    main()
