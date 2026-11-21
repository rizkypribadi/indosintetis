"""Antarmuka baris perintah: python -m sintetis <perintah> ..."""
from __future__ import annotations

import argparse
import json
import sys
import time

import yaml


def _world_args(p):
    p.add_argument("--agen", type=int, default=100_000, help="jumlah agen (default 100.000)")
    p.add_argument("--seed-dunia", type=int, default=42, help="seed pembangkit populasi & jaringan")


def _sc_args(p):
    p.add_argument("--skenario", default=None, help="berkas YAML skenario (default scenarios/isu_sentani.yaml)")
    p.add_argument("--intervensi-file", default=None, help="berkas YAML intervensi")


def cmd_dunia(a):
    from .world import build_world

    w = build_world(a.agen, a.seed_dunia, use_cache=not a.tanpa_cache)
    print(json.dumps(w.summary(), ensure_ascii=False, indent=1))


def cmd_jalankan(a):
    from .config import load_interventions, load_scenario
    from .engine import Simulation
    from .eventlog import save_run
    from .llm.orchestrator import make_decider
    from .world import build_world

    sc = load_scenario(a.skenario)
    iv = load_interventions(a.intervensi_file)
    w = build_world(a.agen, a.seed_dunia)
    dec = make_decider(a.llm, a.beku, sc)
    sim = Simulation(w, sc, iv[a.intervensi], seed=a.seed, decider=dec, kode_intervensi=a.intervensi)
    t0 = time.perf_counter()
    res = sim.run()
    d = save_run(res, w, a.nama)
    tot = res.timeseries.sum(1)[-1]
    print(f"Selesai {sim.T} langkah dalam {time.perf_counter() - t0:.1f} dtk. Akhir S/E/I/Z/A = {tot.tolist()}")
    print(f"Pernah dengar hoaks: {sim.pernah_dengar.mean():.1%}. Distrik eskalasi: {(res.eskalasi_step >= 0).sum()}")
    print(f"Agen kunci: {res.manifest['llm']}")
    print(f"Log tersimpan di {d}")


def cmd_bekukan(a):
    from .freeze import freeze_decisions

    info = freeze_decisions(a.nama, a.llm, a.agen, a.seed_dunia, a.pilot, a.intervensi)
    print(json.dumps(info, ensure_ascii=False, indent=1))


def cmd_montecarlo(a):
    from .config import load_interventions, load_scenario
    from .montecarlo import run_montecarlo
    from .report import build_report

    d = run_montecarlo(a.agen, a.seed_dunia, a.putaran, a.intervensi, load_scenario(a.skenario),
                       load_interventions(a.intervensi_file), mode=a.llm, beku=a.beku, workers=a.workers, nama=a.nama,
                       posterior=a.posterior)
    md, html = build_report(d)
    print(f"Hasil: {d}\nLaporan: {md}\n         {html}")


def cmd_alur(a):
    from .alur import jalankan_alur

    h = jalankan_alur(a.skenario, a.agen, a.seed_dunia, a.llm, pilot=a.pilot, eksplorasi=a.eksplorasi, putaran=a.putaran,
                      kode=a.intervensi, penyedia_laporan=a.penyedia, fokus=a.fokus or "", workers=a.workers, nama=a.nama,
                      tahap=lambda t, s, c="": print(f"[{t}] {s}: {c}"))
    print(f"Terbaik: {h['terbaik']} · log peta: {h['log_peta']}\nLaporan: out/{h['laporan_pdf'] or h['laporan_md']}")


def cmd_laporan(a):
    from .report import build_report

    md, html = build_report(a.folder)
    print(f"Laporan: {md}\n         {html}")


def cmd_kalibrasi(a):
    from .calibration import aktifkan, calibrate, calibrate_abc
    from .config import OUT_DIR, versi_model

    if a.kasus:                       # R6: kalibrasi banyak kasus dari kajian pasca-kejadian
        from .kajian import kasus_kalibrasi

        cases = kasus_kalibrasi(a.kasus)
        if not cases:
            print(f"Tidak ada kajian sah di {a.kasus}")
            return
        print(f"{len(cases)} kasus: " + ", ".join(f"{c['kasus']} (bobot {c['bobot_kasus']:.2f}"
                                                  + (", near miss" if c["near_miss"] else "") + ")" for c in cases))
        lama = versi_model()["id"]
        d = calibrate_abc(a.agen, a.seed_dunia, a.sampel, a.putaran or 3, a.terima, rujukan=cases, workers=a.workers,
                          nama=a.nama or "posterior_kasus")
        print((d / "kalibrasi_abc.md").read_text(encoding="utf-8"))
        if not a.jangan_aktifkan:
            aktifkan(d, cases)
            print(f"Kalibrasi aktif: {d.name}. Versi model {lama} → {versi_model()['id']}: pustaka lama USANG, "
                  "kartu perlu dicetak ulang (python -m sintetis pustaka bangun; python -m sintetis kartu --semua).")
        return
    a.putaran = a.putaran or (3 if a.metode == "abc" else 5)
    if a.metode == "abc":
        d = calibrate_abc(a.agen, a.seed_dunia, a.sampel, a.putaran, a.terima, workers=a.workers, nama=a.nama)
        print((d / "kalibrasi_abc.md").read_text(encoding="utf-8"))
        print(f"Tersimpan: {d}\nPakai di Monte Carlo: python -m sintetis montecarlo --posterior {d.name}")
        return
    df = calibrate(a.agen, a.seed_dunia, a.putaran)
    OUT_DIR.mkdir(exist_ok=True)
    df.to_csv(OUT_DIR / "kalibrasi.csv", index=False)
    print(df.head(10).to_string())
    print(f"Tersimpan: {OUT_DIR / 'kalibrasi.csv'}")


def cmd_sensitivitas(a):
    import shutil

    from .config import OUT_DIR, load_scenario
    from .report import build_report
    from .sensitivity import run_sensitivity, run_sobol

    if a.metode == "sobol":
        d = run_sobol(a.agen, a.seed_dunia, a.n_sobol, a.intervensi or "AE", load_scenario(a.skenario),
                      mode=a.llm, beku=a.beku, workers=a.workers, nama=a.nama, seed_per_konfig=a.seed_per_konfig)
        berkas = ("sobol.md", "sobol.json")
    else:
        d = run_sensitivity(a.agen, a.seed_dunia, a.putaran, a.sampel, a.intervensi or "ABCDEF",
                            load_scenario(a.skenario), mode=a.llm, beku=a.beku, workers=a.workers, nama=a.nama)
        berkas = ("sensitivitas.md", "sensitivitas.json")
    print(f"Hasil: {d / berkas[0]}")
    if a.ke_mc:   # tempel ke laporan Monte Carlo (dan ke bahan laporan AI)
        mc = OUT_DIR / a.ke_mc
        for f in berkas:
            shutil.copy(d / f, mc / f)
        build_report(mc)
        print(f"Ditambahkan ke laporan {mc / 'laporan.html'}")


def cmd_keterlambatan(a):
    from .config import OUT_DIR
    from .keterlambatan import hitung_keterlambatan

    d = hitung_keterlambatan(a.skenario, a.intervensi, a.jam, a.putaran, a.agen, a.seed_dunia, workers=a.workers,
                             out_dir=OUT_DIR / a.nama if a.nama else None)
    h = json.loads((d / "ringkasan.json").read_text(encoding="utf-8"))
    for kode, r in (h["ringkasan"].get("semua") or {}).items():
        print(f"[{kode}] {r['kalimat']}" + (f"  PERINGATAN: {'; '.join(r['peringatan'])}" if r.get("peringatan") else ""))
    print(f"Hasil: {d}")


def cmd_pustaka(a):
    from . import pustaka

    if a.aksi == "status":
        print(pustaka.teks_status(pustaka.status()))
        return
    pustaka.bangun(a.agen, a.putaran, a.kode, a.prioritas, a.jenis, a.kabupaten, a.kanal,
                   None if a.ajakan is None else a.ajakan == "ya",
                   keterlambatan=True if a.kurva else False if a.tanpa_kurva else None, jam_kurva=a.jam_kurva,
                   putaran_kurva=a.putaran_kurva, seed_dunia=a.seed_dunia, workers=a.workers)


def cmd_situasi(a):
    from .situasi import nilai

    h = nilai({"jenis": a.jenis, "kabupaten": a.kabupaten, "distrik": a.distrik, "kanal": a.kanal, "ajakan": a.ajakan,
               "tanda_bahaya": a.tanda_bahaya, "jam_sejak_diketahui": a.jam_sejak_diketahui,
               "jam_sejak_beredar": a.jam_sejak_beredar})
    print(f"{h['tanda']} · {h['versi_model']['teks']}")
    p = h.get("pustaka")
    print(p["teks"] if isinstance(p, dict) else p)
    c = h.get("kecocokan") or {}
    print(f"Kecocokan: {c.get('label')}" + (f" (tidak cocok: {', '.join(c['tidak_cocok'])})" if c.get("tidak_cocok") else ""))
    if h.get("posisi"):
        print(h["posisi"]["kalimat"])
    for d in h.get("distrik") or []:
        print(f"  distrik {d['nama']}: peluang memanas {d['peluang_memanas']:.0%}, biasanya jam {d['jam_p10']}–{d['jam_p90']}")
    for t in h.get("tindakan") or []:
        print(f"  [{t['status']}] {t['kode']} {t['langkah']}: efek {t['efek_persen']}% ({t['keyakinan']}) — {t.get('alasan', '')}")
    print(f"Harga keterlambatan: {h.get('harga_keterlambatan')}")
    print(f"Log: out/situasi/{h['id']}.json")


def cmd_kartu(a):
    from . import kartu
    from .config import load_region

    kabs = [k["id"] for k in load_region()["kabupaten"]] if a.semua else [a.kabupaten]
    if not kabs or kabs == [None]:
        raise SystemExit("Pilih --kabupaten <id> atau --semua")
    for k in kabs:
        print(f"Kartu: {kartu.buat_kartu(k, bahasa=a.bahasa)}")


def cmd_rencanakan(a):
    from .perencana import rencanakan, simpan_rencana

    h = rencanakan(a.skenario, a.sumber_daya, agen=a.agen, putaran_awal=a.putaran_awal, sampel_ketahanan=a.ketahanan,
                   workers=a.workers)
    print(f"{h['jumlah_rencana_layak']} rencana layak · {h['detik']:.0f} dtk · versi {h['versi_model']['id']}")
    for i, r in enumerate(h["rencana"], 1):
        rg = r["efek_rentang"]
        print(f"#{i} {r['nama']}\n    efek {r['efek_persen']}% (IK {rg[0]} s.d. {rg[1]}%), keyakinan {r['keyakinan']}, "
              f"ketahanan {r['ketahanan']}" + (f"\n    PERINGATAN: {r['peringatan']}" if r.get("peringatan") else ""))
        for l in r["langkah"]:
            print(f"      jam {l['jam_sejak_diketahui']:g} sejak isu diketahui: {l['label']}")
    for k in h["kalimat_sumber_daya"]:
        print(f"• {k}")
    if a.simpan:
        print(f"Rencana #1 disimpan: {simpan_rencana(h)}")


def cmd_kajian(a):
    from . import kajian

    if a.aksi == "daftar":
        for k in kajian.daftar():
            print(k)
    elif a.aksi == "contoh":
        p = kajian.KAJIAN_DIR / "contoh_kajian.yaml.txt"
        kajian.KAJIAN_DIR.mkdir(exist_ok=True)
        p.write_text(yaml.safe_dump(kajian.CONTOH, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"Contoh skema ditulis ke {p} (salin ke kajian/<id>.yaml lalu isi)")
    elif a.aksi == "buat":
        if not a.dari_situasi:
            raise SystemExit("kajian buat --dari-situasi <id log out/situasi>")
        k = kajian.dari_situasi(a.dari_situasi)
        print(f"Draf kajian: kajian/{k['id']}.yaml — lengkapi tindakan, tanda bahaya, hasil, dan pelajaran")
    elif a.aksi == "analisis":
        h = kajian.analisis(a.id, agen=a.agen, putaran=a.putaran, workers=a.workers)
        for t in h["seberapa_meleset"]:
            print(f"  {t['metrik']}: kenyataan {t['kenyataan']} · model {t['model']} — {t['penjelasan']}")
        for j in h["bagaimana_jika"]:
            print(f"  {j['kalimat']}")
        print(f"PDF: {h.get('pdf') or h.get('pdf_error')}")


def cmd_data(a):
    """Tulis templat data/kampung_contoh.csv dari geografi sekarang, dan periksa data/kampung.csv bila ada."""
    import pandas as pd

    from . import TIPE_WILAYAH
    from .config import DATA_DIR
    from .readiness import periksa_kampung
    from .world import build_world

    DATA_DIR.mkdir(exist_ok=True)
    geo = build_world(20_000, a.seed_dunia).geo
    d = geo.kampung_distrik
    contoh = pd.DataFrame({
        "kabupaten_id": [geo.kab_id[geo.distrik_kab[i]] for i in d], "distrik": [geo.distrik_nama[i] for i in d],
        "kampung": geo.kampung_nama, "lon": geo.kampung_lonlat[:, 0].round(5), "lat": geo.kampung_lonlat[:, 1].round(5),
        "penduduk": geo.kampung_bobot.round().astype(int),
        "sinyal": pd.Series(geo.kampung_sinyal).map(lambda s: "4g" if s >= 1 else "lemah" if s > 0 else "tidak"),
        "tipe": [TIPE_WILAYAH[geo.distrik_tipe[i]] for i in d], "pusat_distrik": geo.kampung_pusat_distrik.astype(int)})
    path = DATA_DIR / "kampung_contoh.csv"
    contoh.to_csv(path, index=False)
    print(f"Templat ({len(contoh)} kampung, sumber geografi: {geo.sumber}) ditulis ke {path}")
    print("Ganti isinya dengan data Podes lalu simpan sebagai data/kampung.csv. Kolom wajib: kabupaten_id, distrik, "
          "kampung, lon, lat, penduduk. Opsional: sinyal (4g|lemah|tidak atau 0..1), tipe, pusat_distrik (0/1).")
    for pesan in periksa_kampung():
        print(pesan)


def cmd_geodata(a):
    """Rakit data/kampung.csv + batas wilayah dari data terbuka (Kepmendagri, Dukcapil, WorldPop, Ookla, OSM, DEM)."""
    import json

    from .geodata import bangun
    from .readiness import periksa_kampung

    meta = bangun(kuartal=a.kuartal, ambang_4g_mbps=a.ambang_4g, radius_sinyal_km=a.radius_sinyal,
                  pantai_km=a.pantai_km, timpa=a.timpa)
    print()
    print(f"{meta['pemeriksaan']['kampung_berpoligon']} dari {meta['pemeriksaan']['kampung_resmi']} kampung resmi. "
          "Ringkasan per kabupaten (sinyal baru vs perkiraan lama):")
    for k in meta["kabupaten"]:
        print(f"  {k['kabupaten_id']:15s} {k['distrik']:3d} distrik {k['kampung']:4d} kampung {k['penduduk']:8,d} jiwa  "
              f"4G {k['sinyal_4g']:.0%} (lama {k['sinyal_perkiraan_lama']:.0%})  tipe distrik {json.dumps(k['tipe_distrik'])}")
    print("Sumber & aturan: data/geodata_sumber.json")
    for pesan in periksa_kampung():
        print(pesan)


def cmd_pdf(a):
    import json
    from pathlib import Path

    from .laporan_pdf import build_pdf

    md = Path(a.berkas)
    meta = json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))
    out = build_pdf(md.read_text(encoding="utf-8"), meta["fakta"], meta["jenis"], meta["info"], md.with_suffix(".pdf"))
    print(f"PDF: {out}")


def cmd_status(a):
    from .readiness import check

    ikon = {"siap": "[siap]    ", "perlu": "[PERLU]   ", "opsional": "[opsional]"}
    for r in check():
        print(f"{ikon[r['status']]} {r['butir']}: {r['keterangan']}")


def cmd_persona(a):
    from .config import OUT_DIR
    from .world import build_world

    w = build_world(a.agen, a.seed_dunia)
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / "persona_untuk_ditinjau.yaml"
    path.write_text("# Tinjau oleh penutur asli. Ubah isian bila perlu, set ditinjau: true, lalu salin ke\n"
                    "# config/persona_ditinjau.yaml. Kunci 'id' wajib dipertahankan.\n"
                    + yaml.safe_dump(w.key.persona, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"{len(w.key.persona)} persona diekspor ke {path}")


def cmd_bench(a):
    from .config import load_interventions, load_scenario
    from .engine import Simulation
    from .world import build_world

    t0 = time.perf_counter()
    w = build_world(a.agen, a.seed_dunia)
    t1 = time.perf_counter()
    sc = load_scenario()
    sim = Simulation(w, sc, load_interventions()["D"], seed=1, log_events=False)
    sim.T = a.langkah
    sim.run()
    t2 = time.perf_counter()
    print(f"Bangun/muat dunia {a.agen:,} agen: {t1 - t0:.1f} dtk; {a.langkah} langkah: {t2 - t1:.1f} dtk "
          f"({(t2 - t1) / a.langkah * 1000:.0f} ms/langkah). Target PRD: < 300 dtk.".replace(",", "."))


def cmd_serve(a):
    import uvicorn

    uvicorn.run("sintetis.server:app", host=a.host, port=a.port, reload=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sintetis", description="Indonesia Sintetis — simulasi hoaks Provinsi Papua")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dunia", help="bangun populasi & jaringan, tampilkan ringkasan")
    _world_args(p)
    p.add_argument("--tanpa-cache", action="store_true")
    p.set_defaults(f=cmd_dunia)

    p = sub.add_parser("jalankan", help="satu putaran simulasi dengan log peristiwa")
    _world_args(p); _sc_args(p)
    p.add_argument("--intervensi", default="A", help="kode A–F")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--llm", default="abm", choices=["abm", "mock", "deepseek", "sumopod", "ollama"])
    p.add_argument("--beku", default=None, help="nama tabel keputusan beku")
    p.add_argument("--nama", default=None, help="nama folder keluaran")
    p.set_defaults(f=cmd_jalankan)

    p = sub.add_parser("bekukan", help="putaran percontohan LLM lalu bekukan tabel keputusan")
    _world_args(p)
    p.add_argument("--nama", required=True)
    p.add_argument("--llm", default="deepseek", choices=["mock", "deepseek", "sumopod", "ollama"])
    p.add_argument("--pilot", type=int, default=3, help="jumlah seed percontohan per intervensi")
    p.add_argument("--intervensi", default="ABCDEF")
    p.set_defaults(f=cmd_bekukan)

    p = sub.add_parser("montecarlo", help="Monte Carlo paralel semua intervensi + laporan")
    _world_args(p); _sc_args(p)
    p.add_argument("--putaran", type=int, default=200)
    p.add_argument("--intervensi", default="ABCDEF")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--llm", default="abm", choices=["abm", "mock", "deepseek", "sumopod", "ollama"])
    p.add_argument("--beku", default=None)
    p.add_argument("--nama", default=None)
    p.add_argument("--posterior", default=None,
                   help="folder hasil 'kalibrasi --metode abc' di out/: tiap seed memakai satu set parameter posterior")
    p.set_defaults(f=cmd_montecarlo)

    p = sub.add_parser("alur", help="alur otomatis: eksplorasi → bekukan → Monte Carlo → putaran contoh → laporan PDF lapangan")
    _world_args(p)
    p.add_argument("--skenario", default=None, help="berkas YAML skenario (default scenarios/isu_sentani.yaml)")
    p.add_argument("--llm", default="abm", choices=["abm", "mock", "deepseek", "sumopod", "ollama"], help="penalar tokoh kunci")
    p.add_argument("--pilot", type=int, default=2, help="putaran percontohan LLM per intervensi (bila --llm bukan abm)")
    p.add_argument("--eksplorasi", type=int, default=3)
    p.add_argument("--putaran", type=int, default=50)
    p.add_argument("--intervensi", default="ABCDEF")
    p.add_argument("--penyedia", default=None, help="penulis laporan: sumopod | deepseek | ollama | mock")
    p.add_argument("--fokus", default=None)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--nama", default=None)
    p.set_defaults(f=cmd_alur)

    p = sub.add_parser("laporan", help="bangun ulang laporan dari folder Monte Carlo")
    p.add_argument("folder")
    p.set_defaults(f=cmd_laporan)

    p = sub.add_parser("kalibrasi", help="kalibrasi terhadap linimasa rujukan (grid atau ABC)")
    p.add_argument("--metode", default="grid", choices=["grid", "abc"],
                   help="grid: titik terbaik atas grid kecil; abc: sebaran posterior atas semua parameter di sensitivitas.yaml")
    p.add_argument("--agen", type=int, default=20_000)
    p.add_argument("--seed-dunia", type=int, default=42)
    p.add_argument("--putaran", type=int, default=None, help="seed per set parameter (default grid 5, abc 3)")
    p.add_argument("--sampel", type=int, default=200, help="abc: jumlah set parameter dari prior")
    p.add_argument("--terima", type=float, default=0.1, help="abc: porsi sampel terdekat yang diterima")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--nama", default=None, help="abc: nama folder hasil di out/ (default posterior_abc)")
    p.add_argument("--kasus", default=None, help="folder kajian pasca-kejadian (mis. kajian/): kalibrasi ABC banyak kasus")
    p.add_argument("--jangan-aktifkan", action="store_true", help="--kasus: jangan jadikan posterior kalibrasi aktif")
    p.set_defaults(f=cmd_kalibrasi)

    p = sub.add_parser("keterlambatan", help="R3 kurva harga keterlambatan: geser waktu intervensi sejak isu diketahui")
    _world_args(p)
    p.set_defaults(agen=20_000)
    p.add_argument("--skenario", default=None)
    p.add_argument("--intervensi", default="CDF", help="kode intervensi (E = prebunking digeser hari & cakupan)")
    p.add_argument("--jam", default="0:24:2", help="awal:akhir:langkah jam sejak isu diketahui")
    p.add_argument("--putaran", type=int, default=50)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--nama", default=None)
    p.set_defaults(f=cmd_keterlambatan)

    p = sub.add_parser("pustaka", help="R2a pustaka skenario: bangun (dapat dilanjutkan) atau status")
    p.add_argument("aksi", choices=["bangun", "status"])
    p.add_argument("--agen", type=int, default=20_000)
    p.add_argument("--seed-dunia", type=int, default=42)
    p.add_argument("--putaran", type=int, default=30)
    p.add_argument("--kode", default="ABCDEFK", help="intervensi yang diuji di setiap sel")
    p.add_argument("--prioritas", action="store_true", help="hanya sel prioritas (ajakan bertindak, kota/pesisir) + kurva")
    p.add_argument("--jenis", nargs="*", default=None)
    p.add_argument("--kabupaten", nargs="*", default=None)
    p.add_argument("--kanal", nargs="*", default=None, choices=["wa", "fb", "tatap_muka"])
    p.add_argument("--ajakan", default=None, choices=["ya", "tidak"])
    p.add_argument("--kurva", action="store_true", help="hitung kurva keterlambatan untuk semua sel")
    p.add_argument("--tanpa-kurva", action="store_true", help="jangan hitung kurva keterlambatan")
    p.add_argument("--jam-kurva", default="0:12:2")
    p.add_argument("--putaran-kurva", type=int, default=None)
    p.add_argument("--workers", type=int, default=None)
    p.set_defaults(f=cmd_pustaka)

    p = sub.add_parser("situasi", help="R2b Ruang Situasi dari baris perintah (membaca pustaka, tanpa internet)")
    p.add_argument("--jenis", default="penculikan_anak")
    p.add_argument("--kabupaten", required=True)
    p.add_argument("--distrik", default=None)
    p.add_argument("--kanal", default="wa", choices=["wa", "fb", "tatap_muka"])
    p.add_argument("--ajakan", action="store_true")
    p.add_argument("--tanda-bahaya", action="store_true")
    p.add_argument("--jam-sejak-diketahui", type=float, default=0.0)
    p.add_argument("--jam-sejak-beredar", type=float, default=None)
    p.set_defaults(f=cmd_situasi)

    p = sub.add_parser("kartu", help="R1 Kartu Respons Cetak per kabupaten → out/kartu/<versi>/kartu_<kab>.pdf")
    p.add_argument("--kabupaten", default=None)
    p.add_argument("--semua", action="store_true")
    p.add_argument("--bahasa", default="id", choices=["id", "mp"], help="mp = Melayu Papua (hanya bila templat sudah ditinjau)")
    p.set_defaults(f=cmd_kartu)

    p = sub.add_parser("rencanakan", help="R4 perencana intervensi dengan keterbatasan sumber daya")
    p.add_argument("--skenario", default=None)
    p.add_argument("--sumber-daya", required=True, help="berkas profil atau id kabupaten di config/sumber_daya/")
    p.add_argument("--agen", type=int, default=20_000)
    p.add_argument("--putaran-awal", type=int, default=10)
    p.add_argument("--ketahanan", type=int, default=8, help="sampel Latin hypercube untuk uji ketahanan (0 = lewati)")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--simpan", action="store_true", help="simpan rencana terbaik ke out/rencana/ (dipakai kartu & latihan)")
    p.set_defaults(f=cmd_rencanakan)

    p = sub.add_parser("kajian", help="R6 kajian pasca-kejadian: daftar | contoh | buat --dari-situasi | analisis <id>")
    p.add_argument("aksi", choices=["daftar", "contoh", "buat", "analisis"])
    p.add_argument("id", nargs="?", default=None)
    p.add_argument("--dari-situasi", default=None)
    p.add_argument("--agen", type=int, default=20_000)
    p.add_argument("--putaran", type=int, default=20)
    p.add_argument("--workers", type=int, default=None)
    p.set_defaults(f=cmd_kajian)

    p = sub.add_parser("sensitivitas", help="analisis sensitivitas: apakah peringkat intervensi bertahan bila parameter digeser")
    _sc_args(p)
    p.add_argument("--agen", type=int, default=20_000)
    p.add_argument("--seed-dunia", type=int, default=42)
    p.add_argument("--putaran", type=int, default=3, help="seed per konfigurasi parameter")
    p.add_argument("--metode", default="lhs", choices=["lhs", "sobol"],
                   help="lhs: OAT + Latin hypercube (ketahanan peringkat); sobol: indeks Sobol (porsi varians per parameter)")
    p.add_argument("--sampel", type=int, default=40, help="lhs: jumlah sampel global (Latin hypercube)")
    p.add_argument("--n-sobol", type=int, default=64, help="sobol: N dasar (dibulatkan ke pangkat 2); biaya N×(d+2)")
    p.add_argument("--seed-per-konfig", type=int, default=1,
                   help="sobol: seed per konfigurasi (dirata-rata); derau turun ~1/k, biaya naik k kali")
    p.add_argument("--intervensi", default=None, help="default lhs ABCDEF, sobol AE (A selalu ikut)")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--llm", default="abm", choices=["abm", "mock", "deepseek", "sumopod", "ollama"])
    p.add_argument("--beku", default=None)
    p.add_argument("--nama", default=None)
    p.add_argument("--ke-mc", default=None, help="nama folder Monte Carlo di out/ yang laporannya ditambahi hasil ini")
    p.set_defaults(f=cmd_sensitivitas)

    p = sub.add_parser("data", help="tulis templat data/kampung_contoh.csv dan periksa data/kampung.csv")
    p.add_argument("--seed-dunia", type=int, default=42)
    p.set_defaults(f=cmd_data)

    p = sub.add_parser("geodata", help="rakit data/kampung.csv & batas wilayah nyata dari data terbuka (perlu internet)")
    p.add_argument("--kuartal", type=int, default=4, help="jumlah kuartal terakhir data Ookla yang digabung")
    p.add_argument("--ambang-4g", type=float, default=5.0, help="Mbps unduh minimum ubin Ookla untuk dianggap 4G")
    p.add_argument("--radius-sinyal", type=float, default=2.0, help="km dari titik kampung untuk mencari ubin Ookla")
    p.add_argument("--pantai-km", type=float, default=3.0, help="kampung sejauh ini dari laut dianggap pesisir")
    p.add_argument("--timpa", action="store_true", help="ganti data/kampung.csv yang sudah ada (lama → .bak)")
    p.set_defaults(f=cmd_geodata)

    p = sub.add_parser("pdf", help="bangun ulang PDF dari laporan AI (.md) beserta .json pasangannya")
    p.add_argument("berkas")
    p.set_defaults(f=cmd_pdf)

    p = sub.add_parser("status", help="periksa kesiapan: data, kunci, tinjauan, hasil yang sudah ada")
    p.set_defaults(f=cmd_status)

    p = sub.add_parser("persona", help="ekspor persona agen kunci untuk ditinjau penutur asli")
    _world_args(p)
    p.set_defaults(f=cmd_persona)

    p = sub.add_parser("bench", help="uji kinerja (target PRD: 1 juta agen × 96 langkah < 5 menit)")
    p.add_argument("--agen", type=int, default=1_000_000)
    p.add_argument("--seed-dunia", type=int, default=42)
    p.add_argument("--langkah", type=int, default=96)
    p.set_defaults(f=cmd_bench)

    p = sub.add_parser("serve", help="jalankan dashboard di peramban")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(f=cmd_serve)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
