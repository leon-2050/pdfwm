#!/usr/bin/env python3
"""pdfwm.py — 可控的 PDF 水印工具。

设计要点（这几条决定了水印"看着歪/看不见/被裁/能被打掉"）：
1. 以每页 CropBox 为准定位和缩放，不假设 A4；
2. 先把 /Rotate 烘进内容流再画（transfer_rotation_to_content），横版/旋转页不会错位；
3. 透明度走 ExtGState(CA/ca)，不是把颜色调浅——后者盖住正文时会挡字；
4. 字号按旋转后外接框反解，长中文不会被页边裁掉。

用法:
  python pdfwm.py in.pdf -o out.pdf --text "内部资料 CONFIDENTIAL" --owner "张三"
  python pdfwm.py in.pdf -o out.pdf --text "DRAFT" --mode tile --angle 30 --opacity 0.08
  python pdfwm.py in.pdf -o out.pdf --image logo.png --mode center --opacity 0.15
  python pdfwm.py in.pdf -o out.pdf --text "DRAFT" --pages 1-3,7 --under
  python pdfwm.py in.pdf -o out.pdf --text "内部资料" --flatten        # 栅格化，抗剔除
  python pdfwm.py in.pdf --batch recipients.csv --outdir ./out         # 每人一份唯一水印
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import sys

from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

CJK_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", "Msyh"),
    (r"C:\Windows\Fonts\simhei.ttf", "SimHei"),
    (r"C:\Windows\Fonts\simsun.ttc", "SimSun"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "NotoCJK"),
    ("/System/Library/Fonts/PingFang.ttc", "PingFang"),
]


def pick_font(text: str) -> str:
    if all(ord(c) < 128 for c in text):
        return "Helvetica"
    for path, name in CJK_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
                return name
            except Exception:
                continue
    print("warn: 未找到中文字体，中文可能显示为方块；用 --font 指定 ttf", file=sys.stderr)
    return "Helvetica"


def page_boxes(page):
    mb = page.mediabox
    try:
        cb = page.cropbox
    except Exception:
        cb = mb
    return mb, cb, float(cb.width), float(cb.height)


def fit_font_size(text, font, w, h, angle, scale, box_pad=0.02):
    """按旋转后的外接框反解字号。

    45° 斜放时长字符串的水平投影是 tw*cosθ + size*sinθ，按对角线长度定字号
    一定会被页边裁掉（水印最常见的翻车点）。这里同时解宽、高两个约束。
    """
    tw100 = pdfmetrics.stringWidth(text, font, 100.0) or 100.0
    avail_w = w * (1.0 - 2 * box_pad)
    avail_h = h * (1.0 - 2 * box_pad)
    rad = math.radians(abs(angle) % 180)
    cos_t, sin_t = abs(math.cos(rad)), abs(math.sin(rad))
    max_by_w = avail_w / (tw100 / 100.0 * cos_t + sin_t)
    max_by_h = avail_h / (tw100 / 100.0 * sin_t + cos_t)
    return max(6.0, min(max_by_w, max_by_h) * scale)


def build_stamp(w, h, ox, oy, args, font):
    """生成与页面同尺寸的单页 stamp（坐标系 = 页面 MediaBox 坐标）。"""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(w, h))
    c.setFillAlpha(args.opacity)
    c.setStrokeAlpha(args.opacity)
    if getattr(args, "color", ""):
        r, g, b = (int(args.color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
        c.setFillColorRGB(r, g, b)
    box_pad = getattr(args, "box_pad", 0.02)
    owner = getattr(args, "owner", "")

    if getattr(args, "image", ""):
        from PIL import Image
        iw, ih = Image.open(args.image).size
        if args.mode == "tile":
            step_x, step_y = w / 3, h / 4
            for i in range(-1, 4):
                for j in range(-1, 5):
                    c.saveState()
                    c.translate(i * step_x + ox, j * step_y + oy)
                    c.rotate(args.angle)
                    sw = min(w, h) * 0.28
                    c.drawImage(args.image, -sw / 2, -sw * ih / iw / 2,
                                width=sw, height=sw * ih / iw, mask="auto")
                    c.restoreState()
        else:
            sw = w * args.scale
            sh = sw * ih / iw
            c.saveState()
            c.translate(ox + w / 2, oy + h / 2)
            c.rotate(args.angle)
            c.drawImage(args.image, -sw / 2, -sh / 2, width=sw, height=sh, mask="auto")
            c.restoreState()
        c.showPage()
        c.save()
        buf.seek(0)
        return PdfReader(buf).pages[0]

    if args.mode == "tile":
        # 平铺：字号由格子决定，且必须保证横向至少 2~3 列。
        # 只按 fit_font_size 取（那是"整串塞进一页"的字号）会导致一格就占满整页，
        # 平铺退化成单条水印 —— 这是 tile 模式最容易写错的地方。
        tile_size = max(8.0, min(w, h) * 0.075)
        tw = pdfmetrics.stringWidth(args.text, font, tile_size)
        step_x = tw * 1.35
        max_step_x = w / 2.2                      # 至少 2.2 列
        if step_x > max_step_x:
            tile_size *= max_step_x / step_x
            tile_size = max(6.0, tile_size)
            tw = pdfmetrics.stringWidth(args.text, font, tile_size)
            step_x = tw * 1.35
        osize = max(5.0, tile_size * 0.26)
        step_y = max(tile_size * (4.2 if owner else 3.2), 40.0)
        for i in range(int(w / step_x) + 3):
            for j in range(int(h / step_y) + 3):
                c.saveState()
                c.translate(ox + i * step_x, oy + j * step_y)
                c.rotate(args.angle)
                c.setFont(font, tile_size)
                c.drawString(0, 0, args.text)
                if owner:
                    c.setFont(font, osize)
                    c.drawString(0, -osize * 1.4, owner)
                c.restoreState()
        c.showPage()
        c.save()
        buf.seek(0)
        return PdfReader(buf).pages[0]

    size = fit_font_size(args.text, font, w, h, args.angle, args.scale, box_pad)
    tw = pdfmetrics.stringWidth(args.text, font, size)
    c.setFont(font, size)
    c.saveState()
    c.translate(ox + w / 2, oy + h / 2)
    c.rotate(args.angle)
    c.drawString(-tw / 2, -size * 0.35, args.text)
    c.restoreState()

    if owner:
        osize = max(7.0, size * 0.28)
        ow = pdfmetrics.stringWidth(owner, font, osize)
        c.setFont(font, osize)
        c.saveState()
        c.translate(ox + w / 2, oy + h / 2)
        c.rotate(args.angle)
        c.drawString(-ow / 2, -size * 0.9, owner)
        c.restoreState()

    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def parse_pages(spec, n: int) -> set[int]:
    if not spec:
        return set(range(n))
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            a = int(a) if a.strip() else 1
            b = int(b) if b.strip() else n
            out.update(range(a, b + 1))
        else:
            out.add(int(part))
    return {p - 1 for p in out if 1 <= p <= n}


def stamp_pdf(src, args, password: str = "") -> tuple[bytes, int, int]:
    """给一份 PDF 盖水印，返回 (pdf bytes, 总页数, 被盖页数)。"""
    reader = PdfReader(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    if reader.is_encrypted:
        if not password:
            raise ValueError("PDF is encrypted; pass --password")
        reader.decrypt(password)

    font = getattr(args, "font", "") or pick_font(args.text)
    if getattr(args, "font", ""):
        pdfmetrics.registerFont(TTFont("CustomWM", args.font, subfontIndex=0))
        font = "CustomWM"

    writer = PdfWriter(clone_from=reader)
    targets = parse_pages(getattr(args, "pages", ""), len(writer.pages))
    for idx, page in enumerate(writer.pages):
        if idx not in targets:
            continue
        try:
            page.transfer_rotation_to_content()
        except Exception:
            pass
        mb, cb, w, h = page_boxes(page)
        stamp = build_stamp(w, h, float(cb.left), float(cb.bottom), args, font)
        page.merge_transformed_page(
            stamp,
            Transformation().translate(tx=float(mb.left), ty=float(mb.bottom)),
            over=not getattr(args, "under", False),
            expand=False,
        )
    writer.add_metadata({"/Producer": "pdfwm", "/Creator": "pdfwm"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), len(writer.pages), len(targets)


def rasterize_pdf(pdf_bytes: bytes, dpi: int = 200, quality: int = 88) -> bytes:
    """每页渲染成位图再重组成 PDF —— 水印烤进像素，qpdf/mutool 剔不掉。
    代价：文字层丢失，不可检索/不可复制。
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    out = PdfWriter()
    scale = max(72, dpi) / 72.0
    for page in doc:
        w_pt, h_pt = page.get_size()
        pil = page.render(scale=scale).to_pil().convert("RGB")
        img_buf = io.BytesIO()
        pil.save(img_buf, format="JPEG", quality=quality, optimize=True)
        img_buf.seek(0)
        page_buf = io.BytesIO()
        c = rl_canvas.Canvas(page_buf, pagesize=(w_pt, h_pt))
        c.drawImage(ImageReader(img_buf), 0, 0, width=w_pt, height=h_pt)
        c.showPage()
        c.save()
        page_buf.seek(0)
        out.add_page(PdfReader(page_buf).pages[0])
    buf = io.BytesIO()
    out.write(buf)
    return buf.getvalue()


def build_args(**over) -> argparse.Namespace:
    """batch / GUI / 自检复用的一份默认参数。"""
    base = dict(text="CONFIDENTIAL", owner="", image="", mode="center", angle=45.0,
                opacity=0.12, scale=0.95, color="#cc0000", font="", pages="",
                under=False, flatten=False, flatten_dpi=200, box_pad=0.02,
                password="", batch="", outdir="")
    base.update(over)
    return argparse.Namespace(**base)


def run_batch(args) -> int:
    """按 CSV 给每个收件人生成一份唯一水印的 PDF。

    CSV 表头: file,text,owner[,pages] —— file 是输出文件名（可省 .pdf）。
    text / owner 支持 {列名} 占位符，由同行其它列填充。
    """
    if not os.path.isfile(args.batch):
        print(f"no such csv: {args.batch}")
        return 1
    outdir = args.outdir or "out"
    os.makedirs(outdir, exist_ok=True)
    rows = 0
    with open(args.batch, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            name = row.get("file", "")
            if not name:
                continue
            try:
                text = (row.get("text") or args.text).format(**row)
                owner = (row.get("owner") or "").format(**row)
            except (KeyError, IndexError) as e:
                print(f"  skip {name}: bad placeholder {e}")
                continue
            ns = argparse.Namespace(**{
                **vars(args),
                "text": text or args.text,
                "owner": owner,
                "pages": row.get("pages") or args.pages,
            })
            data, total, stamped = stamp_pdf(args.input, ns, args.password)
            if args.flatten:
                data = rasterize_pdf(data, args.flatten_dpi)
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            dest = os.path.join(outdir, name)
            with open(dest, "wb") as fh:
                fh.write(data)
            rows += 1
            print(f"  {dest}  pages={total} stamped={stamped} owner={ns.owner!r}")
    print(f"ok batch rows={rows} outdir={outdir}")
    return 0 if rows else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output", default="")
    ap.add_argument("--text", default="CONFIDENTIAL")
    ap.add_argument("--owner", default="", help="第二行小字，如 姓名/工号/日期")
    ap.add_argument("--image", default="", help="用图片(logo)代替文字")
    ap.add_argument("--mode", choices=["center", "tile"], default="center")
    ap.add_argument("--angle", type=float, default=45.0)
    ap.add_argument("--opacity", type=float, default=0.12)
    ap.add_argument("--scale", type=float, default=0.95, help="文字占可用宽高的比例")
    ap.add_argument("--color", default="#cc0000")
    ap.add_argument("--font", default="")
    ap.add_argument("--pages", default="")
    ap.add_argument("--under", action="store_true", help="放在正文下层（不压字，但可能被背景遮住）")
    ap.add_argument("--flatten", action="store_true",
                    help="栅格化：水印烤进像素，无法剔除（代价：文字不可检索）")
    ap.add_argument("--flatten-dpi", type=int, default=200)
    ap.add_argument("--batch", default="", help="CSV: file,text,owner[,pages] —— 每人一份唯一水印")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--password", default="")
    args = ap.parse_args()

    if not args.input.lower().endswith(".pdf"):
        print("input must be a .pdf")
        return 1

    if args.batch:
        return run_batch(args)

    if not args.output:
        print("-o/--output is required (unless --batch)")
        return 2

    data, total, stamped = stamp_pdf(args.input, args, args.password)
    if args.flatten:
        data = rasterize_pdf(data, args.flatten_dpi)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"ok pages={total} stamped={stamped} opacity={args.opacity} "
          f"angle={args.angle} mode={args.mode} flatten={args.flatten} out={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())