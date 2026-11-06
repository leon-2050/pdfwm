"""生成应用图标 icon.ico（多尺寸）+ icon.png。

设计：深色圆角底 + 白纸（右上折角 = 纸媒暗示）+ 纸内红色斜向水印带。
水印带严格裁剪在纸面内 —— 水印就该被纸裁住，溢出去会读成丝带。
"""
from PIL import Image, ImageDraw, ImageFilter
import os

S = 1024
OUT = os.path.dirname(os.path.abspath(__file__))
BG_TOP = (38, 42, 66, 255)
BG_BOT = (25, 28, 45, 255)
RED = (226, 58, 54, 208)
INK = (168, 177, 196, 255)


def vgrad(size, top, bot):
    col = Image.new("RGBA", (1, size[1]))
    for y in range(size[1]):
        t = y / max(size[1] - 1, 1)
        col.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(4)))
    return col.resize(size, Image.BILINEAR)


def make():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bgmask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(bgmask).rounded_rectangle(
        (int(S * .045), int(S * .045), S - int(S * .045), S - int(S * .045)),
        radius=int(S * .225), fill=255)
    im.paste(vgrad((S, S), BG_TOP, BG_BOT), (0, 0), bgmask)

    pw, ph = int(S * 0.50), int(S * 0.63)
    r = int(pw * 0.06)
    fold = int(pw * 0.26)
    page = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    pd = ImageDraw.Draw(page)
    pd.rounded_rectangle((0, 0, pw - 1, ph - 1), radius=r, fill=(250, 251, 253, 255))
    pd.polygon([(pw - fold, 0), (pw, 0), (pw, fold)], fill=(0, 0, 0, 0))
    pd.polygon([(pw - fold, 0), (pw - fold, fold), (pw, fold)],
               fill=(205, 211, 224, 255))
    pd.line([(pw - fold, 0), (pw - fold, fold), (pw, fold)],
            fill=(176, 184, 201, 255), width=max(2, S // 360))

    x0, x1 = int(pw * 0.15), int(pw * 0.85)
    lh = max(6, int(ph * 0.036))
    y = int(ph * 0.21)
    lens = (1.0, 0.92, 0.60, 0.97)
    for k in range(4):
        pd.rounded_rectangle((x0, y, int(x0 + (x1 - x0) * lens[k]), y + lh),
                             radius=lh // 2, fill=INK)
        y += int(ph * 0.108)

    band = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bw = int(pw * 0.19)
    cy = int(ph * 0.62)
    bd.rectangle((-pw, cy - bw // 2, pw * 2, cy + bw // 2), fill=RED)
    band = band.rotate(33, resample=Image.BICUBIC, center=(pw // 2, cy))

    pmask = Image.new("L", (pw, ph), 0)
    pmd = ImageDraw.Draw(pmask)
    pmd.rounded_rectangle((0, 0, pw - 1, ph - 1), radius=r, fill=255)
    pmd.polygon([(pw - fold, 0), (pw, 0), (pw, fold)], fill=0)
    band.putalpha(Image.composite(band.getchannel("A"),
                                  Image.new("L", (pw, ph), 0), pmask))
    page = Image.alpha_composite(page, band)

    page = page.rotate(7, expand=True, resample=Image.BICUBIC)
    px = (S - page.width) // 2 - int(S * 0.012)
    py = (S - page.height) // 2 + int(S * 0.018)

    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        (px + int(S * .010), py + int(S * .024),
         px + page.width + int(S * .010), py + page.height + int(S * .024)),
        radius=int(S * .045), fill=(0, 0, 0, 95))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(S * 0.017)))
    im.alpha_composite(page, (px, py))

    im.save(os.path.join(OUT, "icon.png"))
    im.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT, "icon.ico"), format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("icon.ico / icon.png ok", im.size)


if __name__ == "__main__":
    make()