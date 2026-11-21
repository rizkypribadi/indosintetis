"""R6 Kajian pasca-kejadian: setiap kejadian nyata, termasuk isu yang TIDAK berkembang (near miss), menjadi
pelajaran dan data kalibrasi.

Satu kajian = kajian/<id>.yaml (skema di `CONTOH`). Tanpa nama orang atau akun: pelaku tindakan ditulis
sebagai PERAN, lokasi paling rinci tingkat distrik. Validasi menolak kolom nama/akun/telepon dan teks yang
memuat nomor telepon atau nama akun (@...). Setiap perubahan dicatat di kajian/audit.log (siapa, kapan, apa).

Analisis otomatis (`analisis`):
  1. Putar ulang: linimasa → skenario (tanam di distrik asal, kanal awal, jeda deteksi) + tindakan nyata pada
     jam nyata, dijalankan sebagai Monte Carlo.
  2. Bandingkan model dengan kenyataan pada metrik pola: jam sampai memanas, distrik yang memanas, durasi.
  3. Bagaimana jika: tiap tindakan dilakukan 4 jam lebih awal (seed berpasangan).
  4. Kasus siap dipakai kalibrasi banyak kasus (`kasus_kalibrasi`, `python -m sintetis kalibrasi --kasus kajian/`).
  5. Lencana validasi per kabupaten (`lencana_validasi`) untuk kartu dan Ruang Situasi.

Data kejadian nyata sensitif: simpan lokal (folder kajian/ tidak ikut git), bagikan keluar hanya agregat.
"""
from __future__ import annotations

import copy
import getpass
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import pustaka
from .config import ROOT, TANDA_SIMULASI, load_params, load_region, load_yaml, versi_model

KAJIAN_DIR = ROOT / "kajian"
JENIS_BUTIR = ("muncul", "diketahui", "tindakan", "tanda_bahaya", "mereda")
KANAL_TINDAKAN = ("gereja_adat", "medsos_resmi", "radio", "tim_keliling", "penindakan", "lainnya")
KEYAKINAN = {"tinggi": 1.0, "sedang": 0.6, "rendah": 0.25}
TERLARANG = {"nama", "nama_orang", "akun", "nama_akun", "telepon", "nomor", "hp", "email", "surel", "alamat", "nik"}
POLA_PRIBADI = re.compile(r"(@\w{3,})|(\+?62[\d\s\-]{8,}|\b08\d[\d\s\-]{7,})")
CONTOH = {
    "id": "kj-2027-001", "kabupaten": "kep_yapen", "distrik": "Yapen Selatan", "jenis_isu": "penculikan_anak",
    "tanggal": "2027-03-02", "near_miss": False, "kanal_awal": "wa", "ajakan_tindakan": True,
    "linimasa": [
        {"jam": 0, "jenis": "muncul", "keterangan": "pesan pertama di grup WA orang tua murid", "keyakinan": "sedang"},
        {"jam": 3, "jenis": "diketahui", "keterangan": "dilaporkan Bhabinkamtibmas ke Polres", "keyakinan": "tinggi"},
        {"jam": 5, "jenis": "tindakan", "peran": "ketua klasis", "kanal": "gereja_adat", "keyakinan": "tinggi"},
        {"jam": 9, "jenis": "tanda_bahaya", "distrik": "Yapen Selatan", "keterangan": "warga berjaga di jalan masuk kampung",
         "keyakinan": "sedang"},
        {"jam": 30, "jenis": "mereda", "keyakinan": "rendah"}],
    "sumber": [{"jenis": "laporan_lapangan", "keterangan": "laporan Polres", "keyakinan": "tinggi"}],
    "hasil": {"eskalasi": True, "distrik": ["Yapen Selatan"], "durasi_jam": 21},
    "pelajaran": {"berhasil": ["klarifikasi gereja cepat"], "menghambat": ["sinyal mati di distrik barat"],
                  "usulan": ["tambah radio komunitas di profil sumber daya"]},
}


def _semua_teks(o, kunci=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _semua_teks(v, k)
    elif isinstance(o, list):
        for v in o:
            yield from _semua_teks(v, kunci)
    elif isinstance(o, str):
        yield kunci, o


def _semua_kunci(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _semua_kunci(v)
    elif isinstance(o, list):
        for v in o:
            yield from _semua_kunci(v)


def validasi(k: dict) -> list[str]:
    """Daftar kesalahan (kosong = sah)."""
    err = []
    for w in ("id", "kabupaten", "jenis_isu", "tanggal", "linimasa"):
        if not k.get(w):
            err.append(f"kolom wajib '{w}' kosong")
    if k.get("id") and not re.fullmatch(r"[\w\-]+", str(k["id"])):
        err.append("id hanya boleh huruf, angka, _ dan -")
    kabs = {x["id"] for x in load_region()["kabupaten"]}
    if k.get("kabupaten") and k["kabupaten"] not in kabs:
        err.append(f"kabupaten harus salah satu dari {sorted(kabs)}")
    jenis = set(pustaka.muat_templat()) | {"lainnya"}
    if k.get("jenis_isu") and k["jenis_isu"] not in jenis:
        err.append(f"jenis_isu harus salah satu dari {sorted(jenis)}")
    if k.get("kanal_awal") and k["kanal_awal"] not in pustaka.KANAL_AWAL:
        err.append(f"kanal_awal harus {pustaka.KANAL_AWAL}")
    jenis_ada = []
    for i, b in enumerate(k.get("linimasa") or []):
        if b.get("jenis") not in JENIS_BUTIR:
            err.append(f"linimasa[{i}].jenis harus {JENIS_BUTIR}")
        try:
            if float(b.get("jam")) < 0:
                err.append(f"linimasa[{i}].jam tidak boleh negatif")
        except (TypeError, ValueError):
            err.append(f"linimasa[{i}].jam harus angka (jam sejak isu muncul)")
        if b.get("keyakinan", "sedang") not in KEYAKINAN:
            err.append(f"linimasa[{i}].keyakinan harus tinggi | sedang | rendah")
        if b.get("jenis") == "tindakan" and b.get("kanal") not in KANAL_TINDAKAN:
            err.append(f"linimasa[{i}].kanal harus {KANAL_TINDAKAN}")
        jenis_ada.append(b.get("jenis"))
    if "muncul" not in jenis_ada:
        err.append("linimasa butuh butir 'muncul' (jam 0 = isu pertama beredar)")
    for kk in set(_semua_kunci(k)):
        if str(kk).lower() in TERLARANG:
            err.append(f"kolom '{kk}' tidak diizinkan: tulis PERAN, bukan nama/akun/nomor")
    for kk, t in _semua_teks(k):
        if POLA_PRIBADI.search(t):
            err.append(f"teks di '{kk}' memuat nomor telepon atau nama akun; hapus data pribadi")
    return err


def _audit(aksi: str, kid: str, pengguna: str | None, isi: dict | None = None) -> None:
    KAJIAN_DIR.mkdir(parents=True, exist_ok=True)
    from .config import stable_hash

    with open(KAJIAN_DIR / "audit.log", "a", encoding="utf-8") as f:
        f.write(json.dumps({"waktu": datetime.now().isoformat(timespec="seconds"), "pengguna": pengguna or getpass.getuser(),
                            "aksi": aksi, "id": kid, "hash": stable_hash(isi) if isi else None}, ensure_ascii=False) + "\n")


def simpan(k: dict, pengguna: str | None = None) -> Path:
    err = validasi(k)
    if err:
        raise ValueError("; ".join(err))
    KAJIAN_DIR.mkdir(parents=True, exist_ok=True)
    p = KAJIAN_DIR / f"{k['id']}.yaml"
    aksi = "ubah" if p.exists() else "buat"
    p.write_text("# Kajian pasca-kejadian (R6). DATA SENSITIF: simpan lokal, tanpa nama orang/akun.\n"
                 + yaml.safe_dump(k, allow_unicode=True, sort_keys=False), encoding="utf-8")
    _audit(aksi, k["id"], pengguna, k)
    return p


def muat(kid: str) -> dict:
    if not re.fullmatch(r"[\w\-]+", kid):
        raise ValueError("id tidak valid")
    return load_yaml(KAJIAN_DIR / f"{kid}.yaml")


def daftar() -> list[dict]:
    out = []
    for p in sorted(KAJIAN_DIR.glob("*.yaml")) if KAJIAN_DIR.exists() else []:
        try:
            k = load_yaml(p)
            a = KAJIAN_DIR / f"{k['id']}_analisis.json"
            out.append({"id": k["id"], "kabupaten": k.get("kabupaten"), "jenis_isu": k.get("jenis_isu"),
                        "tanggal": str(k.get("tanggal")), "near_miss": k.get("near_miss"), "dianalisis": a.exists()})
        except Exception:
            continue
    return out


def dari_situasi(log: str | dict, pengguna: str | None = None, simpan_draf: bool = True) -> dict:
    """Draf kajian dari log Ruang Situasi (out/situasi/<id>.json)."""
    from .situasi import muat_log

    h = log if isinstance(log, dict) else muat_log(log)
    m, w = h["masukan"], h["waktu"]
    tgl = h["id"][:8]
    jenis = m.get("jenis") if m.get("jenis") in pustaka.muat_templat() else "lainnya"
    k = {"id": f"kj-{tgl}-{m['kabupaten']}-{h['id'][9:15]}", "kabupaten": m["kabupaten"], "distrik": m.get("distrik"),
         "jenis_isu": jenis, "tanggal": f"{tgl[:4]}-{tgl[4:6]}-{tgl[6:8]}", "near_miss": None,
         "kanal_awal": m.get("kanal", "wa"), "ajakan_tindakan": bool(m.get("ajakan")),
         "linimasa": [{"jam": 0, "jenis": "muncul", "keterangan": "isu pertama beredar (dari Ruang Situasi)",
                       "keyakinan": "rendah" if w.get("jam_beredar_diperkirakan") else "sedang"},
                      {"jam": w["jeda_dipakai_jam"], "jenis": "diketahui", "keterangan": "dilaporkan ke petugas",
                       "keyakinan": "sedang"}],
         "sumber": [{"jenis": "ruang_situasi", "keterangan": f"log {h['id']}", "keyakinan": "sedang"}],
         "hasil": {"eskalasi": None, "distrik": [], "durasi_jam": None},
         "pelajaran": {"berhasil": [], "menghambat": [], "usulan": []},
         "catatan": "DRAF dari Ruang Situasi: lengkapi tindakan, tanda bahaya, hasil, dan pelajaran."}
    if m.get("tanda_bahaya"):
        k["linimasa"].append({"jam": w["jam_sejak_beredar"], "jenis": "tanda_bahaya", "keterangan": "dilaporkan saat Ruang Situasi dibuka",
                              "keyakinan": "sedang"})
    if m.get("deskripsi"):
        k["deskripsi"] = m["deskripsi"]
    if simpan_draf:
        simpan(k, pengguna)
    return k


# ---------------------------------------------------------------------------------------------- putar ulang
SPEC_TINDAKAN = {
    "gereja_adat": {"jenis": "gereja_adat", "porsi_tokoh": 0.5},
    "medsos_resmi": {"jenis": "medsos_resmi", "durasi_jam": 24, "jangkauan": 0.4},
    "radio": {"jenis": "radio", "durasi_jam": 24},
    "tim_keliling": {"jenis": "tim_keliling", "orang": 4, "durasi_jam": 24},
    "penindakan": {"jenis": "penindakan", "porsi_akun": 0.5, "porsi_lokal": 0.3},
}


def _butir(k: dict, jenis: str) -> list[dict]:
    return sorted([b for b in k["linimasa"] if b["jenis"] == jenis], key=lambda b: float(b["jam"]))


def skenario_kajian(k: dict) -> tuple[dict, dict, list[dict]]:
    """(skenario, intervensi {A, N, N-4, N-4:i}, daftar tindakan yang dimodelkan)."""
    jenis = k["jenis_isu"] if k["jenis_isu"] in pustaka.muat_templat() else "penculikan_anak"
    sid = pustaka.sel_id(jenis, k["kabupaten"], k.get("kanal_awal", "wa"), bool(k.get("ajakan_tindakan")))
    diket = _butir(k, "diketahui")
    jeda = float(diket[0]["jam"]) if diket else None
    try:
        sc = pustaka.skenario_sel(sid, distrik=k.get("distrik"), jeda_deteksi_jam=jeda)
    except Exception:
        sc = pustaka.skenario_sel(sid, jeda_deteksi_jam=jeda)
    akhir = max([float(b["jam"]) for b in k["linimasa"]] + [float((k.get("hasil") or {}).get("durasi_jam") or 0)])
    sc["durasi_jam"] = int(max(48, akhir + 12))
    sc["nama"] = f"Putar ulang kajian {k['id']}"
    sc["kode"] = f"kajian_{k['id']}"
    region = load_region()
    stasiun = [r["id"] for r in region.get("radio", []) if k["kabupaten"] in r["jangkauan"]]
    tindakan = []
    for b in _butir(k, "tindakan"):
        if b["kanal"] not in SPEC_TINDAKAN:
            continue
        s = dict(SPEC_TINDAKAN[b["kanal"]])
        s["t_jam"] = float(b["jam"])
        if b["kanal"] in ("gereja_adat", "medsos_resmi", "tim_keliling"):
            s["kabupaten"] = k["kabupaten"]
        if b["kanal"] == "radio" and stasiun:
            s["stasiun"] = stasiun[:1]
        for kk in ("jumlah_tokoh", "orang", "jangkauan", "durasi_jam"):
            if b.get(kk) is not None:
                s[kk] = b[kk]
        tindakan.append({"label": f"{b.get('peran', 'petugas')} lewat {b['kanal'].replace('_', ' ')} (jam {float(b['jam']):g})",
                         "spec": s, "bobot": KEYAKINAN[b.get("keyakinan", "sedang")]})
    iv = {"A": {"nama": "Tanpa tindakan", "langkah": []},
          "N": {"nama": "Tindakan nyata", "langkah": [t["spec"] for t in tindakan]}}
    if tindakan:
        iv["N-4"] = {"nama": "Semua tindakan 4 jam lebih awal",
                     "langkah": [{**t["spec"], "t_jam": max(0.0, t["spec"]["t_jam"] - 4)} for t in tindakan]}
        for i, t in enumerate(tindakan):
            ls = [dict(x["spec"]) for x in tindakan]
            ls[i]["t_jam"] = max(0.0, ls[i]["t_jam"] - 4)
            iv[f"N-4:{i}"] = {"nama": f"{t['label']} 4 jam lebih awal", "langkah": ls}
    return sc, iv, tindakan


def _durasi(world, sc, spec, seed, kab_mask) -> tuple[float, float]:
    from .engine import Simulation

    sim = Simulation(world, sc, spec, seed=seed, log_events=False)
    sim.run()
    over = (sim.risk[:, kab_mask] > sim.ambang).any(1)
    jam = float(np.argmax(over) * sim.dt_h) if over.any() else np.nan
    return float(over.sum() * sim.dt_h), jam


def analisis(kid: str | dict, agen: int = 20_000, putaran: int = 20, seed_dunia: int = 42, workers: int | None = None,
             progress=print) -> dict:
    from .analysis import paired_effect
    from .montecarlo import run_montecarlo
    from .world import build_world

    k = kid if isinstance(kid, dict) else muat(kid)
    err = validasi(k)
    if err:
        raise ValueError("; ".join(err))
    sc, iv, tindakan = skenario_kajian(k)
    kab = k["kabupaten"]
    out_dir = KAJIAN_DIR / f"{k['id']}_putar_ulang"
    kode = list(iv)
    if progress:
        progress(f"Kajian {k['id']}: putar ulang {len(kode)} jalur × {putaran} putaran")
    d = run_montecarlo(agen, seed_dunia, putaran, kode, sc, iv, workers=workers, progress=None, out_dir=out_dir)
    hk = pd.read_parquet(d / "hasil_kabupaten.parquet")
    dist = pd.read_parquet(d / "distrik.parquet")
    jalur = "N" if "N" in iv and iv["N"]["langkah"] else "A"
    g = hk[(hk["tipe"] == kab) & (hk["intervensi"] == jalur)]
    w = build_world(agen, seed_dunia)
    kmask = w.geo.distrik_kab == w.geo.kab_index(kab)
    dur = [_durasi(w, sc, iv[jalur], s, kmask) for s in range(min(putaran, 10))]
    # --- kenyataan
    tb = _butir(k, "tanda_bahaya")
    hasil = k.get("hasil") or {}
    eskalasi_nyata = bool(hasil.get("eskalasi")) if hasil.get("eskalasi") is not None else bool(tb)
    if k.get("near_miss"):
        eskalasi_nyata = False
    jam_nyata = float(tb[0]["jam"]) if tb and eskalasi_nyata else None
    jm = g["jam_ke_eskalasi"].dropna()
    p_esk = float(g["jam_ke_eskalasi"].notna().mean()) if len(g) else np.nan
    tabel = []
    if jam_nyata is not None:
        med = float(jm.median()) if len(jm) else np.nan
        tabel.append({"metrik": "jam sampai memanas", "kenyataan": jam_nyata,
                      "model": _r(med), "model_rentang": [_r(jm.quantile(0.1)), _r(jm.quantile(0.9))] if len(jm) else None,
                      "meleset": _r(med - jam_nyata) if len(jm) else None,
                      "penjelasan": ("model tidak memanas sama sekali di kabupaten ini" if not len(jm) else
                                     f"model memanas {'lebih cepat' if med < jam_nyata else 'lebih lambat'} ±{abs(med - jam_nyata):.0f} jam "
                                     "dibanding kenyataan")})
    tabel.append({"metrik": "apakah memanas", "kenyataan": "ya" if eskalasi_nyata else "tidak (near miss)",
                  "model": f"{p_esk:.0%} putaran memanas", "model_rentang": None,
                  "meleset": _r(abs(p_esk - (1.0 if eskalasi_nyata else 0.0)), 2),
                  "penjelasan": ("model sejalan dengan kenyataan" if abs(p_esk - eskalasi_nyata) < 0.5 else
                                 "model " + ("terlalu jarang" if eskalasi_nyata else "terlalu sering") + " memanas untuk kasus ini")})
    dn = dist[(dist["intervensi"] == jalur) & (dist["kabupaten"] == kab)]
    model_d = set(dn.loc[dn["peluang_memanas"] >= 0.5, "nama"].str.lower())
    nyata_d = {str(x).lower() for x in hasil.get("distrik") or []} | {str(b.get("distrik", "")).lower() for b in tb if b.get("distrik")}
    nyata_d.discard("")
    if nyata_d or model_d:
        tepat = model_d & nyata_d
        tabel.append({"metrik": "distrik memanas", "kenyataan": sorted(nyata_d), "model": sorted(model_d), "model_rentang": None,
                      "meleset": {"tepat": sorted(tepat), "terlewat": sorted(nyata_d - model_d), "berlebih": sorted(model_d - nyata_d)},
                      "penjelasan": f"{len(tepat)} dari {len(nyata_d)} distrik nyata juga memanas di model (peluang ≥ 50%)"})
    if hasil.get("durasi_jam"):
        dm = np.array([x[0] for x in dur])
        tabel.append({"metrik": "durasi memanas (jam)", "kenyataan": float(hasil["durasi_jam"]), "model": _r(float(np.median(dm))),
                      "model_rentang": [_r(float(np.percentile(dm, 10))), _r(float(np.percentile(dm, 90)))],
                      "meleset": _r(float(np.median(dm)) - float(hasil["durasi_jam"])),
                      "penjelasan": "lama setidaknya satu distrik di kabupaten melewati ambang risiko"})
    # --- bagaimana jika
    eff = paired_effect(hk[hk["intervensi"].isin([jalur, *[c for c in kode if c.startswith("N-4")]])], baseline=jalur)
    eff = eff[eff["tipe"] == kab]
    jika = []
    for _, r in eff.iterrows():
        nm = iv[r["intervensi"]]["nama"]
        base = float(g["siaga_jam_per_1000"].mean()) if len(g) else np.nan
        lo, hi = (100 * r["selisih_lo"] / base, 100 * r["selisih_hi"] / base) if base else (np.nan, np.nan)
        jika.append({"kode": r["intervensi"], "nama": nm, "efek_persen": _r(r["efek_persen"]), "rentang": [_r(lo), _r(hi)],
                     "kalimat": (f"Bila {nm[0].lower() + nm[1:]}, pada skenario serupa beban siaga "
                                 f"{'turun' if r['efek_persen'] < 0 else 'naik'} ±{abs(r['efek_persen']):.0f}% "
                                 f"(IK 95% perubahan {_b(lo)}% s.d. {_b(hi)}%" + ("" if r["signifikan"] else ", belum pasti") + ").")})
    galat_jam = next((t["meleset"] for t in tabel if t["metrik"] == "jam sampai memanas"), None)
    h = {"id": k["id"], "kabupaten": kab, "tanggal": str(k.get("tanggal")), "near_miss": bool(k.get("near_miss")),
         "versi_model": versi_model(), "tanda": TANDA_SIMULASI, "agen": agen, "putaran": putaran,
         "tindakan_dimodelkan": [t["label"] for t in tindakan], "seberapa_meleset": tabel, "bagaimana_jika": jika,
         "galat_jam": galat_jam, "durasi_model": [x[0] for x in dur], "folder": out_dir.name,
         "dianalisis": datetime.now().isoformat(timespec="seconds")}
    (KAJIAN_DIR / f"{k['id']}_analisis.json").write_text(json.dumps(h, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    try:
        h["pdf"] = str(pdf_kajian(k, h, KAJIAN_DIR / f"{k['id']}.pdf"))
    except Exception as e:
        h["pdf_error"] = f"{type(e).__name__}: {e}"
    _audit("analisis", k["id"], None)
    return h


def _r(x, n=1):
    try:
        return None if x is None or np.isnan(float(x)) else round(float(x), n)
    except (TypeError, ValueError):
        return None


def _b(v) -> str:
    if v is None or np.isnan(v):
        return "?"
    v = round(float(v))
    return f"{v:+d}".replace("-", "−") if v else "0"


# ---------------------------------------------------------------------------------------------- kalibrasi & lencana
def kasus_kalibrasi(folder: str | Path | None = None) -> list[dict]:
    """Kasus untuk `calibration` (banyak kasus): target jam sampai memanas di kabupaten asal (near miss →
    'tidak memanas'), bobot kasus = rata-rata keyakinan butir linimasa yang dipakai."""
    folder = Path(folder) if folder else KAJIAN_DIR
    out = []
    for p in sorted(folder.glob("*.yaml")):
        k = load_yaml(p)
        if not isinstance(k, dict) or validasi(k):
            continue
        sc, iv, _ = skenario_kajian(k)
        tb = _butir(k, "tanda_bahaya")
        dipakai = _butir(k, "muncul") + _butir(k, "diketahui") + tb
        bobot = float(np.mean([KEYAKINAN[b.get("keyakinan", "sedang")] for b in dipakai])) if dipakai else 0.25
        hasil = k.get("hasil") or {}
        esk = (not k.get("near_miss")) and (hasil.get("eskalasi") if hasil.get("eskalasi") is not None else bool(tb))
        target = float(tb[0]["jam"]) if esk and tb else None
        out.append({"kasus": k["id"], "skenario": sc, "intervensi": iv["N"] if iv["N"]["langkah"] else iv["A"],
                    "wilayah_inti": k["kabupaten"], "bobot_kasus": bobot,
                    # near miss: target = nilai "tidak memanas" yang sama dengan calibration._statistik (1,5 × durasi)
                    "target": {"jam_ke_eskalasi": target if target is not None else float(sc["durasi_jam"]) * 1.5},
                    "bobot": {"jam_ke_eskalasi": 1.0}, "near_miss": target is None})
    return out


def lencana_validasi(kab: str) -> dict:
    rows = []
    for p in KAJIAN_DIR.glob("*_analisis.json") if KAJIAN_DIR.exists() else []:
        try:
            h = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if h.get("kabupaten") == kab:
            rows.append(h)
    if not rows:
        return {"kasus": 0, "teks": "belum ada kasus nyata yang dikaji untuk kabupaten ini"}
    g = [abs(r["galat_jam"]) for r in rows if r.get("galat_jam") is not None]
    nm = sum(bool(r.get("near_miss")) for r in rows)
    akhir = max(r.get("dianalisis", "") for r in rows)[:10]
    bulan = ("Januari Februari Maret April Mei Juni Juli Agustus September Oktober November Desember").split()
    try:
        dt = datetime.fromisoformat(akhir)
        akhir_t = f"{bulan[dt.month - 1]} {dt.year}"
    except ValueError:
        akhir_t = akhir
    teks = f"{len(rows)} kasus nyata" + (f" ({nm} near miss)" if nm else "")
    if g:
        teks += f", galat jam memanas ±{np.mean(g):.0f} jam"
    teks += f", terakhir diperbarui {akhir_t}"
    return {"kasus": len(rows), "near_miss": nm, "galat_jam_rata2": _r(float(np.mean(g))) if g else None,
            "terakhir": akhir, "teks": teks}


def pdf_kajian(k: dict, h: dict, path: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    from .laporan_pdf import _setup_fonts

    _setup_fonts()
    st = {"h1": ParagraphStyle("h1", fontName="Sans-Bold", fontSize=16, leading=20),
          "h2": ParagraphStyle("h2", fontName="Sans-Bold", fontSize=12, leading=15, spaceBefore=8, spaceAfter=3),
          "b": ParagraphStyle("b", fontName="Sans", fontSize=10, leading=13)}
    P = lambda t: Paragraph(escape(str(t)), st["b"])  # noqa: E731

    def tabel(rows, w):
        t = Table(rows, colWidths=w)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee"))]))
        return t

    lin = [[P("Jam"), P("Butir"), P("Keterangan / peran"), P("Keyakinan")]]
    for b in sorted(k["linimasa"], key=lambda b: float(b["jam"])):
        lin.append([P(f"{float(b['jam']):g}"), P(b["jenis"]), P(" · ".join(str(b.get(x)) for x in ("peran", "kanal", "distrik", "keterangan") if b.get(x))),
                    P(b.get("keyakinan", "sedang"))])
    mel = [[P("Metrik"), P("Kenyataan"), P("Model"), P("Penjelasan")]]
    for t in h["seberapa_meleset"]:
        mod = f"{t['model']}" + (f" (rentang {t['model_rentang'][0]}–{t['model_rentang'][1]})" if t.get("model_rentang") else "")
        mel.append([P(t["metrik"]), P(t["kenyataan"]), P(mod), P(t["penjelasan"])])
    story = [Paragraph(f"Kajian pasca-kejadian {escape(k['id'])}", st["h1"]),
             P(f"{TANDA_SIMULASI} (bagian model) · {h['versi_model']['teks']}"),
             P(f"Kabupaten {k['kabupaten']}" + (f", distrik {k['distrik']}" if k.get("distrik") else "")
               + f" · jenis isu {k['jenis_isu']} · tanggal {k.get('tanggal')}" + (" · near miss" if k.get("near_miss") else "")),
             Paragraph("Linimasa", st["h2"]), tabel(lin, [14 * mm, 28 * mm, 105 * mm, 26 * mm]),
             Paragraph("Seberapa meleset model dibanding kenyataan", st["h2"]), tabel(mel, [36 * mm, 34 * mm, 45 * mm, 58 * mm]),
             Paragraph("Bagaimana jika", st["h2"])]
    story += [P(j["kalimat"]) for j in h["bagaimana_jika"]] or [P("Tidak ada tindakan yang bisa dimodelkan.")]
    pel = k.get("pelajaran") or {}
    story.append(Paragraph("Pelajaran", st["h2"]))
    for kk, lbl in (("berhasil", "Yang berhasil"), ("menghambat", "Yang menghambat"), ("usulan", "Usulan perubahan kartu/profil")):
        if pel.get(kk):
            story.append(P(f"{lbl}: " + "; ".join(map(str, pel[kk]))))
    story += [Spacer(1, 6), P("Data kejadian nyata sensitif: bagikan keluar hanya dalam bentuk agregat.")]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                      bottomMargin=16 * mm, title=f"Kajian {k['id']}").build(story)
    return path


__all__ = ["validasi", "simpan", "muat", "daftar", "dari_situasi", "analisis", "kasus_kalibrasi", "lencana_validasi",
           "CONTOH", "load_params", "copy"]
