"""Pemeriksa kesiapan: apa yang sudah siap dipakai dan apa yang masih perkiraan/penempat.
Dipakai oleh `python -m sintetis status` dan tab Dunia di dashboard."""
from __future__ import annotations

import socket

from .config import CONFIG_DIR, DATA_DIR, OUT_DIR, load_yaml
from .llm.cache import DecisionCache
from .llm.orchestrator import resolve_api_key

OK, PERLU, OPSI = "siap", "perlu", "opsional"


def periksa_kampung() -> list[str]:
    """Periksa data/kampung.csv. Daftar kosong = tidak ada berkas; baris berawalan 'GALAT' = harus diperbaiki."""
    import pandas as pd

    from .config import load_region

    path = DATA_DIR / "kampung.csv"
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path)
    except Exception as e:
        return [f"GALAT: data/kampung.csv tidak terbaca ({e})"]
    out = []
    kurang = [c for c in ("kabupaten_id", "distrik", "kampung", "lon", "lat", "penduduk") if c not in df]
    if kurang:
        return [f"GALAT: kolom wajib tidak ada: {', '.join(kurang)}"]
    kab = [k["id"] for k in load_region()["kabupaten"]]
    asing = sorted(set(df["kabupaten_id"]) - set(kab))
    if asing:
        out.append(f"PERINGATAN: kabupaten_id tidak dikenal dan diabaikan: {asing}. Pilihan: {kab}")
    hilang = [k for k in kab if k not in set(df["kabupaten_id"])]
    if hilang:
        out.append(f"GALAT: tidak ada kampung untuk kabupaten: {hilang}")
    if df[["lon", "lat", "penduduk"]].isna().any().any() or (df["penduduk"] <= 0).any():
        out.append("GALAT: ada lon/lat/penduduk yang kosong atau penduduk <= 0")
    luar = ~(df["lon"].between(134, 142) & df["lat"].between(-10, 1))
    if luar.any():
        out.append(f"PERINGATAN: {int(luar.sum())} kampung di luar kotak Papua (lon 134..142, lat -10..1); cek lon/lat tertukar")
    if "sinyal" in df:
        buruk = df["sinyal"].map(lambda x: isinstance(x, str) and x.strip().lower() not in ("4g", "lemah", "tidak"))
        if buruk.any():
            out.append(f"GALAT: {int(buruk.sum())} nilai sinyal tidak dikenal (pakai 4g | lemah | tidak atau angka 0..1)")
    if "tipe" in df and (bad := sorted(set(df["tipe"].dropna()) - {"kota", "pesisir", "pedalaman", "kepulauan"})):
        out.append(f"GALAT: tipe tidak dikenal: {bad}")
    ok = not any(o.startswith("GALAT") for o in out)
    out.insert(0, f"data/kampung.csv: {len(df)} kampung, {df.groupby(['kabupaten_id', 'distrik']).ngroups} distrik"
                  + (" — lolos pemeriksaan" if ok else " — PERLU DIPERBAIKI"))
    return out


def check() -> list[dict]:
    out = []

    def add(butir: str, status: str, ket: str) -> None:
        out.append({"butir": butir, "status": status, "keterangan": ket})

    kunci = [p for p, env in (("SumoPod", "SUMOPOD_API_KEY"), ("DeepSeek", "DEEPSEEK_API_KEY")) if resolve_api_key(env)]
    add("Kunci API LLM", OK if kunci else OPSI,
        "tersedia" if kunci else "tidak ada: simulasi tetap jalan dengan aturan ABM; laporan AI & agen LLM butuh kunci")
    cek = periksa_kampung()
    terbuka = (DATA_DIR / "geodata_sumber.json").exists() and cek and not any(c.startswith("GALAT") for c in cek)
    add("Geografi kampung", PERLU if not cek or any(c.startswith("GALAT") for c in cek) else OK,
        (" | ".join(cek) + (" | dari data terbuka ('geodata'): batas & kode resmi Kepmendagri 2025, penduduk Dukcapil "
                            "dibagi WorldPop, sinyal proksi Ookla. Ganti sinyal dengan Podes & isi data/penduduk_distrik.csv "
                            "(BPS) bila ada" if terbuka else "")) if cek
        else "masih SINTETIS. Jalankan 'geodata' untuk geografi nyata dari data terbuka (perlu internet), "
             "atau 'data' untuk templat Podes")
    ref = load_yaml(CONFIG_DIR / "kalibrasi_rujukan.yaml")
    penempat = str(ref.get("sumber", "")).startswith("isi:")
    add("Linimasa kasus rujukan (kalibrasi)", PERLU if penempat else OK,
        "config/kalibrasi_rujukan.yaml masih nilai CONTOH; parameter belum terkalibrasi" if penempat else f"sumber: {ref.get('sumber')}")
    p = CONFIG_DIR / "persona_ditinjau.yaml"
    n = sum(1 for x in (load_yaml(p) or []) if x.get("ditinjau")) if p.exists() else 0
    add("Persona ditinjau penutur asli", OK if n >= 50 else PERLU,
        f"{n} persona bertanda ditinjau (target PRD ≥ 50). Jalankan 'persona' untuk mengekspor bahan tinjauan")
    beku = DecisionCache().stats()["beku"]
    add("Tabel keputusan LLM beku", OK if beku else OPSI, ", ".join(beku) if beku else "belum ada: Monte Carlo memakai aturan ABM")
    mcs = [d.parent.name for d in OUT_DIR.glob("*/hasil.parquet") if (d.parent / "manifest.json").exists()]
    add("Hasil Monte Carlo", OK if mcs else PERLU, ", ".join(sorted(mcs)) if mcs else "belum ada: jalankan 'montecarlo'")
    sens = [d.parent.name for d in OUT_DIR.glob("*/sensitivitas.json")]
    add("Analisis sensitivitas", OK if sens else PERLU, ", ".join(sorted(sens)) if sens else "belum ada: jalankan 'sensitivitas'")
    # --- alat tim lapangan (R1–R6)
    try:
        from .pustaka import status as st_pustaka

        ps = st_pustaka()
        add("Pustaka skenario (Ruang Situasi, Kartu)", OK if ps["ada"] and not ps["usang"] and not ps["prioritas_belum"] else PERLU,
            (f"{ps['selesai']}/{ps['total_desain']} sel, {ps['sel_berkurva']} berkurva keterlambatan"
             + (" · USANG untuk versi model sekarang: bangun ulang" if ps["usang"] else "")
             + (f" · {len(ps['prioritas_belum'])} sel prioritas belum dihitung" if ps["prioritas_belum"] else ""))
            if ps["ada"] else "belum ada: jalankan 'pustaka bangun --prioritas' lalu 'pustaka bangun' (±1 malam)")
    except Exception as e:  # pustaka rusak tidak boleh menggagalkan daftar kesiapan
        add("Pustaka skenario (Ruang Situasi, Kartu)", PERLU, f"tidak terbaca: {e}")
    tk = load_yaml(CONFIG_DIR / "templat_kartu.yaml") or {}
    add("Templat kalimat kartu ditinjau", OK if (tk.get("ditinjau") or {}).get("id") else PERLU,
        "sudah ditinjau: " + ", ".join(tk.get("peninjau") or []) if (tk.get("ditinjau") or {}).get("id") else
        "config/templat_kartu.yaml belum ditinjau penutur asli & praktisi lapangan; kartu tercetak sebagai DRAF")
    from .config import ROOT

    kj = list((ROOT / "kajian").glob("*_analisis.json")) if (ROOT / "kajian").exists() else []
    add("Kajian pasca-kejadian (kalibrasi banyak kasus)", OK if kj else OPSI,
        f"{len(kj)} kajian sudah dianalisis; kalibrasi ulang: 'kalibrasi --kasus kajian/'" if kj else
        "belum ada: catat kejadian nyata dan near miss di tab Kajian pasca-kejadian")
    add("Jeda deteksi per tipe wilayah", OPSI, "nilai bawaan di parameter.yaml (jeda_deteksi_jam) masih PERKIRAAN; tanyakan di FGD")
    try:
        socket.create_connection(("127.0.0.1", 11434), timeout=0.3).close()
        add("Ollama lokal (pembanding)", OK, "server Ollama terdeteksi di port 11434")
    except OSError:
        add("Ollama lokal (pembanding)", OPSI, "tidak terdeteksi: pasang Ollama + model di config/llm.yaml untuk pembanding model lokal")
    return out
