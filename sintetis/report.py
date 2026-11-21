"""FR-14 Ekspor laporan Monte Carlo (Markdown + HTML mandiri) beserta pelacak biaya LLM."""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import KANAL_PAPARAN, METRIK, METRIK_UTAMA, paired_effect, ranking, recommendations, summarize
from .llm.cache import DecisionCache

STATE_COLORS = {"S": "#5b6884", "E": "#e6a700", "I": "#e5484d", "Z": "#1fb5a0", "A": "#a855f7"}


def _fmt(v: float, m: str) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    if m in ("jangkauan_hoaks", "puncak_penyebar", "puncak_siaga", "porsi_distrik_eskalasi"):
        return f"{v * 100:.2f}%"
    return f"{v:.1f}"


def build_report(mc_dir: str | Path) -> tuple[Path, Path]:
    d = Path(mc_dir)
    df = pd.read_parquet(d / "hasil.parquet")
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    names = man["intervensi"]
    summ, eff, rank = summarize(df), paired_effect(df), ranking(df)
    for name, tab in (("ringkasan", summ), ("efek_berpasangan", eff), ("peringkat", rank)):
        tab.to_csv(d / f"{name}.csv", index=False)
    rec = recommendations(rank, eff, names)
    cache = DecisionCache().stats()

    md = [f"# Laporan Monte Carlo — {man['skenario']}", "",
          f"- Agen: **{man['agen']:,}** · putaran per intervensi: **{man['putaran']}** · seed dunia {man['seed_dunia']} · "
          f"seed dinamika {man['seed']}".replace(",", "."),
          f"- Versi kode `{man['versi_kode']}` · mode agen kunci `{man['mode_llm']}`"
          + (f" (tabel beku `{man['tabel_beku']}`)" if man.get("tabel_beku") else ""),
          f"- Keputusan agen kunci: {man['keputusan_agen_kunci']}" + _coverage(man["keputusan_agen_kunci"]),
          f"- Durasi komputasi: {man['detik']} detik dengan {man['workers']} worker"]
    post = man.get("posterior")
    if post:
        md.append(f"- Parameter: tiap seed memakai satu set dari posterior kalibrasi ABC `{post['nama']}` "
                  f"({post['set_terpakai']} dari {post['set_tersedia']} set terpakai; rujukan: {post.get('kasus')}). "
                  "IK 95% memuat ketidakpastian parameter, bukan hanya keacakan dinamika.")
    prov = (man.get("isi_skenario") or {}).get("provokator") or []
    if prov:
        md.append("- Aktor yang sengaja menyebar: " + "; ".join(
            f"{x.get('nama', x['jenis'])} ({x['jenis']}, "
            + (f"{x.get('akun', 10)} akun {x.get('kanal', 'fb').upper()}" if x["jenis"] == "buzzer" else f"{x.get('jumlah', 3)} orang")
            + f", T+{x.get('mulai_jam', 0)}–{x.get('selesai_jam', 'akhir')} jam)" for x in prov))
    md += ["", "## Intervensi", ""]
    md += [f"- **{k}** — {v}" for k, v in names.items()]
    md += ["", "## Rekomendasi draf per tipe wilayah", "",
           f"Metrik utama: {METRIK[METRIK_UTAMA]} (makin kecil makin baik).", ""]
    md += [f"- {r}" for r in rec]
    md += ["", "## Efek terhadap baseline (A), seed berpasangan, IK 95%", "",
           "| Tipe | Intervensi | Selisih siaga-jam/1.000 | IK 95% | Efek | Signifikan |", "|---|---|---|---|---|---|"]
    for _, r in eff.iterrows():
        md.append(f"| {r.tipe} | {r.intervensi} | {r.selisih:.1f} | [{r.selisih_lo:.1f}, {r.selisih_hi:.1f}] | "
                  f"{r.efek_persen:+.0f}% | {'ya' if r.signifikan else 'tidak'} |")
    md += ["", "## Peringkat & stabilitas (bootstrap 1.000×)", "",
           "| Tipe | Peringkat | Intervensi | Nilai | Stabilitas peringkat |", "|---|---|---|---|---|"]
    for _, r in rank.iterrows():
        md.append(f"| {r.tipe} | {r.peringkat} | {r.intervensi} | {r.nilai:.1f} | {r.stabilitas:.0%} |")
    md += ["", "## Ringkasan metrik (rata-rata [IK 95%])", ""]
    cols = list(METRIK)
    md += ["| Tipe | Int. | " + " | ".join(METRIK[c] for c in cols) + " |", "|---|---|" + "---|" * len(cols)]
    for _, r in summ.iterrows():
        cells = [f"{_fmt(r[c], c)} [{_fmt(r[c + '_lo'], c)}–{_fmt(r[c + '_hi'], c)}]" for c in cols]
        md.append(f"| {r.tipe} | {r.intervensi} | " + " | ".join(cells) + " |")
    if "paparan_wa" in summ:
        md += ["", "## Dari kanal mana warga pertama kali mendengar hoaks (baseline A)", "",
               "| Tipe | " + " | ".join(KANAL_PAPARAN.values()) + " |", "|---|" + "---|" * len(KANAL_PAPARAN)]
        for _, r in summ[summ["intervensi"] == sorted(names)[0]].iterrows():
            md.append(f"| {r.tipe} | " + " | ".join(f"{r[c]:.0%}" for c in KANAL_PAPARAN) + " |")
    if "paparan_provokator" in summ and summ["paparan_provokator"].fillna(0).max() > 0:
        kode = sorted(names)
        md += ["", "## Peran provokator dan buzzer", "",
               "Porsi paparan hoaks (warga yang pertama kali mendengar) yang datang langsung dari buzzer atau provokator "
               "lapangan. Sisanya datang dari warga biasa yang ikut percaya dan meneruskan.", "",
               "| Tipe | " + " | ".join(kode) + " |", "|---|" + "---|" * len(kode)]
        for tipe, g in summ.groupby("tipe", observed=True):
            v = dict(zip(g["intervensi"], g["paparan_provokator"]))
            md.append(f"| {tipe} | " + " | ".join(f"{v.get(c, float('nan')):.1%}" for c in kode) + " |")
    for nama_md in ("sensitivitas.md", "sobol.md"):
        if (d / nama_md).exists():
            md += ["", *(d / nama_md).read_text(encoding="utf-8").splitlines()]
    md += ["", "## Biaya LLM (cache keseluruhan)", "",
           f"- Entri cache: {cache['entri']} · token masuk {cache['token_masuk']:,} (cache hit {cache['token_cache']:,}) · "
           f"token keluar {cache['token_keluar']:,}".replace(",", "."),
           "- Monte Carlo tidak memanggil API; biaya hanya timbul saat percontohan/pembekuan.", "",
           "## Keterbatasan", "",
           ("- Parameter diundi dari posterior ABC, yang hanya sebaik linimasa rujukannya; parameter yang tidak terbatasi "
            "rujukan tetap selebar rentang perkiraannya. Geografi kampung sintetis sampai data Podes dimasukkan." if post else
            "- Parameter adalah titik awal yang belum dikalibrasi penuh; geografi kampung sintetis sampai data Podes dimasukkan."),
           "- Hasil disajikan agregat per tipe wilayah, tidak per individu atau kelompok etnis.",
           *(["- Jumlah, intensitas, dan daya serang provokator/buzzer adalah perkiraan skenario, bukan hasil pengukuran; "
              "uji beberapa varian sebelum menarik kesimpulan."] if prov else []),
           "- Perilaku agen kunci perlu divalidasi lewat FGD dan tinjauan penutur asli Melayu Papua.", ""]
    md_path = d / "laporan.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    html_path = d / "laporan.html"
    html_path.write_text(_to_html("\n".join(md), man, d), encoding="utf-8")
    return md_path, html_path


def md_to_html(md: str) -> str:
    """Konversi Markdown sederhana (judul, daftar, tabel, tebal) ke potongan HTML."""
    out, in_table, in_list = [], False, False
    for line in md.splitlines():
        line = line.rstrip()
        esc = html.escape(line)
        esc = _bold(esc)
        if line.startswith("* "):
            line, esc = "- " + line[2:], "- " + esc[2:]
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-"):
                continue
            if not in_table:
                out.append("<table><thead><tr>" + "".join(f"<th>{_bold(html.escape(c))}</th>" for c in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{_bold(html.escape(c))}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</tbody></table>")
            in_table = False
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{esc[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("### "):
            out.append(f"<h3>{esc[4:]}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{esc[3:]}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{esc[2:]}</h1>")
        elif line.strip() and set(line.strip()) <= set("-*_"):
            out.append("<hr>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{esc[5:]}</blockquote>")
        elif line.strip():
            out.append(f"<p>{esc}</p>")
    if in_table:
        out.append("</tbody></table>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def html_page(body: str, title: str = "Laporan Indonesia Sintetis") -> str:
    return f"""<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{--bg:#fff;--ink:#131a2b;--line:#d7dce7;--muted:#566079}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0b0f1a;--ink:#e6eaf3;--line:#263049;--muted:#9aa3b9}}}}
body{{background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif;max-width:1100px;margin:auto;padding:24px 16px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 20px;display:block;overflow-x:auto}}
th,td{{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;white-space:nowrap}}
blockquote{{margin:8px 0;padding:4px 12px;border-left:3px solid var(--line);color:var(--muted)}}
h2{{margin-top:32px}} canvas{{max-width:100%;border:1px solid var(--line);border-radius:6px}}</style></head>
<body>{body}</body></html>"""


def _to_html(md: str, man: dict, d: Path) -> str:
    """Laporan HTML mandiri dengan grafik kurva."""
    curves = (d / "kurva_rata2.json").read_text(encoding="utf-8") if (d / "kurva_rata2.json").exists() else "{}"
    chart = f"""<h2>Kurva rata-rata siaga aktif (A) & penyebar (I), semua wilayah</h2><canvas id="c" width="900" height="300"></canvas>
<script>const K={curves};const cv=document.getElementById('c'),x=cv.getContext('2d');
const col=['#64748b','#e5484d','#e6a700','#1fb5a0','#a855f7','#2340c4'];let mx=0;
for(const k in K)for(const r of K[k])mx=Math.max(mx,r[5-1][4],r[5-1][2]);
let i=0;x.font='12px sans-serif';for(const k in K){{const s=K[k];x.strokeStyle=col[i%6];x.lineWidth=2;
x.beginPath();s.forEach((r,t)=>{{const X=40+t/(s.length-1)*840,Y=280-r[4][4]/mx*260;t?x.lineTo(X,Y):x.moveTo(X,Y)}});x.stroke();
x.setLineDash([4,3]);x.beginPath();s.forEach((r,t)=>{{const X=40+t/(s.length-1)*840,Y=280-r[4][2]/mx*260;t?x.lineTo(X,Y):x.moveTo(X,Y)}});x.stroke();x.setLineDash([]);
x.fillStyle=col[i%6];x.fillText(k+' (A garis, I putus)',50+i*140,14);i++}}</script>"""
    return html_page(md_to_html(md) + chart)


def _coverage(h: dict) -> str:
    beku, miss = h.get("beku", 0), h.get("beku_miss", 0)
    if beku + miss == 0:
        return ""
    return (f" — cakupan tabel beku {beku / (beku + miss):.0%}; sisanya memakai aturan ABM "
            "(tambah --pilot saat membekukan untuk menaikkan cakupan)")


def _bold(s: str) -> str:
    import re

    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", s)
