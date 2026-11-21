"""Alur otomatis untuk tim lapangan: satu tombol menjalankan seluruh alur yang disarankan.

  1. eksplorasi     beberapa putaran tanpa intervensi (A) untuk melihat seberapa bervariasi kejadiannya
  2. bekukan        putaran percontohan dengan LLM, lalu keputusan tokoh kunci dibekukan (FR-08);
                    dilewati bila tokoh kunci memakai aturan ABM
  3. montecarlo     semua intervensi, seed berpasangan, memakai tabel beku tadi (FR-09/FR-10)
  4. peta           putaran contoh: intervensi terbaik vs tanpa intervensi pada seed yang paling mewakili
                    hasil Monte Carlo, disimpan sebagai log agar bisa diputar ulang di peta
  5. laporan        laporan AI berbahasa sederhana untuk tim lapangan, lengkap dengan PDF

Keputusan diambil dari Monte Carlo; putaran di peta hanya untuk menjelaskan keputusan itu. Karena putaran
contoh memakai seed, dunia, dan tabel beku yang sama dengan Monte Carlo, angkanya identik dengan baris
Monte Carlo pada seed tersebut, jadi cerita di peta tidak bertentangan dengan statistiknya.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import URUT_TIPE, paired_effect, ranking, summarize
from .config import OUT_DIR, load_interventions, load_params, load_scenario
from .engine import Simulation
from .eventlog import save_run
from .llm.orchestrator import make_decider
from .world import build_world

TAHAP = [
    ("eksplorasi", "Eksplorasi putaran tanpa intervensi"),
    ("bekukan", "Bekukan keputusan tokoh kunci"),
    ("montecarlo", "Monte Carlo semua intervensi"),
    ("peta", "Putaran contoh untuk peta"),
    ("laporan", "Laporan AI untuk tim lapangan (PDF)"),
]
KANAL_NAMA = {"paparan_wa": "WhatsApp", "paparan_fb": "Facebook", "paparan_tatap_muka": "tatap muka",
              "paparan_radio": "radio"}


def _r(x, n=3):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def keyakinan(signifikan: bool, stabilitas: float) -> str:
    """Terjemahan IK 95% + stabilitas bootstrap ke bahasa lapangan."""
    if signifikan and stabilitas >= 0.7:
        return "tinggi"
    if signifikan:
        return "sedang"
    return "rendah"


def kartu_tindakan(df: pd.DataFrame, names: dict[str, str]) -> dict:
    """Ringkasan siap pakai per tipe wilayah, dihitung di Python (bukan oleh LLM)."""
    summ, eff, rank = summarize(df), paired_effect(df), ranking(df, n_boot=500)
    summ["tipe"] = summ["tipe"].astype(str)
    out = {}
    for tipe in URUT_TIPE:
        r = rank[(rank["tipe"] == tipe) & (rank["intervensi"] != "A")]
        if r.empty:
            continue
        base = summ[(summ["tipe"] == tipe) & (summ["intervensi"] == "A")]
        b = base.iloc[0] if len(base) else None

        def efek(kode):
            e = eff[(eff["tipe"] == tipe) & (eff["intervensi"] == kode)]
            return (float(e["efek_persen"].iat[0]), bool(e["signifikan"].iat[0])) if len(e) else (np.nan, False)

        best, lemah = r.iloc[0], r.iloc[-1]
        ef, sig = efek(best["intervensi"])
        semua = []
        for _, x in r.iterrows():
            e2, s2 = efek(x["intervensi"])
            semua.append({"kode": x["intervensi"], "nama": names.get(x["intervensi"], ""), "peringkat": int(x["peringkat"]),
                          "efek_persen": _r(e2, 1), "keyakinan": keyakinan(s2, float(x["stabilitas"]))})
        kanal = None
        if b is not None:
            porsi = {KANAL_NAMA[k]: float(b[k]) for k in KANAL_NAMA if k in b and not pd.isna(b[k])}
            kanal = max(porsi, key=porsi.get) if porsi else None
        menurunkan = bool(not np.isnan(ef) and ef < 0)
        out[tipe] = {
            "utamakan": {"kode": best["intervensi"], "nama": names.get(best["intervensi"], ""),
                         "efek_persen": _r(ef, 1), "menurunkan": menurunkan,
                         "keyakinan": keyakinan(sig, float(best["stabilitas"])) if menurunkan else "rendah"},
            "paling_lemah": {"kode": lemah["intervensi"], "nama": names.get(lemah["intervensi"], "")},
            "semua_intervensi": semua,
            "tanpa_intervensi": None if b is None else {
                "warga_pernah_dengar": _r(b["jangkauan_hoaks"]), "puncak_siaga": _r(b["puncak_siaga"], 4),
                "beban_siaga_per_1000": _r(b["siaga_jam_per_1000"], 1),
                "peluang_ada_distrik_memanas": _r(b["peluang_eskalasi"]),
                "rata2_jam_sampai_memanas": _r(b["jam_ke_eskalasi"], 1), "kanal_utama": kanal},
        }
    return out


def seed_wakil(df: pd.DataFrame, kode: str, metric: str = "siaga_jam_per_1000") -> tuple[int, float, float]:
    """Seed yang selisih (kode − A) untuk semua wilayah paling dekat dengan median Monte Carlo.
    Mengembalikan (seed, selisih seed itu, median selisih)."""
    g = df[df["tipe"] == "semua"].pivot(index="seed", columns="intervensi", values=metric)
    diff = (g[kode] - g["A"]).dropna()
    med = float(diff.median())
    s = int((diff - med).abs().idxmin())
    return s, float(diff.loc[s]), med


def jalankan_alur(skenario: str | Path | dict | None = None, agen: int = 100_000, seed_dunia: int = 42, llm: str = "abm",
                  pilot: int = 2, eksplorasi: int = 3, putaran: int = 50, kode: str = "ABCDEF",
                  penyedia_laporan: str | None = None, fokus: str = "", workers: int | None = None,
                  nama: str | None = None, progress=print, tahap=None, kurva_keterlambatan: bool = False) -> dict:
    """Jalankan seluruh alur. `tahap(id, status, catatan)` dipanggil di awal/akhir tiap tahap
    (status: jalan | selesai | dilewati | gagal)."""
    from .freeze import freeze_decisions
    from .laporan_pdf import build_pdf
    from .llm.asisten import ringkas_mc, ringkas_putaran, tulis_laporan
    from .montecarlo import run_montecarlo
    from .report import build_report, html_page, md_to_html

    say = progress or (lambda s: None)
    lapor = tahap or (lambda *a: None)
    sc = skenario if isinstance(skenario, dict) else load_scenario(skenario) if skenario else load_scenario()
    iv = load_interventions()
    kode = "A" + "".join(c for c in dict.fromkeys(kode) if c != "A" and c in iv)
    if len(kode) < 2:
        raise ValueError("Pilih minimal satu intervensi selain A.")
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    nama = nama or f"alur_{sc.get('kode', 'skenario')}_{stamp}"
    out = OUT_DIR / nama
    out.mkdir(parents=True, exist_ok=True)
    pakai_llm = llm not in ("abm", "", None)
    t_mulai = time.perf_counter()
    ringkas: dict = {"nama": nama, "skenario": sc.get("nama"), "agen": agen, "seed_dunia": seed_dunia,
                     "llm_tokoh": llm, "pilot": pilot, "putaran_mc": putaran, "intervensi": kode, "tahap": {}}

    def catat(tid: str, status: str, catatan: str = "", **data):
        ringkas["tahap"][tid] = {"status": status, "catatan": catatan, **data}
        lapor(tid, status, catatan)
        (out / "alur.json").write_text(json.dumps(ringkas, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    w = build_world(agen, seed_dunia, params=load_params())

    # 1. eksplorasi -----------------------------------------------------------------
    catat("eksplorasi", "jalan", f"{eksplorasi} putaran tanpa intervensi")
    eks = []
    for s in range(1, eksplorasi + 1):
        dec = make_decider(llm if pakai_llm else "abm", None, sc)
        sim = Simulation(w, sc, iv["A"], seed=1000 + s, decider=dec, log_events=False, kode_intervensi="A")
        res = sim.run()
        tot = res.timeseries.sum(1).astype(float)
        N = float(sim.dpop.sum())
        eks.append({"seed": 1000 + s, "warga_pernah_dengar": _r(sim.pernah_dengar.mean()),
                    "puncak_siaga": _r(tot[:, 4].max() / N, 4),
                    "jam_puncak_siaga": _r(int(tot[:, 4].argmax()) * sim.dt_h, 1),
                    "beban_siaga_per_1000": _r(tot[:, 4].sum() * sim.dt_h / N * 1000, 1),
                    "distrik_memanas": int((res.eskalasi_step >= 0).sum())})
        say(f"  eksplorasi {s}/{eksplorasi}: {eks[-1]['warga_pernah_dengar']:.0%} warga dengar, "
            f"{eks[-1]['distrik_memanas']} distrik memanas")
    v = [e["beban_siaga_per_1000"] for e in eks]
    catat("eksplorasi", "selesai", f"beban siaga {min(v):.0f}–{max(v):.0f} orang-jam/1.000 antar-putaran", hasil=eks)

    # 2. bekukan --------------------------------------------------------------------
    beku = None
    if not pakai_llm:
        catat("bekukan", "dilewati", "tokoh kunci memakai aturan ABM, tidak perlu tabel beku")
    else:
        catat("bekukan", "jalan", f"{pilot} putaran percontohan × {len(kode)} intervensi dengan {llm}")
        beku = f"{nama}_beku"
        info = freeze_decisions(beku, llm, agen, seed_dunia, pilot, kode, scenario=sc, interventions=iv, progress=say)
        catat("bekukan", "selesai", f"{info['entri_beku']} keputusan dibekukan sebagai '{beku}'"
              + (f" · ±{info['biaya'].get('biaya_usd', 0):.3f} USD" if isinstance(info.get("biaya"), dict) else ""),
              tabel_beku=beku, info=info)

    # 3. Monte Carlo ----------------------------------------------------------------
    mode_mc = llm if beku else "abm"
    catat("montecarlo", "jalan", f"{putaran} putaran × {len(kode)} intervensi")
    d = run_montecarlo(agen, seed_dunia, putaran, kode, sc, iv, mode=mode_mc, beku=beku, workers=workers, nama=nama,
                       progress=say)
    build_report(d)
    df = pd.read_parquet(d / "hasil.parquet")
    names = {c: iv[c]["nama"] for c in kode}
    kartu = kartu_tindakan(df, names)
    best = kartu.get("semua", next(iter(kartu.values())))["utamakan"]["kode"]
    catat("montecarlo", "selesai", f"terbaik untuk semua wilayah: {best} · {names[best]}", folder=d.name, terbaik=best)

    # 3b. (opsional) kurva harga keterlambatan R3 untuk intervensi berwaktu -------------------------
    ket = None
    if kurva_keterlambatan:
        from .keterlambatan import bisa_digeser, hitung_keterlambatan, muat, untuk_laporan

        kk = "".join(c for c in kode if c != "A" and bisa_digeser(iv[c]))
        if kk:
            catat("keterlambatan", "jalan", f"geser waktu {kk} 0–12 jam sejak isu diketahui")
            kd = hitung_keterlambatan(sc, kk, "0:12:2", max(10, putaran // 2), agen, seed_dunia, interventions=iv,
                                      workers=workers, out_dir=out / "keterlambatan", progress=say)
            ket = untuk_laporan(muat(kd))
            catat("keterlambatan", "selesai", "kurva siap")

    # 4. putaran contoh untuk peta ----------------------------------------------------
    s_wakil, sel, med = seed_wakil(df, best)
    catat("peta", "jalan", f"seed {s_wakil} (paling mewakili median Monte Carlo)")
    contoh, log = [], {}
    for c in ("A", best):
        dec = make_decider(mode_mc, beku, sc)
        sim = Simulation(w, sc, iv[c], seed=s_wakil, decider=dec, log_events=True, kode_intervensi=c)
        res = sim.run()
        log[c] = save_run(res, w, f"{nama}_{c}_s{s_wakil}", out_dir=OUT_DIR / "runs").name
        contoh.append(ringkas_putaran(res.timeseries, res.risk, res.eskalasi_step, res.events, res.manifest, w.geo,
                                      sim.dpop, sim.dt_h, sc, iv[c]["nama"], float(sim.pernah_dengar.mean()),
                                      res.kanal_masuk, res.paparan_provokator))
    catat("peta", "selesai", "log siap diputar di peta", log=log, seed=s_wakil)

    # 5. laporan lapangan -------------------------------------------------------------
    catat("laporan", "jalan", "AI menulis laporan untuk tim lapangan")
    fakta = {
        "skenario": {k: sc.get(k) for k in ("nama", "deskripsi", "mulai", "isu", "tanam", "provokator") if k in sc},
        "kartu_tindakan_per_tipe_wilayah": kartu,
        "eksplorasi_tanpa_intervensi": eks,
        "putaran_contoh": {"seed": s_wakil, "intervensi_terbaik": best,
                           "alasan_dipilih": ("seed ini dipilih karena selisih intervensi terbaik vs tanpa intervensi "
                                              "paling dekat dengan median Monte Carlo, jadi ceritanya mewakili hasil rata-rata"),
                           "selisih_beban_siaga_seed_ini": _r(sel, 1), "median_selisih_mc": _r(med, 1),
                           "putaran": contoh},
        "mc": ringkas_mc(d, sc),
        "alur": {"agen": agen, "llm_tokoh": llm, "tabel_beku": beku, "pilot": pilot if beku else 0,
                 "putaran_mc": putaran, "intervensi": names},
        **({"keterlambatan": ket} if ket else {}),
    }
    md, info = tulis_laporan(fakta, "lapangan", penyedia_laporan, fokus)
    md += (f"\n\n---\n\n> Draf dibuat otomatis oleh {info['model']} ({info['penyedia']}) pada {stamp} dari angka hasil "
           "simulasi. Periksa ulang bersama tim ahli dan mitra lokal sebelum dipakai; semua warga di model fiktif.\n")
    berkas = f"laporan_lapangan_{stamp}"
    (out / f"{berkas}.md").write_text(md, encoding="utf-8")
    (out / f"{berkas}.json").write_text(json.dumps({"jenis": "lapangan", "info": info, "fakta": fakta}, ensure_ascii=False,
                                                   default=str), encoding="utf-8")
    (out / f"{berkas}.html").write_text(html_page(md_to_html(md), "Laporan untuk tim lapangan"), encoding="utf-8")
    pdf, pdf_err = None, None
    try:
        pdf = build_pdf(md, fakta, "lapangan", info, out / f"{berkas}.pdf")
    except Exception as e:      # PDF gagal tidak boleh menghapus laporan teks
        pdf_err = f"{type(e).__name__}: {e}"
    catat("laporan", "selesai" if pdf else "gagal", "PDF siap" if pdf else f"PDF gagal: {pdf_err}",
          berkas=berkas, biaya_usd=info.get("biaya_usd"))
    ringkas.update(detik=round(time.perf_counter() - t_mulai, 1), folder=d.name, terbaik=best, log_peta=log,
                   seed_peta=s_wakil, laporan_md=f"{d.name}/{berkas}.md", laporan_html=f"{d.name}/{berkas}.html",
                   laporan_pdf=f"{d.name}/{berkas}.pdf" if pdf else None, pdf_error=pdf_err)
    (out / "alur.json").write_text(json.dumps(ringkas, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return ringkas
