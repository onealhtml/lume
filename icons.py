"""Imagens do Lume: o orbe de luz e a roda de cores. `python icons.py` gera a pasta assets/."""
import colorsys
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

SS = 4  # supersampling para bordas suaves


def _soft(big, rgb, box, alpha, blur):
    """Elipse de cor solida com borda desfocada so no alfa (desfocar RGBA puxaria preto nas bordas)."""
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse(box, fill=alpha)
    layer = Image.new("RGBA", (big, big), (*rgb, 0))
    layer.putalpha(mask.filter(ImageFilter.GaussianBlur(blur)))
    return layer


def _orb(size, rgb, glow=True):
    """Esfera de luz na cor da lampada, com halo e reflexo no topo."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    c, r = big / 2, big * 0.30
    if glow:
        img = Image.alpha_composite(img, _soft(big, rgb, (c - r * 1.45, c - r * 1.45, c + r * 1.45, c + r * 1.45),
                                               150, big * 0.07))
    # corpo: gradiente radial do branco quente (centro, levemente acima) para a cor
    body = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    steps = 40
    for i in range(steps):
        t = i / (steps - 1)
        rr = r * (1 - t * 0.85)
        off = r * 0.18 * t
        col = tuple(round(v + (255 - v) * t ** 1.6 * 0.85) for v in rgb)
        d.ellipse((c - rr, c - rr - off, c + rr, c + rr - off), fill=(*col, 255))
    img = Image.alpha_composite(img, body)
    # reflexo especular
    img = Image.alpha_composite(img, _soft(big, (255, 255, 255), (c - r * 0.55, c - r * 0.82, c + r * 0.15, c - r * 0.38),
                                           130, big * 0.012))
    return img.resize((size, size), Image.LANCZOS)


def tray_icon(rgb=None, size=64):
    """Orbe na cor atual; apagada = anel vazio."""
    if rgb:
        return _orb(size, rgb, glow=True)
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    c, r, w = big / 2, big * 0.30, big * 0.075
    ImageDraw.Draw(img).ellipse((c - r, c - r, c + r, c + r), outline=(200, 200, 210, 255), width=round(w))
    return img.resize((size, size), Image.LANCZOS)


def app_icon(size=256):
    """Squircle escuro com o orbe ambar no centro."""
    big = size * SS
    bg = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.23, fill=255)
    grad = Image.new("RGBA", (big, big))
    gd = ImageDraw.Draw(grad)
    for y in range(big):
        t = y / big
        gd.line((0, y, big, y), fill=(round(44 - 22 * t), round(46 - 24 * t), round(58 - 30 * t), 255))
    bg.paste(grad, mask=mask)
    orb = _orb(big, (255, 170, 70)).resize((round(big * 1.1),) * 2, Image.LANCZOS)
    off = (big - orb.width) // 2
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    layer.paste(orb, (off, off), orb)
    clipped = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    clipped.paste(layer, mask=mask)
    return Image.alpha_composite(bg, clipped).resize((size, size), Image.LANCZOS)


def color_wheel(size=460):
    """Matiz no angulo (0 = direita, sentido horario com y para baixo), saturacao no raio."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    R = size / 2
    for y in range(size):
        for x in range(size):
            dx, dy = x + 0.5 - R, y + 0.5 - R
            d = math.hypot(dx, dy)
            if d > R:
                continue
            h = (math.degrees(math.atan2(dy, dx)) + 360) % 360
            r, g, b = (round(v * 255) for v in colorsys.hsv_to_rgb(h / 360, min(1, d / R), 1))
            px[x, y] = (r, g, b, round(255 * min(1, R - d)))
    return img


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    os.makedirs(out, exist_ok=True)
    icon = app_icon(256)
    icon.save(os.path.join(out, "lume.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(os.path.join(out, "lume.png"))
    color_wheel().save(os.path.join(out, "wheel.png"))
    if "--preview" not in sys.argv:
        raise SystemExit
    # pre-visualizacao do icone da bandeja (nao vai para o repositorio)
    strip = Image.new("RGBA", (64 * 4, 64), (0, 0, 0, 0))
    for i, rgb in enumerate([None, (255, 177, 92), (255, 40, 60), (60, 120, 255)]):
        strip.paste(tray_icon(rgb), (i * 64, 0))
    strip.save("tray-preview.png")
    print("ok")
