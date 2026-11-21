"""Laporan AI sebagai PDF: sampul dengan angka kunci, teks Markdown dari LLM yang ditata ulang, dan
grafik pendukung (matplotlib) yang disisipkan di bawah bagian yang relevan.

Grafik dibuat dari `fakta` (angka yang sudah dihitung Python dan dikirim ke LLM), bukan dari teks LLM,
jadi grafik selalu sesuai dengan data simulasi meskipun narasinya keliru.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT, TA_RIGHT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, NextPageTemplate,  # noqa: E402
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

from . import STATUS, TIPE_WILAYAH  # noqa: E402
from .config import TANDA_SIMULASI, versi_model  # noqa: E402

# ------------------------------------------------------------------ warna & huruf
INK, INK_2, MUTED, LINE = "#14181f", "#454b57", "#6b7280", "#d9dde5"
ACCENT, ACCENT_DARK, ACCENT_SOFT = "#2340c4", "#16224f", "#eef1fb"
# Warna status sama dengan dashboard (PRD V-02); S sengaja abu-abu.
WARNA_STATUS = {"S": "#5b6884", "E": "#e6a700", "I": "#e5484d", "Z": "#1fb5a0", "A": "#a855f7"}
NAMA_STATUS = {"S": "Rentan", "E": "Terpapar", "I": "Penyebar", "Z": "Skeptis", "A": "Siaga aktif"}
# Intervensi: A (baseline) netral abu-abu, B–F urutan kategorikal tetap (lolos validator CVD, mode terang).
WARNA_IV = {"A": "#8b919c", "B": "#2a78d6", "C": "#eb6834", "D": "#1baf7a", "E": "#eda100", "F": "#e87ba4",
            "G": "#6250d6", "H": "#008300"}
KANAL_LABEL = {"wa": "WhatsApp", "fb": "Facebook", "tatap_muka": "Tatap muka", "radio": "Radio"}
WARNA_KANAL = {"wa": "#2a78d6", "fb": "#eb6834", "tatap_muka": "#1baf7a", "radio": "#eda100"}
BIRU = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
URUT_TIPE = [t for t in (*TIPE_WILAYAH, "semua")]
LABEL_PARAM = {  # nama parameter yang bisa dibaca orang awam (lihat config/parameter.yaml)
    "beta.wa": "Daya sebar lewat WhatsApp", "beta.fb": "Daya sebar lewat Facebook",
    "beta.tatap_muka": "Daya sebar tatap muka", "beta.radio": "Daya sebar radio",
    "tau_jam": "Lama penyebar tetap aktif", "laju_bersikap": "Kecepatan warga bersikap",
    "kredibilitas_dasar": "Peluang dasar warga percaya", "peluang_siaga": "Peluang penyebar jadi siaga aktif",
    "peluang_korektor": "Warga skeptis ikut meluruskan", "efek_literasi": "Efek literasi digital",
    "efek_klarifikasi_bersikap": "Efek klarifikasi pada keputusan", "prebunking.percaya": "Sisa kepercayaan setelah prebunking",
    "prebunking.paparan": "Sisa paparan setelah prebunking", "skala_sinyal": "Kekuatan sinyal seluler",
}


def _pendek(teks: str, n: int) -> str:
    """Potong di batas kata dan beri elipsis, bukan di tengah kata."""
    teks = str(teks)
    if len(teks) <= n:
        return teks
    return teks[: n].rsplit(" ", 1)[0].rstrip(",;:·-") + "…"


def _minus(s: str) -> str:
    return s.replace("-", "−")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
FRAME_W = PAGE_W - 2 * MARGIN

_FONT_CANDIDATES = [  # (reportlab regular, bold, italic, bolditalic), nama keluarga matplotlib
    (("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeuii.ttf",
      "C:/Windows/Fonts/segoeuiz.ttf"), "Segoe UI"),
]
_fonts_ready = False


def _setup_fonts() -> None:
    """Huruf Unicode (Segoe UI bila ada, kalau tidak DejaVu Sans bawaan matplotlib) untuk PDF dan grafik."""
    global _fonts_ready
    if _fonts_ready:
        return
    files, family = None, "DejaVu Sans"
    for cand, fam in _FONT_CANDIDATES:
        if all(Path(f).exists() for f in cand):
            files, family = cand, fam
            break
    if files is None:
        base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        files = tuple(str(base / f) for f in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf",
                                               "DejaVuSans-BoldOblique.ttf"))
    for name, f in zip(("Sans", "Sans-Bold", "Sans-Italic", "Sans-BoldItalic"), files):
        pdfmetrics.registerFont(TTFont(name, f))
    pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Italic", boldItalic="Sans-BoldItalic")
    for f in files:
        font_manager.fontManager.addfont(f)
    plt.rcParams.update({
        "font.family": family, "font.size": 8.5, "axes.titlesize": 9, "axes.titleweight": "bold",
        "axes.labelcolor": INK_2, "axes.edgecolor": LINE, "axes.linewidth": 0.8, "xtick.color": INK_2,
        "ytick.color": INK_2, "xtick.labelsize": 8, "ytick.labelsize": 8, "axes.spines.top": False,
        "axes.spines.right": False, "axes.grid": True, "grid.color": "#eceef2", "grid.linewidth": 0.7,
        "axes.axisbelow": True, "legend.frameon": False, "legend.fontsize": 8, "figure.dpi": 100,
        "text.color": INK, "axes.titlecolor": INK, "axes.titlelocation": "left",
    })
    _fonts_ready = True


def _styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName="Sans", textColor=colors.HexColor(INK), leading=14.2, fontSize=10)
    return {
        "body": ParagraphStyle("body", **base, spaceAfter=5),
        "bullet": ParagraphStyle("bullet", **base, leftIndent=14, bulletIndent=3, spaceAfter=3),
        "h2": ParagraphStyle("h2", fontName="Sans-Bold", fontSize=14, leading=18, textColor=colors.HexColor(ACCENT_DARK),
                             spaceBefore=14, spaceAfter=6, keepWithNext=1),
        "h3": ParagraphStyle("h3", fontName="Sans-Bold", fontSize=11, leading=14, textColor=colors.HexColor(INK),
                             spaceBefore=8, spaceAfter=4, keepWithNext=1),
        "quote": ParagraphStyle("quote", **{**base, "fontSize": 9, "leading": 12.5, "textColor": colors.HexColor(INK_2)}),
        "caption": ParagraphStyle("caption", fontName="Sans", fontSize=8.3, leading=11, textColor=colors.HexColor(MUTED),
                                  spaceBefore=3, spaceAfter=10),
        "cell": ParagraphStyle("cell", fontName="Sans", fontSize=8.3, leading=10.5, textColor=colors.HexColor(INK)),
        "cell_r": ParagraphStyle("cell_r", fontName="Sans", fontSize=8.3, leading=10.5, textColor=colors.HexColor(INK),
                                 alignment=TA_RIGHT),
        "head": ParagraphStyle("head", fontName="Sans-Bold", fontSize=8.3, leading=10.5, textColor=colors.HexColor(ACCENT_DARK)),
        "kpi_num": ParagraphStyle("kpi_num", fontName="Sans-Bold", fontSize=17, leading=20, textColor=colors.HexColor(ACCENT_DARK)),
        "kpi_lbl": ParagraphStyle("kpi_lbl", fontName="Sans", fontSize=8, leading=10.2, textColor=colors.HexColor(INK_2)),
        "meta_k": ParagraphStyle("meta_k", fontName="Sans", fontSize=8.5, leading=11, textColor=colors.HexColor(MUTED)),
        "meta_v": ParagraphStyle("meta_v", fontName="Sans", fontSize=9, leading=11.5, textColor=colors.HexColor(INK)),
        "cover_kicker": ParagraphStyle("ck", fontName="Sans-Bold", fontSize=8.5, leading=11, textColor=colors.HexColor("#b9c6ff")),
        "cover_title": ParagraphStyle("ct", fontName="Sans-Bold", fontSize=22, leading=27, textColor=colors.white),
        "cover_sub": ParagraphStyle("cs", fontName="Sans", fontSize=11, leading=15, textColor=colors.HexColor("#dfe5ff")),
    }


# ------------------------------------------------------------------ Markdown → flowable
def _inline(s: str) -> str:
    s = escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])", r"<i>\1</i>", s)
    return re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", s)


_NUM = re.compile(r"^[\s\[\(]*[−+\-–]?[\d.,]+\s*%?[\]\)]*(\s*(\(|\[|s\.d\.|–|-).*)?$")


def _table(rows: list[list[str]], st: dict) -> Table:
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    size = 8.3 if ncol <= 5 else 7.6 if ncol <= 7 else 7
    longest = [max(min(len(r[c]), 60) for r in rows) + 4 for c in range(ncol)]
    # kolom tidak boleh lebih sempit dari kata terpanjangnya (agar "Pedalaman" tidak terpotong jadi "Pedalama n")
    kata = [max((len(k) for r in rows for k in re.sub(r"\*\*", "", r[c]).split()), default=1) for c in range(ncol)]
    w_min = np.array([max(k * size * 0.62 + 10, 15 * mm if ncol <= 6 else 11 * mm) for k in kata])
    w = np.array(longest, float)
    w = np.maximum(w / w.sum() * FRAME_W, w_min)
    lebih = w.sum() - FRAME_W
    if lebih > 0:                       # ambil kelebihan dari kolom yang masih di atas batas minimumnya
        ruang = w - w_min
        w -= lebih * ruang / max(ruang.sum(), 1e-9)
    w = w / w.sum() * FRAME_W
    cell = ParagraphStyle("c", parent=st["cell"], fontSize=size, leading=size + 2.2)
    cell_r = ParagraphStyle("cr", parent=st["cell_r"], fontSize=size, leading=size + 2.2)
    head = ParagraphStyle("h", parent=st["head"], fontSize=size, leading=size + 2.2)
    data = [[Paragraph(_inline(c), head) for c in rows[0]]]
    for r in rows[1:]:
        data.append([Paragraph(_inline(c), cell_r if c and _NUM.match(c) and i > 0 else cell) for i, c in enumerate(r)])
    t = Table(data, colWidths=list(w), repeatRows=1, hAlign="LEFT")
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(ACCENT_SOFT)),
             ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#b8c3ea")),
             ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#e6e9ef")),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3.2),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2), ("LEFTPADDING", (0, 0), (-1, -1), 4),
             ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    style += [("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafbfd")) for i in range(2, len(data), 2)]
    t.setStyle(TableStyle(style))
    return t


def _callout(text: str, st: dict, tone: str = "info") -> Table:
    bg, bar = {"info": ("#f3f5fb", ACCENT), "warn": ("#fff7e6", "#c98500")}[tone]
    t = Table([[Paragraph(text, st["quote"])]], colWidths=[FRAME_W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
                           ("LINEBEFORE", (0, 0), (0, -1), 2.5, colors.HexColor(bar)),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def parse_markdown(md: str, st: dict) -> tuple[str, list[tuple[str, list]]]:
    """Kembalikan (judul H1, [(judul bagian, [flowable...])]). Bagian pertama tanpa judul = pembuka."""
    title, sections = "", [("", [])]
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        line = raw.strip()
        cur = sections[-1][1]
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            if rows:
                cur += [_table(rows, st), Spacer(1, 8)]
            continue
        if line.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(_inline(lines[i].strip().lstrip("> ").strip()))
                i += 1
            box, gap = _callout("<br/>".join(b for b in buf if b), st), Spacer(1, 8)
            box._penutup = gap._penutup = True
            cur += [box, gap]
            continue
        i += 1
        if not line or (set(line) <= set("-*_") and len(line) >= 3):
            continue
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("## ") or line.startswith("# "):
            sections.append((line.lstrip("#").strip(), []))
        elif line.startswith("### "):
            cur.append(Paragraph(_inline(line[4:]), st["h3"]))
        elif re.match(r"^[-*] ", line):
            indent = (len(raw) - len(raw.lstrip())) // 2
            s = ParagraphStyle("b", parent=st["bullet"], leftIndent=14 + 12 * indent, bulletIndent=3 + 12 * indent)
            cur.append(Paragraph(_inline(line[2:]), s, bulletText="•" if not indent else "–"))
        elif m := re.match(r"^(\d+)[.)] (.*)", line):
            cur.append(Paragraph(_inline(m.group(2)), st["bullet"], bulletText=f"{m.group(1)}."))
        else:
            cur.append(Paragraph(_inline(line), st["body"]))
    return title, sections


# ------------------------------------------------------------------ grafik
def _png(fig, width_mm: float = FRAME_W / mm) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    img = Image(buf)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width_mm * mm
    img.drawHeight = width_mm * mm * ratio
    return img


def _pct_axis(ax, axis: str = "y", dec: int = 0) -> None:
    from matplotlib.ticker import FuncFormatter

    f = FuncFormatter(lambda v, _: f"{v * 100:.{dec}f}%".replace(".", ","))
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(f)


def _id(v: float, dec: int = 0) -> str:
    return f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fig_kurva_mc(kurva: dict, dt: float, names: dict) -> Image:
    """Small multiples per tipe wilayah: proporsi siaga aktif (A) rata-rata per intervensi."""
    codes = sorted(kurva)
    arr = {c: np.asarray(kurva[c], float) for c in codes}       # (T+1, 5 tipe, 5 status)
    tipe = [t for i, t in enumerate(TIPE_WILAYAH) if any(arr[c][:, i, :].sum() > 0 for c in codes)]
    fig, axes = plt.subplots(1, len(tipe), figsize=(7.2, 2.35), sharey=True)
    axes = np.atleast_1d(axes)
    for ax, t in zip(axes, tipe):
        ti = TIPE_WILAYAH.index(t)
        for c in sorted(codes, key=lambda c: c == "A"):        # baseline digambar paling atas
            y = arr[c][:, ti, 4]
            x = np.arange(len(y)) * dt
            ax.plot(x, y, color="#4b515c" if c == "A" else WARNA_IV.get(c, INK_2), lw=1.3 if c == "A" else 1.6,
                    ls=(0, (3, 2)) if c == "A" else "-", label=f"{c}", solid_capstyle="round", zorder=3 if c == "A" else 2)
        ax.set_title(t.capitalize(), fontsize=8.5)
        ax.set_xlabel("jam sejak isu muncul", fontsize=7.5)
        ax.set_xticks(np.arange(0, x[-1] + 1, 12 if x[-1] <= 72 else 24))
        ax.grid(axis="x", visible=False)
    _pct_axis(axes[0], dec=1)
    axes[0].set_ylabel("proporsi penduduk")
    h, l = axes[0].get_legend_handles_labels()
    order = np.argsort(l)
    fig.legend([h[i] for i in order], [f"{l[i]} · {_pendek(names.get(l[i], ''), 46)}" for i in order], loc="lower center",
               ncol=2, bbox_to_anchor=(0.5, -0.27), fontsize=7.3, handlelength=2.2)
    fig.tight_layout(w_pad=0.6)
    return _png(fig)


def fig_efek(efek: list[dict], names: dict) -> Image:
    """Efek terhadap baseline A (%) per tipe wilayah, batang dengan IK 95%."""
    tipe = [t for t in URUT_TIPE if any(e["tipe"] == t for e in efek)]
    codes = sorted({e["intervensi"] for e in efek})
    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    W = 0.8 / len(codes)
    for ci, c in enumerate(codes):
        xs, ys, lo, hi = [], [], [], []
        for ti, t in enumerate(tipe):
            e = next((x for x in efek if x["tipe"] == t and x["intervensi"] == c), None)
            if not e or e.get("efek_persen") is None or not e.get("selisih"):
                continue
            base = e["selisih"] / (e["efek_persen"] / 100) if e["efek_persen"] else 0
            xs.append(ti + (ci - (len(codes) - 1) / 2) * W)
            ys.append(e["efek_persen"])
            lo.append(100 * e["selisih_lo"] / base if base else e["efek_persen"])
            hi.append(100 * e["selisih_hi"] / base if base else e["efek_persen"])
        ys, lo, hi = np.array(ys), np.array(lo), np.array(hi)
        ax.bar(xs, ys, W * 0.86, color=WARNA_IV.get(c, INK_2), edgecolor="white", linewidth=0.8,
               label=f"{c} · {_pendek(names.get(c, ''), 46)}", zorder=2)
        ax.errorbar(xs, ys, yerr=[np.abs(ys - lo), np.abs(hi - ys)], fmt="none", ecolor=INK_2, elinewidth=0.7,
                    capsize=1.6, zorder=3)
    ax.axhline(0, color=INK_2, lw=0.8)
    ax.set_xticks(range(len(tipe)), [t.capitalize() for t in tipe])
    ax.set_ylabel("perubahan beban siaga vs A")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:+.0f}%"))
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, fontsize=7.3)
    fig.tight_layout()
    return _png(fig)


def fig_heatmap(ringkasan: list[dict], names: dict) -> Image:
    tipe = [t for t in URUT_TIPE if any(r["tipe"] == t for r in ringkasan)]
    codes = sorted({r["intervensi"] for r in ringkasan})
    M = np.full((len(tipe), len(codes)), np.nan)
    for r in ringkasan:
        if r["tipe"] in tipe and r.get("siaga_jam_per_1000") is not None:
            M[tipe.index(r["tipe"]), codes.index(r["intervensi"])] = r["siaga_jam_per_1000"]
    cmap = LinearSegmentedColormap.from_list("biru", BIRU)
    fig, ax = plt.subplots(figsize=(7.2, 0.42 * len(tipe) + 0.9))
    im = ax.imshow(M, cmap=cmap, aspect="auto", vmin=0, vmax=np.nanmax(M))
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            if not np.isnan(M[i, j]):
                dark = M[i, j] > 0.55 * np.nanmax(M)
                ax.text(j, i, _id(M[i, j]), ha="center", va="center", fontsize=8,
                        color="white" if dark else INK, fontweight="bold" if codes[j] != "A" and M[i, j] == np.nanmin(M[i]) else "normal")
    ax.set_xticks(range(len(codes)), [f"{c}" for c in codes])
    ax.set_yticks(range(len(tipe)), [t.capitalize() for t in tipe])
    ax.tick_params(length=0)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks(np.arange(-.5, len(codes)), minor=True)
    ax.set_yticks(np.arange(-.5, len(tipe)), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=7)
    fig.tight_layout()
    return _png(fig)


def fig_kanal(porsi: dict[str, dict[str, float]]) -> Image:
    """Batang 100% horizontal: dari kanal mana warga pertama mendengar kabar, per tipe wilayah."""
    tipe = [t for t in URUT_TIPE if t in porsi][::-1]
    keys = [k for k in KANAL_LABEL if any((porsi[t].get(k) or 0) > 0 for t in tipe)]
    fig, ax = plt.subplots(figsize=(7.2, 0.36 * len(tipe) + 0.85))
    left = np.zeros(len(tipe))
    for k in keys:
        v = np.array([porsi[t].get(k) or 0 for t in tipe], float)
        ax.barh(range(len(tipe)), v, left=left, color=WARNA_KANAL[k], edgecolor="white", linewidth=1.5,
                label=KANAL_LABEL[k], height=0.62)
        for i, (l, w) in enumerate(zip(left, v)):
            if w >= 0.08:
                ax.text(l + w / 2, i, f"{w * 100:.0f}%", ha="center", va="center", fontsize=7.6, color=INK)
        left += v
    ax.set_yticks(range(len(tipe)), [t.capitalize() for t in tipe])
    ax.set_xlim(0, 1)
    _pct_axis(ax, "x")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=len(keys), fontsize=7.6)
    fig.tight_layout()
    return _png(fig)


def fig_sens_rentang(sens: dict, names: dict) -> Image:
    """Rentang efek tiap intervensi di semua sampel parameter (P5–median–P95)."""
    r = sens["global"]["rentang_efek_persen"]
    codes = sorted(r)[::-1]
    fig, ax = plt.subplots(figsize=(7.2, 0.38 * len(codes) + 0.8))
    for i, c in enumerate(codes):
        p5, med, p95 = r[c]
        ax.plot([p5, p95], [i, i], color=WARNA_IV.get(c, INK_2), lw=5, solid_capstyle="round", alpha=0.55)
        ax.plot([med], [i], "o", color=WARNA_IV.get(c, INK_2), ms=7, mec="white", mew=1.5)
        ax.text(max(p95, 0) + 2, i, _minus(f"{med:+.0f}%  ({p5:+.0f} s.d. {p95:+.0f})"), va="center", fontsize=7.6,
                color=INK_2)
    ax.axvline(0, color=INK_2, lw=0.8)
    ax.set_yticks(range(len(codes)), [f"{c} · {_pendek(names.get(c, ''), 34)}" for c in codes], fontsize=7.6)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:+.0f}%"))
    lo = min(v[0] for v in r.values())
    ax.set_xlim(min(lo - 5, -5), 28)
    ax.set_xlabel("perubahan beban siaga vs A (titik = median, batang = persentil 5–95)")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return _png(fig)


def fig_tornado(sens: dict) -> Image | None:
    """Parameter mana yang paling menggeser beban siaga baseline bila digeser ke batas rendah/tinggi."""
    oat, nom = sens.get("satu_per_satu") or [], sens["nominal"].get("baseline")
    if not oat or not nom:
        return None
    par: dict[str, dict] = {}
    for r in oat:
        par.setdefault(r["parameter"], {})[r["sisi"]] = r["baseline"] - nom
    items = sorted(par.items(), key=lambda kv: max(abs(v) for v in kv[1].values()))[-10:]
    fig, ax = plt.subplots(figsize=(7.2, 0.3 * len(items) + 0.8))
    for i, (k, v) in enumerate(items):
        for sisi, col in (("rendah", "#2a78d6"), ("tinggi", "#eb6834")):
            if sisi in v:
                ax.barh(i, v[sisi], color=col, height=0.6, edgecolor="white", linewidth=0.8,
                        label=f"parameter di batas {sisi}" if i == 0 else None)
    ax.axvline(0, color=INK_2, lw=0.8)
    ax.set_yticks(range(len(items)), [LABEL_PARAM.get(k, k) for k, _ in items], fontsize=7.6)
    ax.set_xlabel(f"selisih beban siaga baseline A (nominal {_id(nom)} orang-jam per 1.000)")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right", fontsize=7.4)
    fig.tight_layout()
    return _png(fig)


def fig_status_run(runs: list[dict]) -> Image | None:
    """Area bertumpuk S/E/I/Z/A per putaran (dua panel bila mode banding)."""
    runs = [r for r in runs if r.get("_grafik")]
    if not runs:
        return None
    fig, axes = plt.subplots(1, len(runs), figsize=(7.2, 2.5), sharey=True)
    axes = np.atleast_1d(axes)
    for ax, r in zip(axes, runs):
        g = r["_grafik"]
        x, P = np.asarray(g["jam"]), np.asarray(g["proporsi"])
        ax.stackplot(x, P.T, colors=[WARNA_STATUS[s] for s in STATUS], alpha=0.92, edgecolor="white", linewidth=0.4,
                     labels=[NAMA_STATUS[s] for s in STATUS])
        ax.set_xlim(x[0], x[-1])
        ax.set_ylim(0, 1)
        ax.set_title(f"{r['intervensi']['kode']} · {_pendek(r['intervensi']['nama'], 44 if len(runs) > 1 else 80)}", fontsize=8.3)
        ax.set_xlabel("jam sejak isu muncul", fontsize=7.5)
        ax.set_xticks(np.arange(0, x[-1] + 1, 12 if x[-1] <= 72 else 24))
        ax.grid(False)
    _pct_axis(axes[0])
    axes[0].set_ylabel("proporsi penduduk")
    h, lab = axes[0].get_legend_handles_labels()
    fig.legend(h, lab, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.1), fontsize=7.6)
    fig.tight_layout()
    return _png(fig)


def fig_penyebar_siaga_run(runs: list[dict]) -> Image | None:
    """Garis penyebar (I) dan siaga aktif (A) per tipe wilayah, small multiples."""
    runs = [r for r in runs if r.get("_grafik")]
    if not runs:
        return None
    tipe = list(runs[0]["_grafik"]["tipe"])
    fig, axes = plt.subplots(1, len(tipe), figsize=(7.2, 2.2), sharey=True)
    axes = np.atleast_1d(axes)
    ls = ["-", "--"]
    for ax, t in zip(axes, tipe):
        for k, r in enumerate(runs):
            g = r["_grafik"]
            x = np.asarray(g["jam"])
            for s in ("I", "A"):
                ax.plot(x, g["tipe"][t][s], color=WARNA_STATUS[s], lw=1.5, ls=ls[k % 2],
                        label=f"{NAMA_STATUS[s]}" + (f" ({r['intervensi']['kode']})" if len(runs) > 1 else ""))
        ax.set_title(t.capitalize(), fontsize=8.5)
        ax.set_xticks(np.arange(0, x[-1] + 1, 24))
        ax.set_xlabel("jam", fontsize=7.5)
        ax.grid(axis="x", visible=False)
    _pct_axis(axes[0], dec=1)
    h, lab = axes[0].get_legend_handles_labels()
    fig.legend(h, lab, loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.14), fontsize=7.6)
    fig.tight_layout()
    return _png(fig)


def fig_tipe_run(runs: list[dict]) -> Image:
    tipe = [t for t in TIPE_WILAYAH if t in runs[0]["per_tipe_wilayah"]]
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    W = 0.75 / len(runs)
    for k, r in enumerate(runs):
        v = [r["per_tipe_wilayah"][t]["siaga_jam_per_1000"] or 0 for t in tipe]
        xs = np.arange(len(tipe)) + (k - (len(runs) - 1) / 2) * W
        c = WARNA_IV.get(r["intervensi"]["kode"], ACCENT)
        ax.bar(xs, v, W * 0.88, color=c, edgecolor="white", linewidth=0.8,
               label=f"{r['intervensi']['kode']} · {_pendek(r['intervensi']['nama'], 50)}")
        for x, y in zip(xs, v):
            ax.text(x, y, _id(y), ha="center", va="bottom", fontsize=7.4, color=INK_2)
    ax.set_xticks(range(len(tipe)), [t.capitalize() for t in tipe])
    ax.set_ylabel("orang-jam siaga per 1.000 penduduk")
    ax.grid(axis="x", visible=False)
    if len(runs) > 1:
        ax.legend(loc="upper left", fontsize=7.4)
    fig.tight_layout()
    return _png(fig)


def fig_distrik_run(r: dict) -> Image | None:
    top = r.get("distrik_puncak_siaga_tertinggi") or []
    if not top:
        return None
    top = top[::-1]
    fig, ax = plt.subplots(figsize=(7.2, 0.3 * len(top) + 0.7))
    v = [d["puncak_siaga"] for d in top]
    ax.barh(range(len(top)), v, color=WARNA_STATUS["A"], height=0.62, edgecolor="white")
    for i, x in enumerate(v):
        ax.text(x, i, f"  {x * 100:.1f}%".replace(".", ","), va="center", fontsize=7.4, color=INK_2)
    ax.set_yticks(range(len(top)), [f"{d['distrik']} ({d['kabupaten']}, {d['tipe']})" for d in top], fontsize=7.6)
    _pct_axis(ax, "x", 1)
    ax.set_xlim(0, max(v) * 1.2)
    ax.set_xlabel("puncak proporsi penduduk berstatus siaga aktif")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return _png(fig)


def fig_aksi_run(runs: list[dict]) -> Image | None:
    runs = [r for r in runs if (r.get("agen_kunci") or {}).get("per_aksi")]
    if not runs:
        return None
    aksi = sorted({a for r in runs for a in r["agen_kunci"]["per_aksi"]})
    fig, ax = plt.subplots(figsize=(7.2, 0.3 * len(aksi) * len(runs) + 0.8))
    H = 0.75 / len(runs)
    for k, r in enumerate(runs):
        v = [r["agen_kunci"]["per_aksi"].get(a, 0) for a in aksi]
        ys = np.arange(len(aksi)) + (k - (len(runs) - 1) / 2) * H
        ax.barh(ys, v, H * 0.88, color=WARNA_IV.get(r["intervensi"]["kode"], ACCENT), edgecolor="white",
                label=f"{r['intervensi']['kode']}")
        for y, x in zip(ys, v):
            ax.text(x, y, f" {x}", va="center", fontsize=7.4, color=INK_2)
    ax.set_yticks(range(len(aksi)), aksi, fontsize=7.8)
    ax.set_xlabel("jumlah keputusan agen kunci")
    ax.grid(axis="y", visible=False)
    if len(runs) > 1:
        ax.legend(loc="lower right", fontsize=7.4)
    fig.tight_layout()
    return _png(fig)


def fig_keterlambatan(ket: dict) -> Image | None:
    """R3: beban siaga bila tindakan dimulai sekian jam sejak isu diketahui, pita IK 95%, garis batas waktu."""
    kurva, ring = ket.get("kurva") or {}, ket.get("ringkasan") or {}
    if not kurva:
        return None
    fig, ax = plt.subplots(figsize=(7.4, 3.3))
    gaya = ["-", "--", "-.", ":"]
    base = None
    for i, (kode, pts) in enumerate(kurva.items()):
        x = np.array([p["jam"] for p in pts])
        y, lo, hi = (np.array([p[k] for p in pts], float) for k in ("nilai", "nilai_lo", "nilai_hi"))
        warna = WARNA_IV.get(kode, ACCENT)
        ax.fill_between(x, lo, hi, color=warna, alpha=0.14, lw=0)
        ax.plot(x, y, gaya[i % 4], color=warna, lw=1.8, marker="o", ms=3, label=(ring.get(kode) or {}).get("nama", kode))
        bw = (ring.get(kode) or {}).get("batas_waktu_jam")
        if bw is not None:
            ax.axvline(bw, color=warna, ls=":", lw=1)
        base = pts[0].get("baseline", base)
    if base is not None:
        ax.axhline(base, color=MUTED, ls=":", lw=1.2, label="tanpa tindakan")
    ax.set_xlabel("jam sejak isu diketahui petugas")
    ax.set_ylabel("beban siaga (orang-jam / 1.000)")
    ax.legend(loc="upper left", ncol=2)
    fig.tight_layout()
    return _png(fig)


# ------------------------------------------------------------------ penempatan grafik
def _figures(fakta: dict, jenis: str) -> list[tuple[tuple[str, ...], str, Image]]:
    """[(kata kunci judul bagian, keterangan, gambar)]. Kata kunci pertama yang cocok menentukan tempat."""
    out = []

    def add(keys, cap, img):
        if img is not None:
            out.append((keys, cap, img))

    if jenis == "lapangan":
        mc = fakta.get("mc") or {}
        names = mc.get("intervensi", {})
        runs = (fakta.get("putaran_contoh") or {}).get("putaran") or []
        rk = mc.get("ringkasan_rata2_IK95") or []
        a_rows = {r["tipe"]: r for r in rk if r["intervensi"] == "A" and r.get("paparan_wa") is not None}
        if a_rows:
            add(("dibiarkan",), "Lewat apa warga pertama kali mendengar isu bila tidak ada tindakan, per tipe wilayah.",
                fig_kanal({t: {k: r.get(f"paparan_{k}") for k in KANAL_LABEL} for t, r in a_rows.items()}))
        if mc.get("efek_vs_baseline_A_berpasangan"):
            add(("ampuh", "langkah"), "Seberapa besar tiap langkah mengurangi warga yang siaga bertindak, dibanding tanpa "
                "tindakan. Makin ke bawah makin baik. Garis tipis menunjukkan rentang ketidakpastian; bila garis melewati "
                "0%, langkah itu belum terbukti lebih baik.", fig_efek(mc["efek_vs_baseline_A_berpasangan"], names))
        g = mc.get("_grafik") or {}
        if g.get("kurva"):
            add(("jadwal", "dibiarkan"), "Kapan warga yang siaga bertindak memuncak, rata-rata dari banyak kali percobaan. "
                "Garis putus-putus = tanpa tindakan. Bergerak sebelum puncak.", fig_kurva_mc(g["kurva"], g.get("dt_jam", 0.5), names))
        if runs:
            add(("distrik", "awasi"), "Distrik dengan warga siaga terbanyak pada putaran contoh tanpa tindakan.",
                fig_distrik_run(runs[0]))
            add(("contoh",), "Putaran contoh: keadaan warga dari jam ke jam. Kiri tanpa tindakan, kanan dengan langkah "
                "terbaik. Ungu = siaga bertindak, merah = ikut menyebarkan.", fig_status_run(runs))
            add(("tokoh",), "Apa yang dilakukan tokoh masyarakat di putaran contoh.", fig_aksi_run(runs))
        return out

    if jenis == "mc":
        names = fakta.get("intervensi", {})
        g = fakta.get("_grafik") or {}
        if g.get("kurva"):
            add(("dinamika", "penyebaran"), "Proporsi penduduk berstatus siaga aktif (A) dari waktu ke waktu, rata-rata semua "
                "putaran, per tipe wilayah. Garis putus-putus = tanpa intervensi (A).", fig_kurva_mc(g["kurva"], g.get("dt_jam", 0.5), names))
        rk = fakta.get("ringkasan_rata2_IK95") or []
        a_rows = {r["tipe"]: r for r in rk if r["intervensi"] == "A" and r.get("paparan_wa") is not None}
        if a_rows:
            add(("dinamika", "kanal", "penyebaran"), "Kanal tempat warga pertama kali mendengar isu, tanpa intervensi (A), per tipe "
                "wilayah.", fig_kanal({t: {k: r.get(f"paparan_{k}") for k in KANAL_LABEL} for t, r in a_rows.items()}))
        if fakta.get("efek_vs_baseline_A_berpasangan"):
            add(("perbandingan", "intervensi", "efek"), "Perubahan beban siaga aktif dibanding tanpa intervensi (A), per tipe "
                "wilayah. Garis tipis = interval kepercayaan 95% (seed berpasangan). Makin ke bawah makin baik.",
                fig_efek(fakta["efek_vs_baseline_A_berpasangan"], names))
        if rk:
            add(("perbandingan", "intervensi", "tipe wilayah"), "Beban siaga aktif (orang-jam per 1.000 penduduk) per tipe wilayah "
                "dan intervensi. Angka tebal = intervensi terbaik di baris itu.", fig_heatmap(rk, names))
        sens = fakta.get("analisis_sensitivitas")
        if sens and sens.get("global"):
            add(("sensitiv", "keterbatasan", "validasi"), "Rentang efek tiap intervensi ketika parameter yang belum terkalibrasi "
                "digeser bersamaan (sampel Latin hypercube).", fig_sens_rentang(sens, names))
            add(("sensitiv", "keterbatasan", "validasi"), "Parameter yang paling menggeser beban siaga baseline bila diset ke "
                "batas rendah atau tinggi rentangnya (yang lain tetap).", fig_tornado(sens))
    else:
        runs = fakta.get("putaran") or []
        if not runs:
            return out
        add(("dinamika", "penyebaran"), "Komposisi status warga dari waktu ke waktu (semua wilayah).", fig_status_run(runs))
        add(("dinamika", "penyebaran"), "Proporsi penyebar (I) dan siaga aktif (A) per tipe wilayah"
            + (" (garis putus-putus = putaran kedua)." if len(runs) > 1 else "."), fig_penyebar_siaga_run(runs))
        kanal = (runs[0].get("kanal") or {}).get("paparan_hoaks_pertama")
        if kanal:
            add(("dinamika", "kanal", "penyebaran"), "Kanal tempat warga pertama kali mendengar isu"
                + (f" (intervensi {runs[0]['intervensi']['kode']})." if len(runs) > 1 else "."), fig_kanal(kanal))
        add(("perbandingan", "tipe wilayah"), "Beban siaga aktif per tipe wilayah (orang-jam per 1.000 penduduk).", fig_tipe_run(runs))
        add(("perbandingan", "tipe wilayah", "dinamika"), "Distrik dengan puncak siaga aktif tertinggi"
            + (f" (intervensi {runs[0]['intervensi']['kode']})." if len(runs) > 1 else "."), fig_distrik_run(runs[0]))
        add(("agen kunci", "tokoh"), "Keputusan agen kunci (tokoh gereja, adat, kepala kampung, dll.) menurut jenis tindakan.",
            fig_aksi_run(runs))
    return out


# ------------------------------------------------------------------ sampul & halaman
def _kartu_semua(fakta: dict) -> dict:
    kartu = fakta.get("kartu_tindakan_per_tipe_wilayah") or {}
    return kartu.get("semua") or next(iter(kartu.values()), {})


def _dampak(u: dict) -> str:
    if u.get("menurunkan") and u.get("efek_persen") is not None:
        return f"turun ±{_id(abs(u['efek_persen']))}%"
    return "belum terbukti"


def _kpi(fakta: dict, jenis: str) -> list[tuple[str, str]]:
    if jenis == "lapangan":
        k = _kartu_semua(fakta)
        u, b = k.get("utamakan") or {}, k.get("tanpa_intervensi") or {}
        out = []
        if u:
            nilai = _minus(f"-{_id(abs(u['efek_persen']))}%") if u.get("menurunkan") else "belum ada"
            out.append((nilai, f"warga siaga dengan langkah utama {u['kode']}: {_pendek(u.get('nama', ''), 40)} "
                        f"(keyakinan {u.get('keyakinan')})"))
        if b.get("warga_pernah_dengar") is not None:
            out.append((f"{_id(b['warga_pernah_dengar'] * 100)}%", "warga mendengar isu bila dibiarkan"))
        if b.get("rata2_jam_sampai_memanas") is not None:
            out.append((f"jam ke-{_id(b['rata2_jam_sampai_memanas'])}", "rata-rata distrik mulai memanas bila dibiarkan"))
        runs = (fakta.get("putaran_contoh") or {}).get("putaran") or []
        if runs:
            s = runs[0]["semua_wilayah"]
            out.append((f"{s['distrik_eskalasi']} distrik", "memanas di putaran contoh tanpa tindakan"))
        return out[:4]
    if jenis == "mc":
        eff = [e for e in fakta.get("efek_vs_baseline_A_berpasangan", []) if e["tipe"] == "semua" and e.get("efek_persen") is not None]
        rk = [r for r in fakta.get("ringkasan_rata2_IK95", []) if r["tipe"] == "semua"]
        out = []
        if eff:
            best = min(eff, key=lambda e: e["efek_persen"])
            out.append((_minus(f"{best['intervensi']} · {_id(best['efek_persen'])}%"), f"intervensi terbaik: "
                        f"{_pendek(fakta['intervensi'].get(best['intervensi'], ''), 52)}"))
        base = next((r for r in rk if r["intervensi"] == "A"), None)
        if base:
            out.append((_id(base["siaga_jam_per_1000"]), "beban siaga tanpa intervensi (orang-jam per 1.000 penduduk)"))
            out.append((f"{_id(base['jangkauan_hoaks'] * 100)}%", "warga pernah mendengar isu tanpa intervensi"))
        sens = (fakta.get("analisis_sensitivitas") or {}).get("global")
        if sens:
            out.append((f"{_id(sens['porsi_sampel_terbaik_tetap'] * 100)}%", "sampel parameter yang tetap memilih intervensi terbaik"))
        else:
            pr = [r for r in fakta.get("peringkat_dan_stabilitas_bootstrap", []) if r["tipe"] == "semua" and r["peringkat"] == 1]
            if pr:
                out.append((f"{_id(pr[0]['stabilitas'] * 100)}%", "stabilitas peringkat teratas (bootstrap)"))
        return out[:4]
    r = fakta["putaran"][0]
    s = r["semua_wilayah"]
    out = [(f"{_id(s['puncak_penyebar'] * 100, 1)}%", f"puncak penyebar, jam ke-{_id(s['jam_puncak_penyebar'])}"),
           (f"{_id(s['puncak_siaga'] * 100, 1)}%", f"puncak siaga aktif, jam ke-{_id(s['jam_puncak_siaga'])}"),
           (_id(s["siaga_jam_per_1000"]), "beban siaga (orang-jam per 1.000 penduduk)"),
           (f"{s['distrik_eskalasi']} / {s['distrik']}", "distrik melewati ambang risiko")]
    if s.get("pernah_dengar") is not None:
        out[2] = (f"{_id(s['pernah_dengar'] * 100)}%", "warga pernah mendengar isu")
    return out


def _meta(fakta: dict, jenis: str, info: dict) -> list[tuple[str, str]]:
    sc = fakta.get("skenario") or (fakta.get("putaran") or [{}])[0].get("skenario") or {}
    if jenis == "lapangan":
        a = fakta.get("alur") or {}
        eks = len(fakta.get("eksplorasi_tanpa_intervensi") or [])
        rows = [("Cara analisis", f"Alur otomatis: {eks} putaran penjajakan → "
                 + (f"{a.get('pilot')} percontohan tokoh → " if a.get("tabel_beku") else "")
                 + f"{a.get('putaran_mc')} kali percobaan per langkah → putaran contoh"),
                ("Jumlah warga simulasi", _id(a.get("agen") or 0)),
                ("Langkah yang diuji", "; ".join(f"{k} {v}" for k, v in (a.get("intervensi") or {}).items())),
                ("Tokoh kunci", f"{a.get('llm_tokoh')}" + (f" (keputusan dibekukan: {a['tabel_beku']})" if a.get("tabel_beku")
                                                         else " (aturan bawaan model)"))]
    elif jenis == "mc":
        p = fakta.get("pengaturan", {})
        iv = ", ".join(fakta.get("intervensi", {}))
        rows = [("Jenis analisis", f"Monte Carlo · {p.get('putaran')} putaran per intervensi"),
                ("Jumlah agen", _id(p.get("agen") or 0)), ("Intervensi diuji", iv),
                ("Agen kunci", f"{p.get('mode_llm')}" + (f" (tabel beku {p['tabel_beku']})" if p.get("tabel_beku") else ""))]
    else:
        runs = fakta["putaran"]
        p = runs[0]["pengaturan"]
        rows = [("Jenis analisis", "Putaran tunggal" + (" · mode banding" if len(runs) > 1 else "")),
                ("Jumlah agen", _id(p.get("agen") or 0)),
                ("Intervensi", " vs ".join(f"{r['intervensi']['kode']} ({r['intervensi']['nama']})" for r in runs)),
                ("Agen kunci", f"{p.get('mode_agen_kunci')}" + (f" · {p['model']}" if p.get("model") else ""))]
    mulai = sc.get("mulai") or {}
    return [("Skenario", sc.get("nama", "–"))] + rows + [
        ("Mulai isu", f"{mulai.get('hari', '')} pukul {mulai.get('jam', '')}.00" if mulai else "–"),
        ("Narasi ditulis oleh", f"{info.get('model', '–')} ({info.get('penyedia', '–')})"),
        ("Tanggal", datetime.now().strftime("%d-%m-%Y %H.%M"))]


def _cover_flow(fakta: dict, jenis: str, info: dict, st: dict) -> list:
    flow = [Spacer(1, 4)]
    kpi = _kpi(fakta, jenis)
    if kpi:
        cells = [[Paragraph(escape(v), st["kpi_num"]), Spacer(1, 2), Paragraph(escape(l), st["kpi_lbl"])] for v, l in kpi]
        w = FRAME_W / len(cells)
        t = Table([cells], colWidths=[w] * len(cells))
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(ACCENT_SOFT)),
                               ("LINEAFTER", (0, 0), (-2, -1), 2, colors.white),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 9),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
        flow += [Paragraph("ANGKA KUNCI", st["meta_k"]), Spacer(1, 4), t, Spacer(1, 16)]
    rows = [[Paragraph(escape(k), st["meta_k"]), Paragraph(escape(str(v)), st["meta_v"])] for k, v in _meta(fakta, jenis, info)]
    m = Table(rows, colWidths=[42 * mm, FRAME_W - 42 * mm])
    m.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#e6e9ef")),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    flow += [Paragraph("PENGATURAN SIMULASI", st["meta_k"]), Spacer(1, 4), m, Spacer(1, 18)]
    if jenis == "lapangan":
        teks = ("<b>Cara membaca panduan ini.</b> Isinya hasil latihan di komputer dengan warga <b>fiktif</b>, diulang "
                "berkali-kali agar tidak bergantung pada kebetulan. Pakai untuk <b>memilih langkah</b> dan menyiapkan tim, "
                "bukan untuk meramal kejadian. Mulai dari <b>kartu tindakan</b> di halaman berikut. Teks ditulis otomatis "
                "oleh AI; periksa bersama tokoh dan aparat setempat sebelum dipakai.")
    else:
        teks = ("<b>Cara membaca laporan ini.</b> Angka berasal dari simulasi berbasis agen dengan warga <b>fiktif</b>; geografi "
                "kampung masih perkiraan dan parameter belum dikalibrasi penuh. Gunakan hasilnya untuk <b>membandingkan</b> pilihan "
                "intervensi, bukan sebagai ramalan jumlah orang atau kejadian. Narasi ditulis otomatis oleh model AI dari angka "
                "simulasi dan perlu diperiksa tim ahli serta mitra lokal sebelum dipakai sebagai dasar kebijakan.")
    flow.append(_callout(teks, st, "warn"))
    return flow


def _keterlambatan_flow(fakta: dict, st: dict) -> list:
    """R3 "Kapan harus bertindak": kalimat dihitung Python dari kurva keterlambatan, bukan oleh AI."""
    ket = fakta.get("keterlambatan") or {}
    ring = ket.get("ringkasan") or {}
    if not ring:
        return []
    out = [CondPageBreak(70 * mm), Paragraph("Kapan harus bertindak", st["h2"]),
           Paragraph("Dihitung langsung dari simulasi: tindakan yang sama dijalankan mulai jam ke-0, 2, 4, … sejak isu "
                     "<b>diketahui petugas</b>, dibandingkan pada kejadian yang sama persis. Bentuk kurva lebih bisa dipercaya "
                     "daripada angka persennya: pesan utamanya <b>lebih cepat jauh lebih baik</b>.", st["body"])]
    for r in ring.values():
        out.append(Paragraph(escape(r.get("kalimat", "")), st["bullet"], bulletText="•"))
        for w in r.get("peringatan") or []:
            out.append(Paragraph(f"<i>{escape(w)}</i>", st["bullet"], bulletText="–"))
    img = fig_keterlambatan(ket)
    if img is not None:
        out += [Spacer(1, 4), KeepTogether([img, Paragraph("<b>Harga keterlambatan.</b> Garis = rata-rata beban siaga, pita = "
                                                            "IK 95%, garis titik tegak = batas waktu efektif (manfaat turun di bawah "
                                                            "25% manfaat bila dilakukan segera).", st["caption"])])]
    return out


def _kartu_flow(fakta: dict, st: dict) -> list:
    """Kartu tindakan per tipe wilayah, dihitung langsung dari Monte Carlo (tidak lewat AI)."""
    kartu = fakta.get("kartu_tindakan_per_tipe_wilayah") or {}
    if not kartu:
        return []
    rows = [["Tipe wilayah", "Utamakan", "Dampak pada warga siaga", "Keyakinan", "Bila dibiarkan", "Jangan andalkan"]]
    for t, k in kartu.items():
        u, b = k["utamakan"], k.get("tanpa_intervensi") or {}
        biar = []
        if b.get("warga_pernah_dengar") is not None:
            biar.append(f"{_id(b['warga_pernah_dengar'] * 100)}% dengar")
        if b.get("kanal_utama"):
            biar.append(f"masuk lewat {b['kanal_utama']}")
        if b.get("rata2_jam_sampai_memanas") is not None:
            biar.append(f"memanas ±jam {_id(b['rata2_jam_sampai_memanas'])}")
        rows.append([f"**{t.capitalize()}**", f"{u['kode']} · {u.get('nama', '')}", _minus(_dampak(u)), u["keyakinan"],
                     ", ".join(biar) or "–", f"{k['paling_lemah']['kode']} · {k['paling_lemah'].get('nama', '')}"])
    return [Paragraph("Kartu tindakan per tipe wilayah", st["h2"]),
            Paragraph("Dihitung langsung dari hasil banyak kali percobaan, tanpa AI. <b>Keyakinan tinggi</b> = hasilnya "
                      "konsisten di hampir semua percobaan; <b>rendah</b> = belum terbukti lebih baik dari tanpa tindakan.",
                      st["body"]), Spacer(1, 4), _table(rows, st), Spacer(1, 6)]


def build_pdf(md: str, fakta: dict, jenis: str, info: dict, path: str | Path) -> Path:
    _setup_fonts()
    st = _styles()
    path = Path(path)
    title, sections = parse_markdown(md, st)
    sc = fakta.get("skenario") or (fakta.get("putaran") or [{}])[0].get("skenario") or {}
    title = title or f"Laporan simulasi: {sc.get('nama', '')}"
    sub = sc.get("nama", "")
    if jenis == "lapangan":
        sub = "Untuk Diskominfo, aparat distrik dan kampung, tokoh gereja dan adat, serta relawan"
    short = title if len(title) <= 70 else title[:67] + "…"

    def cover(c, doc):
        band = 88 * mm
        c.saveState()
        c.setFillColor(colors.HexColor(ACCENT_DARK))
        c.rect(0, PAGE_H - band, PAGE_W, band, fill=1, stroke=0)
        c.setFillColor(colors.HexColor(ACCENT))
        c.rect(0, PAGE_H - band, PAGE_W, 2.2 * mm, fill=1, stroke=0)
        y = PAGE_H - 24 * mm
        kicker = "PANDUAN UNTUK TIM LAPANGAN" if jenis == "lapangan" else "LAPORAN ANALISIS SIMULASI"
        for txt, style in ((f"INDONESIA SINTETIS · {kicker} · DRAF", st["cover_kicker"]),
                           (escape(title), st["cover_title"]), (escape(sub), st["cover_sub"])):
            p = Paragraph(txt, style)
            _, h = p.wrap(FRAME_W, band)
            y -= h
            p.drawOn(c, MARGIN, y)
            y -= 5 * mm
        _footer(c, doc)
        c.restoreState()

    def later(c, doc):
        c.saveState()
        c.setStrokeColor(colors.HexColor(LINE))
        c.setLineWidth(0.6)
        c.line(MARGIN, PAGE_H - 12 * mm, PAGE_W - MARGIN, PAGE_H - 12 * mm)
        c.setFont("Sans", 7.8)
        c.setFillColor(colors.HexColor(MUTED))
        c.drawString(MARGIN, PAGE_H - 10.2 * mm, f"Indonesia Sintetis · {short}")
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 10.2 * mm, f"DRAF · {TANDA_SIMULASI}")
        _footer(c, doc)
        c.restoreState()

    def _footer(c, doc):
        c.setFont("Sans", 7.8)
        c.setFillColor(colors.HexColor(MUTED))
        c.drawString(MARGIN, 10 * mm, f"{TANDA_SIMULASI} · agen fiktif · {_vm['id']} · {_vm['status_validasi'][0]}")
        c.drawRightString(PAGE_W - MARGIN, 10 * mm, f"Halaman {doc.page}")

    _vm = versi_model()
    doc = BaseDocTemplate(str(path), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=18 * mm,
                          bottomMargin=17 * mm, title=title, author="Indonesia Sintetis", subject=sub,
                          creator="Indonesia Sintetis — laporan AI")
    doc.addPageTemplates([
        PageTemplate("cover", [Frame(MARGIN, 17 * mm, FRAME_W, PAGE_H - 88 * mm - 26 * mm, id="c", showBoundary=0)], onPage=cover),
        PageTemplate("isi", [Frame(MARGIN, 17 * mm, FRAME_W, PAGE_H - 35 * mm, id="i")], onPage=later),
    ])

    story = _cover_flow(fakta, jenis, info, st) + [NextPageTemplate("isi"), PageBreak()]
    if jenis == "lapangan":
        story += _kartu_flow(fakta, st) + _keterlambatan_flow(fakta, st)
    figs = _figures(fakta, jenis)
    placed = [False] * len(figs)
    nomor = [0]

    def fig_block(i: int) -> list:
        keys, cap, img = figs[i]
        nomor[0] += 1
        placed[i] = True
        return [Spacer(1, 4), KeepTogether([img, Paragraph(f"<b>Gambar {nomor[0]}.</b> {escape(cap)}", st["caption"])])]

    for head, flow in sections:
        if head:
            story += [CondPageBreak(40 * mm), Paragraph(_inline(head), st["h2"])]
        ekor = len(flow)
        while ekor and getattr(flow[ekor - 1], "_penutup", False):
            ekor -= 1
        story += flow[:ekor]
        h = head.lower()
        for i, (keys, _, _) in enumerate(figs):
            if not placed[i] and h and next((k for k in keys if k in h), None):
                # bagian pertama yang cocok dengan kata kunci paling depan didahulukan
                if keys[0] in h or not any(keys[0] in s[0].lower() for s in sections):
                    story += fig_block(i)
        story += flow[ekor:]
    sisa = [i for i, p in enumerate(placed) if not p]
    if sisa:
        story += [CondPageBreak(60 * mm), Paragraph("Lampiran: grafik pendukung", st["h2"])]
        for i in sisa:
            story += fig_block(i)
    doc.build(story)
    return path
