"""R3 Kurva "harga keterlambatan": berapa ruginya setiap jam menunda tindakan, dan kapan sebuah
intervensi praktis sudah tidak berguna.

Untuk tiap intervensi berwaktu (medsos resmi, gereja & adat, radio, penindakan, tim keliling) waktu mulainya
digeser 0..24 jam SETELAH ISU DIKETAHUI PETUGAS (F5, `t_sejak_deteksi`). Prebunking (E) digeser −14..0 hari
sekaligus cakupannya. Setiap titik dijalankan sebagai Monte Carlo dengan seed berpasangan terhadap baseline A,
sehingga selisihnya bersih dari keacakan dinamika.

Keluaran turunan (dihitung di Python, bukan LLM):
  - harga per jam   kemiringan beban siaga di jendela 0–6 jam, dalam % per jam keterlambatan (rentang bootstrap)
  - batas waktu     jam saat manfaat turun di bawah 25% manfaat bila dilakukan di jam ke-0
  - kalimat         satu kalimat sederhana untuk kartu, Ruang Situasi, dan laporan lapangan
  - peringatan      "putaran kurang" bila kurva naik-turun melebihi derau yang wajar

Bentuk kurva lebih bisa dipercaya daripada angka absolutnya: pesan utamanya "lebih cepat jauh lebih baik,
terutama sebelum jam ke-X", bukan angka persen yang tepat.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from .analysis import mean_ci
from .config import OUT_DIR, load_interventions, load_params, load_scenario, versi_model

BERWAKTU = ("medsos_resmi", "gereja_adat", "radio", "penindakan", "tim_keliling")
NAMA_PENDEK = {"medsos_resmi": "Klarifikasi resmi di media sosial", "gereja_adat": "Klarifikasi lewat gereja dan adat",
               "radio": "Siaran radio", "penindakan": "Penurunan akun provokator", "tim_keliling": "Tim keliling",
               "prebunking": "Prebunking"}
METRIK = "siaga_jam_per_1000"
AMBANG_MANFAAT = 0.25


def jenis_utama(spec: dict) -> str:
    js = [s["jenis"] for s in spec.get("langkah", [])]
    return next((j for j in js if j in BERWAKTU), js[0] if js else "tanpa")


def nama_pendek(spec: dict) -> str:
    return NAMA_PENDEK.get(jenis_utama(spec), spec.get("nama", "?"))


def bisa_digeser(spec: dict) -> bool:
    return any(s["jenis"] in BERWAKTU for s in spec.get("langkah", []))


def varian_waktu(iv: dict, kode: str, jam: list[float]) -> dict[str, dict]:
    """Varian `kode@tJ`: semua langkah dimulai J jam sejak isu diketahui (jarak antar-langkah dipertahankan)."""
    base = iv[kode]
    t0 = min(float(s.get("t_jam", 0)) for s in base["langkah"])
    out = {}
    for j in jam:
        spec = copy.deepcopy(base)
        for s in spec["langkah"]:
            s["t_sejak_deteksi"] = float(j) + float(s.pop("t_jam", 0)) - t0
        spec["nama"] = f"{base['nama'].split(',')[0]}, {j:g} jam sejak isu diketahui"
        out[f"{kode}@t{j:g}"] = spec
    return out


def varian_prebunking(iv: dict, kode: str, hari: list[float], cakupan: list[float]) -> dict[str, dict]:
    base = iv[kode]
    out = {}
    for h in hari:
        for c in cakupan:
            spec = copy.deepcopy(base)
            for s in spec["langkah"]:
                if s["jenis"] == "prebunking":
                    s["t_jam"], s["cakupan"] = float(h) * 24 - (0 if h else 0.5), float(c)
            spec["nama"] = f"Prebunking {abs(h):g} hari sebelum isu, cakupan {c:.0%}"
            out[f"{kode}@h{h:g}c{c:g}"] = spec
    return out


def _jam_list(jam: str | tuple | list) -> list[float]:
    if isinstance(jam, str):                      # "0:24:2"
        a, b, c = (float(x) for x in jam.split(":"))
        return [round(float(x), 2) for x in np.arange(a, b + 1e-9, c)]
    if isinstance(jam, tuple) and len(jam) == 3:
        return _jam_list(f"{jam[0]}:{jam[1]}:{jam[2]}")
    return [float(x) for x in jam]


def hitung_keterlambatan(skenario: dict | str | Path | None = None, kode: str = "CDF", jam="0:24:2",
                         putaran: int = 50, n: int = 20_000, seed_dunia: int = 42, interventions: dict | None = None,
                         prebunking_hari: list[float] = (-14, -7, -3, -1, 0), prebunking_cakupan: list[float] = (0.2, 0.4, 0.6, 0.8),
                         workers: int | None = None, out_dir: str | Path | None = None, progress=print,
                         mode: str = "abm", beku: str | None = None) -> Path:
    from .montecarlo import run_montecarlo

    sc = skenario if isinstance(skenario, dict) else load_scenario(skenario)
    iv = interventions or load_interventions()
    jam = _jam_list(jam)
    ivs = {"A": iv["A"]}
    for k in dict.fromkeys(kode):
        if k == "A" or k not in iv:
            continue
        if any(s["jenis"] == "prebunking" for s in iv[k]["langkah"]):
            ivs.update(varian_prebunking(iv, k, list(prebunking_hari), list(prebunking_cakupan)))
        elif bisa_digeser(iv[k]):
            ivs.update(varian_waktu(iv, k, jam))
    if len(ivs) < 2:
        raise ValueError("Tidak ada intervensi berwaktu yang bisa digeser")
    out = Path(out_dir) if out_dir else OUT_DIR / f"keterlambatan_{sc.get('kode', 'skenario')}_{datetime.now():%Y%m%d-%H%M%S}"
    if progress:
        progress(f"Keterlambatan: {len(ivs)} varian × {putaran} putaran ({len(ivs) * putaran} putaran)")
    d = run_montecarlo(n, seed_dunia, putaran, list(ivs), sc, ivs, mode=mode, beku=beku, workers=workers,
                       progress=progress, out_dir=out)
    analisis(d, {k: iv[k] for k in dict.fromkeys("A" + kode) if k in iv}, sc)
    return d


# ---------------------------------------------------------------------------------------------- analisis
def _paired(df: pd.DataFrame, wilayah: str, metric: str = METRIK) -> pd.DataFrame:
    g = df[df["tipe"] == wilayah]
    return g.pivot(index="seed", columns="intervensi", values=metric)


def _slope_pct(jam: np.ndarray, Y: np.ndarray) -> float:
    """Kemiringan rata-rata Y (seed × jam) terhadap jam, dalam % nilai di jam pertama, per jam."""
    m = Y.mean(0)
    if m[0] <= 0:
        return np.nan
    return float(np.polyfit(jam, m, 1)[0] / m[0] * 100)


def kurva_satu(piv: pd.DataFrame, kode: str, jam_all: list[float]) -> list[dict]:
    rows = []
    base = piv["A"].to_numpy(float)
    for j in jam_all:
        c = f"{kode}@t{j:g}"
        if c not in piv:
            continue
        x = piv[c].to_numpy(float)
        mu, lo, hi = mean_ci(x)
        bm, blo, bhi = mean_ci(base - x)
        rows.append({"jam": j, "nilai": mu, "nilai_lo": lo, "nilai_hi": hi, "manfaat": bm, "manfaat_lo": blo,
                     "manfaat_hi": bhi, "baseline": float(base.mean()), "efek_persen": -100 * bm / base.mean() if base.mean() else np.nan})
    return rows


def ringkas_kurva(piv: pd.DataFrame, kode: str, rows: list[dict], spec: dict, n_boot: int = 400, seed: int = 0) -> dict:
    """Harga per jam (0–6 jam), batas waktu efektif (manfaat < 25% manfaat jam 0), kalimat, peringatan."""
    if not rows:
        return {}
    jam = np.array([r["jam"] for r in rows])
    cols = [f"{kode}@t{j:g}" for j in jam]
    Y = piv[cols].to_numpy(float)
    jendela = jam <= 6 + 1e-9
    harga, a, b = np.nan, np.nan, np.nan
    if jendela.sum() >= 2:
        harga = _slope_pct(jam[jendela], Y[:, jendela])
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, len(Y), (n_boot, len(Y)))
        bs = np.array([_slope_pct(jam[jendela], Y[i][:, jendela]) for i in idx])
        bs = bs[~np.isnan(bs)]
        if len(bs):
            a, b = (float(np.percentile(bs, q)) for q in (2.5, 97.5))
    b0, b0_lo = rows[0]["manfaat"], rows[0]["manfaat_lo"]
    batas, lewat = None, False
    if b0 > 0 and b0_lo > 0:
        for r0, r1 in zip(rows, rows[1:]):
            if r1["manfaat"] < AMBANG_MANFAAT * b0:
                # interpolasi linear di antara dua titik
                f = (r0["manfaat"] - AMBANG_MANFAAT * b0) / max(r0["manfaat"] - r1["manfaat"], 1e-9)
                batas = float(r0["jam"] + f * (r1["jam"] - r0["jam"]))
                break
        else:
            lewat = True
    # Uji kewajaran: manfaat seharusnya tidak naik bila tindakan makin lambat. Kenaikan > 2 SE gabungan = derau.
    se = [(r["manfaat_hi"] - r["manfaat"]) / 1.96 for r in rows]
    loncat = [rows[i + 1]["manfaat"] - rows[i]["manfaat"] - 2 * np.hypot(se[i], se[i + 1]) for i in range(len(rows) - 1)]
    lebar0 = (rows[0]["manfaat_hi"] - rows[0]["manfaat_lo"]) / max(abs(b0), 1e-9)
    peringatan = []
    if any(x > 0 for x in loncat) or lebar0 > 1.5:
        peringatan.append("putaran kurang: kurva naik-turun melebihi derau yang wajar; tambah --putaran")
    kep = "rendah"
    if not np.isnan(a) and (a > 0 or b < 0):
        kep = "tinggi" if (b - a) < abs(harga) else "sedang"
    if peringatan and kep == "tinggi":
        kep = "sedang"
    nm = nama_pendek(spec)
    if b0 <= 0 or b0_lo <= 0:
        kalimat = (f"{nm} belum terbukti mengurangi beban siaga pada skenario ini, bahkan bila dilakukan segera "
                   "setelah isu diketahui (keyakinan rendah).")
    else:
        waktu = (f"sebelum jam ke-{batas:.0f} sejak isu diketahui" if batas is not None
                 else f"sampai sedikitnya jam ke-{jam.max():g} sejak isu diketahui" if lewat else "secepatnya")
        kalimat = f"{nm} paling berguna bila dilakukan {waktu}."
        if not np.isnan(harga) and harga > 0:
            kalimat += (f" Setiap jam menunda menambah beban siaga ±{_bul(harga)}% (rentang {_bul(a)} s.d. {_bul(b)}%, "
                        f"keyakinan {kep}).")
        else:
            kalimat += f" Di 6 jam pertama, menunda belum terlihat menambah beban siaga secara jelas (keyakinan {kep})."
    return {"kode": kode, "nama": nm, "jenis": jenis_utama(spec), "harga_per_jam_persen": _f(harga),
            "harga_lo": _f(a), "harga_hi": _f(b), "batas_waktu_jam": _f(batas), "melewati_rentang": lewat,
            "jam_maks": float(jam.max()),
            "manfaat_jam0": _f(b0), "efek_jam0_persen": _f(rows[0]["efek_persen"]), "keyakinan": kep,
            "kalimat": kalimat, "peringatan": peringatan}


def ringkas_prebunking(df: pd.DataFrame, wilayah: str, kode: str, spec: dict, params: dict) -> dict:
    piv = _paired(df, wilayah)
    cols = [c for c in piv if c.startswith(f"{kode}@h")]
    if not cols:
        return {}
    base = piv["A"].to_numpy(float)
    rows = []
    for c in cols:
        h = float(c.split("@h")[1].split("c")[0])
        cak = float(c.split("c")[-1])
        bm, blo, bhi = mean_ci(base - piv[c].to_numpy(float))
        rows.append({"hari": h, "cakupan": cak, "manfaat": bm, "manfaat_lo": blo, "manfaat_hi": bhi,
                     "efek_persen": -100 * bm / base.mean() if base.mean() else np.nan})
    r = pd.DataFrame(rows)
    paruh = (params.get("prebunking") or {}).get("paruh_hari")
    per_cak = r.groupby("cakupan")["efek_persen"].mean()
    terbaik = r.loc[r["manfaat"].idxmax()]
    kal = (f"Prebunking dengan cakupan {per_cak.index.min():.0%} menurunkan beban siaga ±{-per_cak.iloc[0]:.0f}%, "
           f"cakupan {per_cak.index.max():.0%} ±{-per_cak.iloc[-1]:.0f}%.")
    if not paruh:
        kal += (" Di model ini efek prebunking tidak memudar, jadi kapan dilakukan (sebelum isu) tidak berpengaruh; "
                "isi `prebunking.paruh_hari` di parameter.yaml bila ada data.")
    else:
        kal += f" Paling berguna {abs(terbaik['hari']):g} hari sebelum isu atau lebih dekat (efek memudar, paruh {paruh:g} hari)."
    return {"kode": kode, "nama": "Prebunking", "jenis": "prebunking", "titik": r.to_dict("records"),
            "kalimat": kal, "paruh_hari": paruh, "keyakinan": "sedang" if (r["manfaat_lo"] > 0).all() else "rendah"}


def analisis(d: Path, iv_asal: dict, sc: dict | None = None) -> dict:
    """Bangun kurva.parquet, ringkasan.json, dan PNG per intervensi dari folder hasil Monte Carlo varian."""
    d = Path(d)
    df = pd.read_parquet(d / "hasil.parquet")
    kab = d / "hasil_kabupaten.parquet"
    if kab.exists():
        df = pd.concat([df, pd.read_parquet(kab)], ignore_index=True)
    params = load_params()
    jam_all = sorted({float(c.split("@t")[1]) for c in df["intervensi"].unique() if "@t" in c})
    kurva_rows, ringkasan = [], {}
    for wilayah in df["tipe"].unique():
        piv = _paired(df, wilayah)
        if "A" not in piv:
            continue
        ringkasan[wilayah] = {}
        for kode, spec in iv_asal.items():
            if kode == "A":
                continue
            if any(s["jenis"] == "prebunking" for s in spec["langkah"]):
                rp = ringkas_prebunking(df, wilayah, kode, spec, params)
                if rp:
                    ringkasan[wilayah][kode] = rp
                continue
            rows = kurva_satu(piv, kode, jam_all)
            if not rows:
                continue
            kurva_rows += [{"wilayah": wilayah, "kode": kode, **r} for r in rows]
            ringkasan[wilayah][kode] = ringkas_kurva(piv, kode, rows, spec)
    kurva = pd.DataFrame(kurva_rows)
    kurva.to_parquet(d / "kurva.parquet", index=False)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8")) if (d / "manifest.json").exists() else {}
    hasil = {"skenario": (sc or {}).get("nama") or man.get("skenario"), "putaran": man.get("putaran"),
             "agen": man.get("agen"), "jam": jam_all, "relatif": "jam sejak isu diketahui petugas",
             "jeda_deteksi_jam": None, "versi_model": man.get("versi_model") or versi_model(),
             "asal": {k: {"nama": v["nama"], "jenis": jenis_utama(v)} for k, v in iv_asal.items()}, "ringkasan": ringkasan}
    (d / "ringkasan.json").write_text(json.dumps(_bersih(hasil), ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        for kode in {r["kode"] for r in kurva_rows}:
            png_kurva(kurva, ringkasan.get("semua", {}).get(kode, {}), kode, d / f"keterlambatan_{kode}.png")
    except Exception:          # grafik gagal tidak boleh menghapus angka
        pass
    return hasil


def png_kurva(kurva: pd.DataFrame, ring: dict, kode: str, path: Path, wilayah: str = "semua") -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    k = kurva[(kurva["wilayah"] == wilayah) & (kurva["kode"] == kode)].sort_values("jam")
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    ax.fill_between(k["jam"], k["nilai_lo"], k["nilai_hi"], color="#bbbbbb", alpha=0.6, lw=0, label="IK 95%")
    ax.plot(k["jam"], k["nilai"], "-o", color="#111111", ms=3.5, lw=1.6, label=ring.get("nama", kode))
    ax.axhline(k["baseline"].iat[0], color="#555555", ls=":", lw=1.2, label="tanpa tindakan")
    if ring.get("batas_waktu_jam") is not None:
        ax.axvline(ring["batas_waktu_jam"], color="#000000", ls="--", lw=1)
        ax.text(ring["batas_waktu_jam"], ax.get_ylim()[1], f" batas ±jam {ring['batas_waktu_jam']:.0f}", va="top", fontsize=8)
    ax.set_xlabel("jam sejak isu diketahui petugas")
    ax.set_ylabel("beban siaga (orang-jam / 1.000)")
    ax.set_title(f"Harga keterlambatan · {ring.get('nama', kode)}", loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=7.5)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def muat(d: str | Path) -> dict | None:
    """Muat ringkasan.json + kurva.parquet dari folder keterlambatan (atau None)."""
    d = Path(d)
    if not (d / "ringkasan.json").exists():
        return None
    h = json.loads((d / "ringkasan.json").read_text(encoding="utf-8"))
    if (d / "kurva.parquet").exists():
        h["kurva"] = pd.read_parquet(d / "kurva.parquet").to_dict("records")
    return h


def untuk_laporan(h: dict, wilayah: str = "semua") -> dict:
    """Potongan ringkas untuk fakta laporan lapangan / PDF ("Kapan harus bertindak")."""
    ring = h["ringkasan"].get(wilayah, {})
    kurva = {}
    for r in h.get("kurva", []):
        if r["wilayah"] == wilayah:
            kurva.setdefault(r["kode"], []).append({k: r[k] for k in ("jam", "nilai", "nilai_lo", "nilai_hi", "baseline")})
    return {"wilayah": wilayah, "relatif": h.get("relatif"), "ringkasan": ring, "kurva": kurva}


def _bul(v) -> str:
    """Bilangan bulat dengan tanda minus tipografis, tanpa "−0"."""
    return f"{round(float(v)):d}".replace("-", "−")


def _f(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 3)


def _bersih(o):
    if isinstance(o, dict):
        return {k: _bersih(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_bersih(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if np.isnan(o) else round(float(o), 4)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


__all__ = ["hitung_keterlambatan", "analisis", "muat", "untuk_laporan", "varian_waktu", "varian_prebunking", "stats"]
