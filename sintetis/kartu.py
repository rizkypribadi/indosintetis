"""R1 Kartu Respons Cetak per kabupaten: satu lembar A4 bolak-balik yang dibawa tim ke lapangan dan bisa
dipakai tanpa listrik, sinyal, atau laptop.

  Depan  "Bila isu muncul": kepala kartu, jendela waktu sejak isu diketahui (R3), kanal paling ampuh per
         tipe wilayah, distrik yang perlu diawasi, tanda bahaya.
  Belakang "Siapa menghubungi siapa": pohon kontak per PERAN dengan baris kosong untuk ditulis tangan,
         yang jangan dilakukan, pengingat prebunking, catatan kaki (SIMULASI — BUKAN PRAKIRAAN, versi model,
         status validasi).

Kalimat berasal dari config/templat_kartu.yaml (templat tetap yang ditinjau), angka dari pustaka (R2a) dan
kurva keterlambatan (R3). Tidak ada LLM. Hitam-putih: tidak ada warna sebagai satu-satunya pembeda,
huruf minimal 10 pt.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

from . import TIPE_WILAYAH, pustaka
from .config import CONFIG_DIR, OUT_DIR, TANDA_SIMULASI, load_interventions, load_region, load_yaml, versi_model
from .keterlambatan import jenis_utama

KANAL_KOLOM = {"paparan_wa": "WhatsApp", "paparan_fb": "Facebook", "paparan_tatap_muka": "tatap muka", "paparan_radio": "radio"}
BERWAKTU = ("gereja_adat", "radio", "tim_keliling", "medsos_resmi", "penindakan")


def _pct(x, dec=0) -> str:
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{dec}f}%"


def _bul(v) -> str:
    v = round(float(v))
    return f"{v:d}".replace("-", "−")


def _rng(a, b, suf="%") -> str:
    return f"{_bul(min(a, b))} s.d. {_bul(max(a, b))}{suf}"


def _teks(t: dict | str | None, bahasa: str) -> str:
    if isinstance(t, str) or t is None:
        return t or ""
    return t.get(bahasa) or t.get("id") or ""


def muat_templat(bahasa: str = "id") -> dict:
    t = load_yaml(CONFIG_DIR / "templat_kartu.yaml")
    if bahasa != "id" and not (t.get("ditinjau") or {}).get(bahasa):
        raise ValueError(f"Templat kartu bahasa '{bahasa}' belum ditinjau penutur asli (templat_kartu.yaml: ditinjau.{bahasa})")
    return t


def kontak(kab_id: str) -> list[dict]:
    k = load_yaml(CONFIG_DIR / "kontak_peran.yaml")
    region = load_region()
    radio = ", ".join(s["nama"] for s in region.get("radio", []) if kab_id in s["jangkauan"]) or "radio setempat"
    daftar = k.get(kab_id) or k["bawaan"]
    return [{"peran": d["peran"].replace("{radio}", radio),
             "menghubungi": [x.replace("{radio}", radio) for x in d.get("menghubungi", [])]} for d in daftar]


def data_kartu(kab_id: str, P: dict | None = None, bahasa: str = "id") -> dict:
    """Semua isi kartu sebagai data (dipakai PDF dan tes)."""
    region = load_region()
    kab = next(k for k in region["kabupaten"] if k["id"] == kab_id)
    tm = muat_templat(bahasa)
    P = P if P is not None else pustaka.muat()
    iv = load_interventions()
    radio = ", ".join(s["nama"] for s in region.get("radio", []) if kab_id in s["jangkauan"]) or "radio setempat"
    out = {"kabupaten": kab_id, "nama": kab["nama"], "tanggal": datetime.now().strftime("%d-%m-%Y"),
           "versi_model": versi_model(), "tanda": TANDA_SIMULASI, "templat_ditinjau": bool((tm.get("ditinjau") or {}).get(bahasa)),
           "bahasa": bahasa, "kontak": kontak(kab_id), "jangan": [_teks(x, bahasa) for x in tm["jangan"]],
           "tanda_bahaya": [_teks(x, bahasa) for x in tm["tanda_bahaya"]], "kontak_peneliti": tm.get("kontak_peneliti", "")}
    try:
        from .kajian import lencana_validasi

        out["lencana"] = lencana_validasi(kab_id)
    except Exception:
        out["lencana"] = None
    out["komposisi"] = _komposisi(kab_id)
    if P is None:
        out["pustaka"] = None
        out["catatan"] = "Pustaka skenario belum dibangun: jendela waktu memakai urutan bawaan templat, tanpa angka."
    else:
        out["pustaka"] = {"versi": P["status"]["versi"], "usang": P["status"]["usang"]}
    ind = P["indeks"] if P is not None else pd.DataFrame()
    sel_kab = ind[ind["kabupaten_asal"] == kab_id] if len(ind) else ind
    out["jumlah_skenario"] = int(sel_kab["sel"].nunique()) if len(sel_kab) else 0
    rows = sel_kab[(sel_kab["tipe"] == kab_id)] if len(sel_kab) else sel_kab
    ket = P["keterlambatan"] if P is not None else pd.DataFrame()
    ket_kab = ket[ket["sel"].isin(sel_kab["sel"].unique()) & ket["wilayah"].isin([kab_id, "semua"])] if len(ket) and len(sel_kab) else pd.DataFrame()

    # keyakinan keseluruhan: porsi sel di mana langkah terbaik berkeyakinan tinggi/sedang
    kep_all = []
    # --- langkah per jenis intervensi (B & C sama-sama medsos: ambil yang terbaik)
    langkah = {}
    for kode, spec in iv.items():
        j = jenis_utama(spec)
        if kode == "A" or j not in BERWAKTU:
            continue
        r = rows[rows["intervensi"] == kode] if len(rows) else rows
        ef = r["efek_persen"].dropna() if len(r) else pd.Series(dtype=float)
        lo = r["efek_lo"].dropna() if len(r) and "efek_lo" in r else pd.Series(dtype=float)
        hi = r["efek_hi"].dropna() if len(r) and "efek_hi" in r else pd.Series(dtype=float)
        info = {"kode": kode, "jenis": j, "efek": float(ef.mean()) if len(ef) else None,
                # rentang: median batas IK 95% antar-skenario (satu skenario → IK 95% skenario itu)
                "efek_lo": float(lo.median()) if len(lo) else None, "efek_hi": float(hi.median()) if len(hi) else None,
                "keyakinan": _kep(r["keyakinan"]) if len(r) else "belum dihitung", "batas": None, "harga": None}
        k = ket_kab[ket_kab["kode"] == kode] if len(ket_kab) else ket_kab
        if len(k):
            bw = pd.to_numeric(k["batas_waktu_jam"], errors="coerce").dropna()
            hg = pd.to_numeric(k["harga_per_jam_persen"], errors="coerce").dropna()
            info["batas"] = float(bw.median()) if len(bw) else None
            if len(hg):
                info["harga"] = (float(hg.median()), float(pd.to_numeric(k["harga_lo"], errors="coerce").median()),
                                 float(pd.to_numeric(k["harga_hi"], errors="coerce").median()), _kep(k["keyakinan"]))
        if j not in langkah or (info["efek"] is not None and (langkah[j]["efek"] is None or info["efek"] < langkah[j]["efek"])):
            langkah[j] = info
        if info["keyakinan"] not in ("belum dihitung",):
            kep_all.append(info["keyakinan"])
    win = {w["id"]: {"label": w["label"], "langkah": []} for w in tm["jendela"]}
    first = next(iter(win))
    win[first]["langkah"].append({"teks": _teks(tm["langkah_pertama"], bahasa), "angka": ""})
    urut = sorted(langkah.values(), key=lambda x: (x["batas"] if x["batas"] is not None else 99, x["efek"] if x["efek"] is not None else 0))
    for x in urut:
        if x["efek"] is not None and x["efek"] >= 0:
            continue                                     # tidak terbukti menurunkan beban siaga di kabupaten ini
        t = tm["langkah"][x["jenis"]]
        if x["batas"] is not None:
            wid = next((w["id"] for w in tm["jendela"] if x["batas"] <= w["batas_maks"]), tm["jendela"][-1]["id"])
        else:
            wid = t.get("jendela_bawaan", first)
        angka = "Efek belum dihitung di pustaka." if P is not None else ""
        if x["efek"] is not None:
            rg = f" (rentang {_rng(-x['efek_hi'], -x['efek_lo'])})" if x["efek_lo"] is not None else ""
            angka = f"Menurunkan beban siaga ±{_bul(-x['efek'])}%{rg}, keyakinan {x['keyakinan']}."
            if x["batas"] is not None:
                angka += f" Paling berguna sebelum ±jam {x['batas']:.0f}."
        win[wid]["langkah"].append({"teks": _teks(t, bahasa).replace("{radio}", radio), "angka": angka})
    win[list(win)[-1]]["langkah"].append({"teks": _teks(tm["pantau"], bahasa), "angka": ""})
    out["jendela"] = list(win.values())
    hg = [x for x in urut if x["harga"] and x["harga"][0] > 0]
    if hg:
        h = hg[0]["harga"]
        out["harga"] = (f"Setiap jam menunda menambah beban siaga ±{_bul(h[0])}% (rentang {_rng(h[1], h[2])}, "
                        f"keyakinan {h[3]}). Lebih cepat jauh lebih baik.")
    else:
        out["harga"] = "Lebih cepat jauh lebih baik: makin lama ditunda, makin banyak warga yang sudah percaya dan bersiaga."
    out["keyakinan"] = ("tinggi" if kep_all and kep_all.count("tinggi") >= len(kep_all) / 2 else
                        "sedang" if any(k in ("tinggi", "sedang") for k in kep_all) else "rendah")
    # --- kanal paling ampuh per tipe wilayah di kabupaten ini
    out["kanal"] = []
    tipe_ada = [t for t, n in out["komposisi"].items() if n]
    for t in tipe_ada:
        r = sel_kab[(sel_kab["tipe"] == t) & (sel_kab["intervensi"] == "A")] if len(sel_kab) else sel_kab
        if not len(r):
            continue
        porsi = {v: float(r[k].mean()) for k, v in KANAL_KOLOM.items() if k in r and r[k].notna().any()}
        if porsi:
            kn = max(porsi, key=porsi.get)
            out["kanal"].append(tm["kanal"][kn].format(tipe=t, porsi=_pct(porsi[kn])))
    # --- distrik yang perlu diawasi
    out["distrik"] = []
    dist = P["distrik"] if P is not None else pd.DataFrame()
    if len(dist) and len(sel_kab):
        dd = dist[dist["sel"].isin(sel_kab["sel"].unique()) & (dist["intervensi"] == "A") & (dist["kabupaten"] == kab_id)]
        g = dd.groupby("nama").agg(peluang=("peluang_memanas", "mean"), p_lo=("peluang_memanas", lambda s: s.quantile(0.1)),
                                    p_hi=("peluang_memanas", lambda s: s.quantile(0.9)), jam=("jam_median", "median"),
                                    tipe=("tipe", "first"))
        for nama, r in g.sort_values("peluang", ascending=False).head(5).iterrows():
            out["distrik"].append({"nama": nama, "tipe": r["tipe"], "peluang": float(r["peluang"]), "lo": float(r["p_lo"]),
                                   "hi": float(r["p_hi"]), "jam": None if np.isnan(r["jam"]) else float(r["jam"])})
    # --- prebunking (E)
    re_ = rows[rows["intervensi"] == "E"] if len(rows) else rows
    if len(re_) and re_["efek_persen"].notna().any():
        ef = re_["efek_persen"].dropna()
        rg = (f" (rentang {_rng(-re_['efek_hi'].median(), -re_['efek_lo'].median())})"
              if "efek_lo" in re_ and re_["efek_lo"].notna().any() else "")
        efek = f"pada skenario serupa menurunkan beban siaga ±{_bul(-ef.mean())}%{rg}, keyakinan {_kep(re_['keyakinan'])}"
    else:
        efek = "efeknya belum dihitung untuk kabupaten ini"
    out["prebunking"] = _teks(tm["prebunking"], bahasa).format(efek=efek)
    try:                                   # R4: rencana yang disimpan dari Perencana untuk kabupaten ini
        from .perencana import rencana_tersimpan

        rs = [r for r in rencana_tersimpan(kab_id) if r.get("versi_model") == out["versi_model"]["id"]]
        out["rencana"] = rs[0] if rs else None
    except Exception:
        out["rencana"] = None
    return out


def _kep(s: pd.Series) -> str:
    s = [x for x in s if isinstance(x, str)]
    if not s:
        return "rendah"
    for k in ("tinggi", "sedang"):
        if s.count(k) >= len(s) / 2:
            return k
    return "sedang" if any(x in ("tinggi", "sedang") for x in s) else "rendah"


def _komposisi(kab_id: str) -> dict:
    from .population import build_geography

    geo = build_geography(load_region(), np.random.default_rng([42, 1]))
    k = geo.kab_index(kab_id)
    t = np.bincount(geo.distrik_tipe[geo.distrik_kab == k], minlength=len(TIPE_WILAYAH))
    return {TIPE_WILAYAH[i]: int(t[i]) for i in range(len(TIPE_WILAYAH))}


# ---------------------------------------------------------------------------------------------- PDF
def buat_kartu(kab_id: str, path: str | Path | None = None, P: dict | None = None, bahasa: str = "id") -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, KeepInFrame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

    from .laporan_pdf import _setup_fonts

    _setup_fonts()
    d = data_kartu(kab_id, P, bahasa)
    vm = d["versi_model"]
    path = Path(path) if path else OUT_DIR / "kartu" / vm["id"] / f"kartu_{kab_id}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    W, H = A4
    M = 13 * mm
    FW = W - 2 * M
    ink = colors.black
    st = {
        "judul": ParagraphStyle("j", fontName="Sans-Bold", fontSize=17, leading=20, textColor=ink),
        "sub": ParagraphStyle("s", fontName="Sans", fontSize=10, leading=12.5, textColor=ink),
        "h": ParagraphStyle("h", fontName="Sans-Bold", fontSize=12, leading=15, textColor=ink, spaceBefore=6, spaceAfter=2),
        "b": ParagraphStyle("b", fontName="Sans", fontSize=10, leading=12.6, textColor=ink),
        "bi": ParagraphStyle("bi", fontName="Sans", fontSize=10, leading=12.6, textColor=ink, leftIndent=16, bulletIndent=1),
        "kecil": ParagraphStyle("k", fontName="Sans", fontSize=10, leading=12, textColor=ink),
        "tanda": ParagraphStyle("t", fontName="Sans-Bold", fontSize=11, leading=13, textColor=ink, alignment=1),
    }

    def P_(t, s="b"):
        return Paragraph(t, st[s])

    def kotak(isi, tebal=1.2):
        t = Table([[isi]], colWidths=[FW])
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), tebal, ink), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 4),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        return t

    komp = ", ".join(f"{n} distrik {t}" for t, n in d["komposisi"].items() if n)
    kepala = [P_(f"KARTU RESPONS · {escape(d['nama']).upper()}", "judul"),
              P_(f"{escape(komp)} · dicetak {d['tanggal']} · keyakinan keseluruhan: <b>{d['keyakinan']}</b>", "sub"),
              P_(f"<b>{TANDA_SIMULASI}</b> · {escape(vm['teks'])}", "sub")]
    if d.get("pustaka") and d["pustaka"]["usang"]:
        kepala.append(P_("<b>PERINGATAN: pustaka usang untuk versi model ini. Cetak ulang setelah pustaka dibangun ulang.</b>", "sub"))
    if not d["templat_ditinjau"]:
        kepala.append(P_("<b>DRAF</b>: kalimat kartu belum ditinjau penutur asli dan praktisi lapangan. Belum untuk dibagikan.", "sub"))
    if d.get("catatan"):
        kepala.append(P_(escape(d["catatan"]), "sub"))

    depan = [kotak(kepala, 1.6), Spacer(1, 4), P_("BILA ISU MUNCUL — dihitung sejak isu DIKETAHUI petugas", "h")]
    jrows = []
    for w in d["jendela"]:
        isi = []
        for i, l in enumerate(w["langkah"], 1):
            isi.append(Paragraph(f"{escape(l['teks'])}" + (f" <i>{escape(l['angka'])}</i>" if l["angka"] else ""), st["bi"],
                                 bulletText=f"{i}."))
        jrows.append([P_(f"<b>{escape(w['label'])}</b>"), isi or P_("–")])
    t = Table(jrows, colWidths=[22 * mm, FW - 22 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.8, ink), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    depan += [t, Spacer(1, 3), kotak([P_(f"<b>Harga keterlambatan.</b> {escape(d['harga'])}")])]
    depan.append(P_("KANAL PALING AMPUH", "h"))
    depan += [Paragraph(escape(k), st["bi"], bulletText="•") for k in d["kanal"]] or [P_("Belum dihitung untuk kabupaten ini.")]
    depan.append(P_("DISTRIK YANG PERLU DIAWASI (pada skenario serupa, tanpa tindakan)", "h"))
    if d["distrik"]:
        rows = [[P_("<b>Distrik</b>"), P_("<b>Tipe</b>"), P_("<b>Peluang memanas</b>"), P_("<b>Biasanya memanas</b>")]]
        for x in d["distrik"]:
            rows.append([P_(escape(x["nama"])), P_(x["tipe"]), P_(f"{_pct(x['peluang'])} (rentang {_pct(x['lo'])}–{_pct(x['hi'])})"),
                         P_(f"±jam ke-{x['jam']:.0f} sejak isu muncul" if x["jam"] is not None else "–")])
        t = Table(rows, colWidths=[FW * 0.32, FW * 0.15, FW * 0.30, FW * 0.23])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, ink), ("LINEBELOW", (0, 0), (-1, 0), 1.2, ink),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
        depan.append(t)
    else:
        depan.append(P_("Belum dihitung untuk kabupaten ini."))
    depan.append(P_("TANDA BAHAYA: isu mulai berubah menjadi tindakan", "h"))
    depan += [Paragraph(escape(x), st["bi"], bulletText="[  ]") for x in d["tanda_bahaya"]]

    belakang = [kotak([P_(f"SIAPA MENGHUBUNGI SIAPA · {escape(d['nama']).upper()}", "judul"),
                       P_("Tulis nama dan nomor dengan tangan. Sistem tidak menyimpan data pribadi.", "sub")], 1.6),
                Spacer(1, 4)]
    rows = [[P_("<b>Peran</b>"), P_("<b>Menghubungi</b>"), P_("<b>Nama (tulis tangan)</b>"), P_("<b>Nomor (tulis tangan)</b>")]]
    for k in d["kontak"]:
        rows.append([P_(escape(k["peran"])), P_(escape("; ".join(k["menghubungi"]))), Spacer(1, 12 * mm), ""])
    t = Table(rows, colWidths=[FW * 0.27, FW * 0.33, FW * 0.22, FW * 0.18])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.8, ink), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 2)]))
    belakang += [t, P_("YANG JANGAN DILAKUKAN", "h")]
    belakang += [Paragraph(escape(x), st["bi"], bulletText="•") for x in d["jangan"]]
    belakang += [P_("PENGINGAT PREBUNKING", "h"), P_(escape(d["prebunking"]))]
    if d.get("rencana"):
        r = d["rencana"]
        belakang += [P_("RENCANA TERSIMPAN (Perencana)", "h"),
                     P_(f"{escape(r['nama'])}. Pada simulasi menurunkan beban siaga ±{_bul(-(r.get('efek_persen') or 0))}%, keyakinan "
                        f"{escape(str(r.get('keyakinan')))}. Jam dihitung sejak isu diketahui.")]
    kaki = [P_(f"<b>{TANDA_SIMULASI}</b>", "tanda"),
            P_(f"Kartu ini disusun dari {d['jumlah_skenario']} skenario simulasi dengan warga fiktif. Pakai untuk memilih "
               "langkah dan menyiapkan tim, bukan untuk meramal kejadian. Berlaku sampai disahkan bersama mitra "
               "(Diskominfo, sinode, lembaga adat).", "kecil"),
            P_(f"Versi model: {escape(vm['id'])} · kode {escape(vm['kode'])} · status validasi: "
               f"{escape('; '.join(vm['status_validasi']))}", "kecil")]
    if d.get("lencana"):
        kaki.append(P_(f"Validasi kabupaten: {escape(d['lencana']['teks'])}", "kecil"))
    kaki.append(P_(f"Kontak tim peneliti: {escape(d['kontak_peneliti'])}", "kecil"))
    belakang += [Spacer(1, 6), kotak(kaki, 1.2)]

    doc = BaseDocTemplate(str(path), pagesize=A4, leftMargin=M, rightMargin=M, topMargin=M, bottomMargin=M,
                          title=f"Kartu Respons {d['nama']}", author="Indonesia Sintetis", subject=TANDA_SIMULASI)

    def kaki_hal(c, doc_):
        c.saveState()
        c.setFont("Sans", 8)
        c.drawString(M, 6 * mm, f"{TANDA_SIMULASI} · {vm['id']} · {d['nama']} · sisi {'depan' if doc_.page == 1 else 'belakang'}")
        c.restoreState()

    fh = H - 2 * M
    doc.addPageTemplates([PageTemplate("k", [Frame(M, M, FW, fh, id="f", leftPadding=0, rightPadding=0, topPadding=0,
                                                   bottomPadding=0)], onPage=kaki_hal)])
    doc.build([KeepInFrame(FW, fh, depan, mode="shrink"), PageBreak(), KeepInFrame(FW, fh, belakang, mode="shrink")])
    return path


def buat_semua(P: dict | None = None, bahasa: str = "id", folder: str | Path | None = None) -> list[Path]:
    P = P if P is not None else pustaka.muat()
    out = []
    for k in load_region()["kabupaten"]:
        p = Path(folder) / f"kartu_{k['id']}.pdf" if folder else None
        out.append(buat_kartu(k["id"], p, P, bahasa))
    return out
