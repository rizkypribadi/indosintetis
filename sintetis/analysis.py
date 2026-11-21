"""FR-10 Analisis per tipe wilayah: rata-rata, interval kepercayaan 95%, efek berpasangan terhadap
baseline, dan stabilitas peringkat intervensi (bootstrap)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

METRIK_UTAMA = "siaga_jam_per_1000"   # beban siaga aktif (potensi tindakan fisik); makin kecil makin baik
METRIK = {
    "siaga_jam_per_1000": "Siaga aktif (orang-jam per 1.000 penduduk)",
    "puncak_siaga": "Puncak siaga aktif (proporsi)",
    "jangkauan_hoaks": "Jangkauan hoaks (proporsi pernah dengar)",
    "puncak_penyebar": "Puncak penyebar (proporsi)",
    "jam_ke_eskalasi": "Jam hingga eskalasi pertama",
    "porsi_distrik_eskalasi": "Porsi distrik melewati ambang risiko",
}
URUT_TIPE = ["kota", "pesisir", "pedalaman", "kepulauan", "semua"]
# Porsi paparan hoaks pertama per kanal (kolom ada di hasil Monte Carlo versi baru saja).
KANAL_PAPARAN = {"paparan_wa": "WhatsApp", "paparan_fb": "Facebook", "paparan_tatap_muka": "Tatap muka",
                 "paparan_radio": "Radio"}


def mean_ci(x: np.ndarray, level: float = 0.95) -> tuple[float, float, float]:
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return np.nan, np.nan, np.nan
    m = float(x.mean())
    if len(x) < 2:
        return m, m, m
    h = stats.t.ppf((1 + level) / 2, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))
    return m, m - h, m + h


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (tipe, kode), g in df.groupby(["tipe", "intervensi"]):
        r = {"tipe": tipe, "intervensi": kode, "putaran": len(g)}
        for m in METRIK:
            mu, lo, hi = mean_ci(g[m].to_numpy(float))
            r[m], r[m + "_lo"], r[m + "_hi"] = mu, lo, hi
        r["peluang_eskalasi"] = float(g["jam_ke_eskalasi"].notna().mean())
        for m in [*KANAL_PAPARAN, "paparan_provokator"]:
            if m in g:
                r[m] = float(g[m].mean())
        rows.append(r)
    out = pd.DataFrame(rows)
    ada = set(out["tipe"])   # tipe wilayah dulu (urutan tetap), lalu kabupaten (F1) menurut abjad
    out["tipe"] = pd.Categorical(out["tipe"], [t for t in URUT_TIPE if t in ada] + sorted(ada - set(URUT_TIPE)), ordered=True)
    return out.sort_values(["tipe", "intervensi"]).reset_index(drop=True)


def paired_effect(df: pd.DataFrame, metric: str = METRIK_UTAMA, baseline: str = "A") -> pd.DataFrame:
    """Selisih relatif terhadap baseline dengan seed berpasangan (bilangan acak bersama)."""
    rows = []
    for tipe, g in df.groupby("tipe"):
        piv = g.pivot(index="seed", columns="intervensi", values=metric)
        if baseline not in piv:
            continue
        base = piv[baseline]
        for kode in piv.columns:
            if kode == baseline:
                continue
            diff = (piv[kode] - base).to_numpy(float)
            mu, lo, hi = mean_ci(diff)
            b = float(base.mean())
            rows.append({"tipe": tipe, "intervensi": kode, "selisih": mu, "selisih_lo": lo, "selisih_hi": hi,
                         "efek_persen": 100 * mu / b if b else np.nan,
                         "signifikan": bool(hi < 0 or lo > 0)})
    return pd.DataFrame(rows)


def ranking(df: pd.DataFrame, metric: str = METRIK_UTAMA, n_boot: int = 1000, seed: int = 0) -> pd.DataFrame:
    """Peringkat intervensi per tipe (1 = terbaik / nilai terkecil) dan stabilitas bootstrap:
    peluang peringkat tiap intervensi sama dengan peringkat titik-dugaannya, serta peluang
    urutan lengkap tidak berubah."""
    rng = np.random.default_rng(seed)
    rows = []
    for tipe, g in df.groupby("tipe"):
        piv = g.pivot(index="seed", columns="intervensi", values=metric).dropna()
        if piv.empty:
            continue
        X = piv.to_numpy(float)
        point = np.argsort(np.argsort(X.mean(0)))
        idx = rng.integers(0, len(X), (n_boot, len(X)))
        boot_rank = np.argsort(np.argsort(X[idx].mean(1), axis=1), axis=1)
        same = (boot_rank == point).mean(0)
        full = float((boot_rank == point).all(1).mean())
        for j, kode in enumerate(piv.columns):
            rows.append({"tipe": tipe, "intervensi": kode, "peringkat": int(point[j]) + 1,
                         "stabilitas": float(same[j]), "stabilitas_urutan_penuh": full,
                         "nilai": float(X[:, j].mean())})
    out = pd.DataFrame(rows)
    return out.sort_values(["tipe", "peringkat"]).reset_index(drop=True)


def recommendations(rank: pd.DataFrame, eff: pd.DataFrame, names: dict[str, str]) -> list[str]:
    """Kalimat rekomendasi SOP per tipe wilayah (draf, untuk dibahas bersama mitra)."""
    out = []
    for tipe in URUT_TIPE:
        r = rank[rank["tipe"] == tipe]
        if r.empty:
            continue
        best = r[r["intervensi"] != "A"].iloc[0] if (r["intervensi"] != "A").any() else r.iloc[0]
        e = eff[(eff["tipe"] == tipe) & (eff["intervensi"] == best["intervensi"])]
        efek = f"{e['efek_persen'].iat[0]:+.0f}% beban siaga dibanding tanpa intervensi" if len(e) else ""
        sig = "" if len(e) and e["signifikan"].iat[0] else " (belum signifikan)"
        stab = f"stabilitas peringkat {best['stabilitas']:.0%}"
        lemah = r[r["intervensi"] != "A"].iloc[-1]
        out.append(f"**{tipe.capitalize()}**: utamakan {best['intervensi']} — {names.get(best['intervensi'], '')} "
                   f"({efek}{sig}; {stab}). Paling lemah: {lemah['intervensi']} — {names.get(lemah['intervensi'], '')}.")
    return out
