#!/usr/bin/env python3
"""PDF 水印工具（可视化版）—— 左侧实时预览、右侧调参，一键应用到整份 PDF。

预览与导出走同一条绘制引擎（pdfwm.py）：透明度走 ExtGState、按旋转外接框收字号、
先烘 /Rotate 再画、按 CropBox 定位，所以预览所见即成品。
"""
from __future__ import annotations

import io
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, colorchooser, messagebox

import pypdfium2 as pdfium
from PIL import Image, ImageTk
from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdfwm as engine  # noqa: E402

PREVIEW_MAX = (620, 760)


def resource_path(name: str) -> str:
    """打包后资源在 sys._MEIPASS 里，源码运行时在脚本同目录。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


class App(ttk.Frame):
    def __init__(self, master, pdf_path: str | None = None):
        super().__init__(master, padding=8)
        self.pack(fill="both", expand=True)
        self.pdf_path = None
        self.page_idx = 0
        self.page_count = 0
        self._preview_job = None
        self._photo = None

        self.v_text = tk.StringVar(value="内部资料 CONFIDENTIAL")
        self.v_owner = tk.StringVar(value="")
        self.v_mode = tk.StringVar(value="center")
        self.v_angle = tk.DoubleVar(value=45)
        self.v_opacity = tk.DoubleVar(value=0.12)
        self.v_scale = tk.DoubleVar(value=0.85)
        self.v_color = tk.StringVar(value="#cc0000")
        self.v_pages = tk.StringVar(value="")
        self.v_layer = tk.StringVar(value="over")
        self.v_image = tk.StringVar(value="")
        self.v_raster = tk.BooleanVar(value=False)
        self.v_compare = tk.BooleanVar(value=False)
        self.v_pageinfo = tk.StringVar(value="- / -")
        self.v_status = tk.StringVar(value="打开一个 PDF 开始")

        self._build()
        if pdf_path:
            self.load(pdf_path)

    # ---------------- UI ----------------
    def _build(self):
        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nsew")
        right = ttk.Frame(self, padding=(14, 0, 0, 0))
        right.grid(row=0, column=1, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # width/height 先给 1：否则预览图会撑大 Label 的请求尺寸，
        # 把下面的翻页条和按钮条挤出窗口底边（真机 1080p 上必现）。
        self.canvas = tk.Label(left, background="#8a8a8a", anchor="center",
                               width=1, height=1)
        self.canvas.pack(fill="both", expand=True)

        nav = ttk.Frame(left, padding=(0, 8, 0, 0))
        nav.pack(fill="x")
        ttk.Button(nav, text="◀", width=3,
                   command=lambda: self.step_page(-1)).pack(side="left")
        ttk.Label(nav, textvariable=self.v_pageinfo, width=9,
                  anchor="center").pack(side="left")
        ttk.Button(nav, text="▶", width=3,
                   command=lambda: self.step_page(1)).pack(side="left")
        ttk.Checkbutton(nav, text="看原图对比", variable=self.v_compare,
                        command=self.schedule_preview).pack(side="left", padx=12)

        bar = ttk.Frame(left, padding=(0, 6, 0, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="打开 PDF…", command=self.pick).pack(side="left")
        ttk.Button(bar, text="保存整份…",
                   command=self.save).pack(side="left", padx=6)
        ttk.Button(bar, text="批量导出…",
                   command=self.batch).pack(side="left")
        ttk.Label(bar, textvariable=self.v_status).pack(side="left", padx=8)

        r = 0
        ttk.Label(right, text="水印文字").grid(row=r, column=0, sticky="w")
        r += 1
        e = ttk.Entry(right, textvariable=self.v_text, width=30)
        e.grid(row=r, column=0, columnspan=3, sticky="we", pady=(2, 8))
        e.bind("<KeyRelease>", lambda _e: self.schedule_preview())

        r += 1
        ttk.Label(right, text="第二行（姓名/工号/日期）").grid(row=r, column=0, sticky="w")
        r += 1
        e2 = ttk.Entry(right, textvariable=self.v_owner, width=30)
        e2.grid(row=r, column=0, columnspan=3, sticky="we", pady=(2, 8))
        e2.bind("<KeyRelease>", lambda _e: self.schedule_preview())

        r += 1
        ttk.Label(right, text="模式").grid(row=r, column=0, sticky="w")
        r += 1
        mf = ttk.Frame(right)
        mf.grid(row=r, column=0, columnspan=3, sticky="w", pady=(2, 8))
        for txt, val in (("居中单条", "center"), ("平铺", "tile")):
            ttk.Radiobutton(mf, text=txt, value=val, variable=self.v_mode,
                            command=self.schedule_preview).pack(side="left", padx=(0, 12))

        r = self._slider(right, r + 1, "角度", self.v_angle, 0, 90, "{}°")
        r = self._slider(right, r, "不透明度", self.v_opacity, 0.02, 0.60, "{:.2f}")
        r = self._slider(right, r, "大小", self.v_scale, 0.2, 1.0, "{:.2f}")

        ttk.Label(right, text="颜色").grid(row=r, column=0, sticky="w", pady=(6, 0))
        cf = ttk.Frame(right)
        cf.grid(row=r, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.swatch = tk.Label(cf, width=4, background=self.v_color.get(),
                               relief="solid", bd=1)
        self.swatch.pack(side="left")
        ttk.Button(cf, text="选择…", command=self.pick_color,
                   width=8).pack(side="left", padx=6)
        r += 1

        ttk.Label(right, text="层级").grid(row=r, column=0, sticky="w", pady=(8, 0))
        lf = ttk.Frame(right)
        lf.grid(row=r, column=1, columnspan=2, sticky="w", pady=(8, 0))
        for txt, val in (("正文上层", "over"), ("正文下层", "under")):
            ttk.Radiobutton(lf, text=txt, value=val, variable=self.v_layer,
                            command=self.schedule_preview).pack(side="left", padx=(0, 12))
        r += 1

        ttk.Label(right, text="页码范围").grid(row=r, column=0, sticky="w", pady=(8, 0))
        pe = ttk.Entry(right, textvariable=self.v_pages, width=18)
        pe.grid(row=r, column=1, sticky="w", pady=(8, 0))
        pe.bind("<KeyRelease>", lambda _e: self.schedule_preview())
        ttk.Label(right, text="留空=全部，例 1-3,7", foreground="#777").grid(
            row=r, column=2, sticky="w", padx=(6, 0), pady=(8, 0))
        r += 1

        ttk.Label(right, text="图片水印").grid(row=r, column=0, sticky="w", pady=(8, 0))
        if_ = ttk.Frame(right)
        if_.grid(row=r, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(if_, text="选择图片…", command=self.pick_image,
                   width=12).pack(side="left")
        ttk.Button(if_, text="清除", command=self.clear_image,
                   width=6).pack(side="left", padx=6)
        r += 1
        ttk.Label(right, textvariable=self.v_image, foreground="#777",
                  wraplength=300).grid(row=r, column=0, columnspan=3, sticky="w")

        r += 1
        ttk.Checkbutton(right, text="抗剔除（栅格化，文字将不可检索）",
                        variable=self.v_raster).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(10, 0))

        r += 1
        ttk.Separator(right, orient="horizontal").grid(
            row=r, column=0, columnspan=3, sticky="we", pady=10)
        r += 1
        hint = ("提示：透明度用 ExtGState 真透明，不压字；\n"
                "长中文会自动收缩到页面内，不会被裁边；\n"
                "横版/旋转页按 CropBox 定位，不会错位。")
        ttk.Label(right, text=hint, foreground="#666",
                  justify="left").grid(row=r, column=0, columnspan=3, sticky="w")

    def _slider(self, parent, row, label, var, lo, hi, fmt):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        val_lbl = ttk.Label(parent, width=6, text=fmt.format(var.get()))
        val_lbl.grid(row=row, column=2, sticky="e")

        def on_move(v):
            val_lbl.config(text=fmt.format(float(v)))
            self.schedule_preview()

        ttk.Scale(parent, from_=lo, to=hi, variable=var, command=on_move,
                  length=170).grid(row=row, column=1, sticky="we", pady=(4, 0))
        return row + 1

    # ---------------- actions ----------------
    def pick(self):
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if p:
            self.load(p)

    def load(self, path):
        try:
            n = len(PdfReader(path).pages)
        except Exception as e:
            self._dbg(f"load failed: {e!r}")
            messagebox.showerror("打不开", str(e))
            return
        self.pdf_path = path
        self.page_count = n
        self.page_idx = 0
        self.v_status.set(f"{os.path.basename(path)}（{n} 页）")
        self._update_pageinfo()
        self._dbg(f"loaded {path} pages={n}")
        self.schedule_preview()

    def _dbg(self, msg):
        p = os.environ.get("PDFWM_DEBUG")
        if p:
            with open(p, "a", encoding="utf-8") as f:
                f.write(msg + "\n")

    def _update_pageinfo(self):
        self.v_pageinfo.set(f"{self.page_idx + 1} / {self.page_count}"
                            if self.page_count else "- / -")

    def step_page(self, delta):
        if not self.page_count:
            return
        self.page_idx = max(0, min(self.page_count - 1, self.page_idx + delta))
        self._update_pageinfo()
        self.schedule_preview()

    def pick_color(self):
        c = colorchooser.askcolor(color=self.v_color.get(), title="水印颜色")
        if c and c[1]:
            self.v_color.set(c[1])
            self.swatch.config(background=c[1])
            self.schedule_preview()

    def pick_image(self):
        p = filedialog.askopenfilename(
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("全部", "*.*")])
        if p:
            self.v_image.set(p)
            self.schedule_preview()

    def clear_image(self):
        self.v_image.set("")
        self.schedule_preview()

    def _args(self):
        return engine.build_args(
            text=self.v_text.get(), owner=self.v_owner.get(),
            image=self.v_image.get(), mode=self.v_mode.get(),
            angle=self.v_angle.get(), opacity=self.v_opacity.get(),
            scale=self.v_scale.get(), color=self.v_color.get(),
            pages=self.v_pages.get(), under=self.v_layer.get() == "under",
            flatten=self.v_raster.get())

    def schedule_preview(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(180, self.render_preview)

    def render_preview(self):
        self._preview_job = None
        if not self.pdf_path:
            return
        try:
            png = self._render_page(self.page_idx)
        except Exception as e:
            self._dbg(f"preview failed: {e!r}")
            self.v_status.set(f"预览失败: {e}")
            return
        im = Image.open(io.BytesIO(png))
        # 预览尺寸同时受窗口实际可用空间约束，不然大图会再次撑破布局
        avail_w = max(240, self.canvas.winfo_width() - 16)
        avail_h = max(240, self.canvas.winfo_height() - 16)
        im.thumbnail((min(PREVIEW_MAX[0], avail_w), min(PREVIEW_MAX[1], avail_h)),
                     Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(im)
        self.canvas.config(image=self._photo, width=im.width, height=im.height)
        self._dbg(f"preview ok page={self.page_idx} {im.width}x{im.height}")

    def _render_page(self, idx: int) -> bytes:
        """渲染第 idx 页：只给这一页盖水印，长文档调参不必全量重排。"""
        if self.v_compare.get():
            doc = pdfium.PdfDocument(self.pdf_path)
            out = io.BytesIO()
            doc[idx].render(scale=1.5).to_pil().save(out, format="PNG")
            return out.getvalue()
        args = self._args()
        args.pages = str(idx + 1)
        data, _, _ = engine.stamp_pdf(self.pdf_path, args)
        doc = pdfium.PdfDocument(data)
        out = io.BytesIO()
        doc[idx].render(scale=1.5).to_pil().save(out, format="PNG")
        return out.getvalue()

    def save(self):
        if not self.pdf_path:
            messagebox.showinfo("等一下", "先打开一个 PDF")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile=(os.path.splitext(os.path.basename(self.pdf_path))[0]
                         + "-wm.pdf"))
        if not out:
            return
        args = self._args()
        try:
            data, total, stamped = engine.stamp_pdf(self.pdf_path, args)
            if args.flatten:
                data = engine.rasterize_pdf(data, args.flatten_dpi)
            with open(out, "wb") as f:
                f.write(data)
        except Exception as e:
            messagebox.showerror("失败", str(e))
            return
        self.v_status.set(f"已保存 {os.path.basename(out)}（{stamped}/{total} 页）")

    def batch(self):
        """按 CSV 给每个收件人生成一份唯一水印的 PDF。"""
        if not self.pdf_path:
            messagebox.showinfo("等一下", "先打开一个 PDF")
            return
        csvp = filedialog.askopenfilename(
            title="选择收件人 CSV（表头建议 file,name,email,text,owner）",
            filetypes=[("CSV", "*.csv"), ("全部", "*.*")])
        if not csvp:
            return
        outdir = filedialog.askdirectory(title="输出目录")
        if not outdir:
            return
        args = self._args()
        args.input = self.pdf_path
        args.batch = csvp
        args.outdir = outdir
        try:
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = engine.run_batch(args)
            log = buf.getvalue().strip()
            lines = log.splitlines() if log else []
            if rc == 0:
                self.v_status.set(lines[-1] if lines else "批量完成")
            else:
                self.v_status.set("批量失败，检查 CSV")
                messagebox.showerror("批量失败", log[-900:] or "CSV 里没有可用行")
        except Exception as e:
            messagebox.showerror("批量失败", str(e))


def _selftest() -> int:
    """无界面自检：建样品 PDF → 盖章 → 平铺 → 栅格化 → 回读文字/位图。"""
    from reportlab.pdfgen import canvas as rl
    from reportlab.lib.pagesizes import A4

    buf = io.BytesIO()
    c = rl.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 12)
    c.drawString(60, 780, "selftest body line")
    c.showPage()
    c.save()
    src = buf.getvalue()

    args = engine.build_args(text="内部资料 CONFIDENTIAL", owner="selftest")
    data, total, stamped = engine.stamp_pdf(src, args)
    doc = pdfium.PdfDocument(data)
    text = doc[0].get_textpage().get_text_range()
    im = doc[0].render(scale=1.0).to_pil()

    tdata, ttotal, _ = engine.stamp_pdf(
        src, engine.build_args(owner="x@y.z", mode="tile", angle=30, opacity=0.09))
    tdoc = pdfium.PdfDocument(tdata)
    ttext = tdoc[0].get_textpage().get_text_range()

    flat = engine.rasterize_pdf(data, dpi=120)
    fdoc = pdfium.PdfDocument(flat)
    ftext = fdoc[0].get_textpage().get_text_range()
    fim = fdoc[0].render(scale=1.0).to_pil()

    checks = [
        ("render", im.size[0] > 100),
        ("body preserved", "selftest body" in text),
        ("watermark text", "CONFIDENTIAL" in text),
        ("cjk font", engine.pick_font("内部资料") != "Helvetica"),
        ("tile mode", len(tdoc) == ttotal and "CONFIDENTIAL" in ttext),
        # 平铺必须真的重复；只有 1 次说明退化成了单条水印
        ("tile repeats", ttext.count("CONFIDENTIAL") >= 3),
        ("rasterize strips text", ftext.strip() == ""),
        ("rasterize renders", fim.size[0] > 100),
        ("pages intact", total == 1 and stamped == 1),
    ]
    for name, ok in checks:
        print(f"{'OK  ' if ok else 'FAIL'} {name}")
    logp = os.environ.get("PDFWM_SELFTEST_LOG")
    if logp:
        with open(logp, "w", encoding="utf-8") as f:
            for name, ok in checks:
                f.write(f"{'OK' if ok else 'FAIL'} {name}\n")
    return 0 if all(ok for _, ok in checks) else 1


def main():
    if os.environ.get("PDFWM_SELFTEST"):
        raise SystemExit(_selftest())
    if os.environ.get("PDFWM_ARGV_LOG"):
        with open(os.environ["PDFWM_ARGV_LOG"], "w", encoding="utf-8") as f:
            f.write(repr(sys.argv) + "\n")

    # Windows 高 DPI：不做这一步，界面被系统拉伸到模糊、预览尺寸也对不上
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    root = tk.Tk()
    root.title("PDF 水印工具")
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass

    App(root, sys.argv[1] if len(sys.argv) > 1 else None)

    # 窗口尺寸按内容实际需要算 —— 写死尺寸在高 DPI 上会把按钮/滑块挤出边界
    root.update_idletasks()
    need_w = max(root.winfo_reqwidth() + 24, 1240)
    need_h = max(root.winfo_reqheight() + 60, 820)
    root.geometry(f"{need_w}x{need_h}")
    root.minsize(max(900, need_w - 260), max(660, need_h - 200))

    for cand in ("icon.ico", "icon.png"):
        p = resource_path(cand)
        if os.path.exists(p):
            try:
                if cand.endswith(".ico"):
                    root.iconbitmap(default=p)
                else:
                    root.iconphoto(True, tk.PhotoImage(file=p))
                break
            except Exception:
                continue

    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        for attr in (20, 19):
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass

    root.mainloop()


if __name__ == "__main__":
    main()
