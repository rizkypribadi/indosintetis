"""Analisis sensitivitas (PRD §13 minggu 17–20, mitigasi §14): apakah peringkat intervensi bertahan
bila parameter yang belum terkalibrasi digeser?

Dua bagian, keduanya memakai seed berpasangan antar-intervensi:
  1. Satu-per-satu (OAT): tiap parameter diset ke batas rendah lalu tinggi, yang lain tetap nominal.
  2. Global: sampel Latin hypercube atas semua parameter sekaligus; dilaporkan seberapa sering
     intervensi terbaik tetap terbaik, dan korelasi peringkat (Spearman) parameter terhadap keluaran.
  3. Sobol (`run_sobol`): dekomposisi varians dengan rancangan Saltelli. Indeks orde-satu S1 = porsi
     varians keluaran yang disebabkan parameter itu sendiri; indeks total ST = termasuk semua interaksinya.
     ST kecil → parameter boleh dibiarkan pada nilai nominal; ST − S1 besar → parameter bekerja lewat interaksi.
Rentang parameter ada di config/sensitivitas.yaml.
"""
from __future__ import annotations

import copy
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import qmc

from . import montecarlo as mc
from .analysis import METRIK_UTAMA, URUT_TIPE
from .config import CONFIG_DIR, OUT_DIR, _set_dotted, code_version, load_interventions, load_params, load_scenario, load_yaml
from .engine import Simulation
from .llm.orchestrator import make_decider
from .world import build_world

_BASE: dict = {}


def _init(n, seed_dunia, params, scenario, interventions, mode, beku):
    global _BASE
    mc._init(n, seed_dunia, params, scenario, interventions, mode, beku)
    _BASE = params


def _one(ci: int, overrides: dict, code: str, seed: int) -> list[dict]:
    params = copy.deepcopy(_BASE)
    for k, v in overrides.items():
        _set_dotted(params, k, v)
    mc._W.params = params
    sc, iv = mc._CTX["sc"], mc._CTX["iv"]
    sim = Simulation(mc._W, sc, iv[code], seed=seed, decider=make_decider(mc._CTX["mode"], mc._CTX["beku"], sc),
                     log_events=False, kode_intervensi=code)
    res = sim.run()
    return [{"konfig": ci, **r} for r in mc.run_metrics(sim, res, code, seed)]


def _nominal(params: dict, key: str) -> float:
    d = params
    for k in key.split("."):
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return float(d) if not isinstance(d, dict) else 1.0   # skala_sinyal tidak ada di parameter.yaml


def run_sensitivity(n: int = 20_000, seed_dunia: int = 42, putaran: int = 3, sampel: int = 40, kode: str = "ABCDEF",
                    scenario: dict | None = None, mode: str = "abm", beku: str | None = None,
                    workers: int | None = None, nama: str | None = None, progress=print) -> Path:
    scenario = scenario or load_scenario()
    interventions, params = load_interventions(), load_params()
    rentang = load_yaml(CONFIG_DIR / "sensitivitas.yaml")["rentang"]
    keys = list(rentang)
    if mode != "abm" and not beku:
        raise ValueError("Sensitivitas dengan LLM wajib memakai tabel beku (--beku).")
    build_world(n, seed_dunia, params=params)

    configs = make_configs(rentang, sampel, seed_dunia)

    jobs = [(ci, c["ubah"], code, s) for ci, c in enumerate(configs) for s in range(putaran) for code in kode]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    rows, t0, done = [], time.perf_counter(), 0
    init = (n, seed_dunia, params, scenario, interventions, mode, beku)
    progress = progress or (lambda s: None)
    progress(f"{len(configs)} konfigurasi × {putaran} seed × {len(kode)} intervensi = {len(jobs)} putaran")
    if workers == 1:
        _init(*init)
        it = (_one(*j) for j in jobs)
    else:
        ex = ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=init)
        it = (f.result() for f in as_completed([ex.submit(_one, *j) for j in jobs]))
    for r in it:
        rows += r
        done += 1
        if done % max(1, len(jobs) // 10) == 0 or done == len(jobs):
            progress(f"  {done}/{len(jobs)} putaran selesai ({time.perf_counter() - t0:.0f} dtk)")
    if workers != 1:
        ex.shutdown()

    out = OUT_DIR / (nama or f"sensitivitas_{scenario.get('kode', 'skenario')}_{n}")
    out.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_parquet(out / "hasil.parquet", index=False)
    hasil = analyze(df, configs, keys, {k: _nominal(params, k) for k in keys}, rentang)
    hasil["pengaturan"] = {"agen": n, "seed_dunia": seed_dunia, "seed_per_konfigurasi": putaran, "sampel_global": sampel,
                           "skenario": scenario["nama"], "mode_llm": mode, "tabel_beku": beku,
                           "versi_kode": code_version(), "detik": round(time.perf_counter() - t0, 1)}
    (out / "sensitivitas.json").write_text(json.dumps(hasil, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(hasil, {c: interventions[c]["nama"] for c in kode})
    (out / "sensitivitas.md").write_text(md, encoding="utf-8")
    from .report import html_page, md_to_html

    (out / "sensitivitas.html").write_text(html_page(md_to_html(md), "Analisis sensitivitas"), encoding="utf-8")
    return out


def make_configs(rentang: dict, sampel: int, seed: int) -> list[dict]:
    """Konfigurasi 0 = nominal; lalu OAT rendah/tinggi; lalu sampel Latin hypercube."""
    keys = list(rentang)
    configs: list[dict] = [{"jenis": "nominal", "ubah": {}}]
    for k in keys:
        for label, v in zip(("rendah", "tinggi"), rentang[k]):
            configs.append({"jenis": "oat", "parameter": k, "sisi": label, "ubah": {k: float(v)}})
    lo, hi = np.array([rentang[k][0] for k in keys], float), np.array([rentang[k][1] for k in keys], float)
    for row in qmc.scale(qmc.LatinHypercube(len(keys), seed=seed).random(sampel), lo, hi):
        configs.append({"jenis": "global", "ubah": {k: round(float(v), 4) for k, v in zip(keys, row)}})
    return configs


def _urutan(v: pd.Series) -> str:
    return ">".join(v.sort_values().index)


def analyze(df: pd.DataFrame, configs: list[dict], keys: list[str], nominal: dict, rentang: dict) -> dict:
    """Ringkas per konfigurasi: nilai metrik utama per intervensi (rata-rata antar-seed), peringkat, efek."""
    mean = df.groupby(["konfig", "tipe", "intervensi"])[METRIK_UTAMA].mean().unstack("intervensi")
    semua = mean.xs("semua", level="tipe")
    nom = semua.loc[0]
    urut_nom, terbaik_nom = _urutan(nom), nom.drop("A", errors="ignore").idxmin()
    tiga_nom = urut_nom.split(">")[:3]

    def baris(ci: int) -> dict:
        v = semua.loc[ci]
        best = v.drop("A", errors="ignore").idxmin()
        return {"urutan": _urutan(v), "terbaik": best, "baseline": round(float(v.get("A", np.nan)), 1),
                "efek_terbaik_persen": round(float(100 * (v[best] - v["A"]) / v["A"]), 1) if "A" in v and v["A"] else None,
                "efek_persen": {c: round(float(100 * (v[c] - v["A"]) / v["A"]), 1) for c in v.index
                                if c != "A" and "A" in v and v["A"]}}

    oat = [{"parameter": c["parameter"], "sisi": c["sisi"], "nilai": c["ubah"][c["parameter"]],
            "nominal": nominal[c["parameter"]], **baris(ci)}
           for ci, c in enumerate(configs) if c["jenis"] == "oat"]
    for r in oat:
        r["tiga_besar_berubah"], r["terbaik_berubah"] = r["urutan"].split(">")[:3] != tiga_nom, r["terbaik"] != terbaik_nom

    g_idx = [ci for ci, c in enumerate(configs) if c["jenis"] == "global"]
    glob = {}
    if g_idx:
        G = semua.loc[g_idx]
        codes = [c for c in G.columns if c != "A"]
        best = G[codes].idxmin(axis=1)
        X = pd.DataFrame([configs[ci]["ubah"] for ci in g_idx], index=g_idx)[keys]
        eff = {c: 100 * (G[c] - G["A"]) / G["A"] for c in codes} if "A" in G else {}
        target = {"beban_siaga_baseline_A": G["A"]} if "A" in G else {}
        target |= {f"efek_{terbaik_nom}_persen": eff[terbaik_nom]} if eff else {}
        kor = {t: {k: round(float(stats.spearmanr(X[k], y).statistic), 2) for k in keys} for t, y in target.items()}
        per_tipe = {}
        for tipe in URUT_TIPE:
            if tipe not in mean.index.get_level_values("tipe"):
                continue
            mt = mean.xs(tipe, level="tipe")
            b0 = mt.loc[0].drop("A", errors="ignore").idxmin()
            per_tipe[tipe] = {"terbaik_nominal": b0,
                              "porsi_sampel_tetap_terbaik": round(float((mt.loc[g_idx, codes].idxmin(axis=1) == b0).mean()), 3)}
        glob = {
            "sampel": len(g_idx),
            "porsi_sampel_terbaik_tetap": round(float((best == terbaik_nom).mean()), 3),
            "porsi_sampel_tiga_besar_tetap": round(float(np.mean([_urutan(G.loc[i]).split(">")[:3] == tiga_nom for i in g_idx])), 3),
            "terbaik_per_sampel": best.value_counts().to_dict(),
            "rentang_efek_persen": {c: [round(float(np.percentile(eff[c], q)), 1) for q in (5, 50, 95)] for c in eff},
            "korelasi_spearman": kor, "per_tipe_wilayah": per_tipe,
        }
    return {"metrik": METRIK_UTAMA, "nominal": {"urutan": urut_nom, "terbaik": terbaik_nom, **baris(0)},
            "rentang": {k: list(map(float, rentang[k])) for k in keys}, "satu_per_satu": oat, "global": glob}


def to_markdown(h: dict, names: dict[str, str]) -> str:
    nom, g = h["nominal"], h["global"]
    md = ["## Analisis sensitivitas", "",
          "Menguji apakah kesimpulan bertahan bila parameter yang belum terkalibrasi digeser dalam rentang "
          "ketidakpastiannya (`config/sensitivitas.yaml`). Metrik: beban siaga aktif per 1.000 penduduk, semua wilayah.", "",
          f"- Urutan nominal (terbaik → terburuk): **{nom['urutan']}**; terbaik **{nom['terbaik']}** — {names.get(nom['terbaik'], '')}"]
    if g:
        md += [f"- Dari {g['sampel']} sampel acak atas semua parameter sekaligus, **{nom['terbaik']} tetap terbaik di "
               f"{g['porsi_sampel_terbaik_tetap']:.0%} sampel**; urutan tiga besar ({'>'.join(nom['urutan'].split('>')[:3])}) "
               f"tidak berubah di {g['porsi_sampel_tiga_besar_tetap']:.0%} sampel.",
               "- Urutan di papan bawah (A, B, C) sering bertukar karena selisihnya kecil dibanding derau antar-seed; "
               "jangan ditafsirkan sebagai perubahan nyata.",
               "- Terbaik per tipe wilayah: " + "; ".join(
                   f"{t} {v['terbaik_nominal']} ({v['porsi_sampel_tetap_terbaik']:.0%} sampel)" for t, v in g["per_tipe_wilayah"].items()),
               "", "### Rentang efek terhadap baseline A di semua sampel (persentil 5 – median – 95)", "",
               "| Intervensi | P5 | Median | P95 |", "|---|---|---|---|"]
        md += [f"| {c} — {names.get(c, '')} | {v[0]:+.0f}% | {v[1]:+.0f}% | {v[2]:+.0f}% |" for c, v in sorted(g["rentang_efek_persen"].items())]
        md += ["", "### Parameter yang paling menggerakkan hasil (korelasi peringkat Spearman)", ""]
        tk = list(g["korelasi_spearman"])
        md += ["| Parameter | " + " | ".join(tk) + " |", "|---|" + "---|" * len(tk)]
        first = g["korelasi_spearman"][tk[0]]
        for k in sorted(first, key=lambda k: -abs(first[k])):
            md.append(f"| `{k}` | " + " | ".join(f"{g['korelasi_spearman'][t][k]:+.2f}" for t in tk) + " |")
    md += ["", "### Satu parameter digeser, yang lain tetap", "",
           "| Parameter | Nilai (nominal) | Baseline A | Terbaik | Efek terbaik | Urutan | Tiga besar berubah? |", "|---|---|---|---|---|---|---|"]
    for r in h["satu_per_satu"]:
        flag = "terbaik berubah" if r["terbaik_berubah"] else "tiga besar berubah" if r["tiga_besar_berubah"] else "tidak"
        md.append(f"| `{r['parameter']}` {r['sisi']} | {r['nilai']:g} ({r['nominal']:g}) | {r['baseline']:.0f} | {r['terbaik']} | "
                  f"{r['efek_terbaik_persen']:+.0f}% | {r['urutan']} | {flag} |")
    md += ["", "Catatan: rentang parameter adalah perkiraan sebelum kalibrasi; `skala_sinyal` menggeser kekuatan sinyal "
               "semua kampung tanpa mengubah kepemilikan HP. Jaringan sosial dan bobot kepercayaan tidak ikut digeser.", ""]
    return "\n".join(md)


# --------------------------------------------------------------------------------------------- Sobol
def sobol_indeks(fA: np.ndarray, fB: np.ndarray, fAB: np.ndarray, n_boot: int = 500,
                 rng: np.random.Generator | None = None) -> dict:
    """Indeks Sobol dari rancangan Saltelli. fA, fB: (N,), fAB: (d, N) dengan kolom i dari B.

    S1 memakai penduga Saltelli (2010), ST memakai penduga Jansen. IK 95% dari bootstrap atas baris.
    """
    rng = rng or np.random.default_rng(0)

    def hitung(idx):
        a, b, ab = fA[idx], fB[idx], fAB[:, idx]
        mu = np.concatenate([a, b]).mean()          # pusatkan: tak mengubah nilai harapan, mengecilkan varians penduga
        a, b, ab = a - mu, b - mu, ab - mu
        var = np.var(np.concatenate([a, b]), ddof=1)
        if not var > 0:
            z = np.full(len(fAB), np.nan)
            return z, z
        return (b * (ab - a)).mean(1) / var, 0.5 * ((a - ab) ** 2).mean(1) / var

    N = len(fA)
    s1, st = hitung(np.arange(N))
    boot = [hitung(rng.integers(0, N, N)) for _ in range(n_boot)]
    b1, bt = np.array([b[0] for b in boot]), np.array([b[1] for b in boot])
    return {"S1": s1, "S1_lo": np.nanpercentile(b1, 2.5, 0), "S1_hi": np.nanpercentile(b1, 97.5, 0),
            "ST": st, "ST_lo": np.nanpercentile(bt, 2.5, 0), "ST_hi": np.nanpercentile(bt, 97.5, 0),
            "varians": float(np.var(np.concatenate([fA, fB]), ddof=1))}


def run_sobol(n: int = 20_000, seed_dunia: int = 42, n_dasar: int = 64, kode: str = "AE",
              scenario: dict | None = None, mode: str = "abm", beku: str | None = None, workers: int | None = None,
              nama: str | None = None, seed_per_konfig: int = 1, n_boot: int = 500, rentang: dict | None = None,
              progress=print) -> Path:
    """Indeks Sobol untuk beban siaga baseline A dan efek (%) tiap intervensi lain terhadap A.

    Rancangan Saltelli: blok A, B, dan AB_i (N baris masing-masing), ditambah blok B ulang dengan seed lain
    untuk mengukur derau antar-seed di seluruh ruang parameter. Biaya: N·(d + 3) konfigurasi ×
    `seed_per_konfig` × jumlah intervensi.

    Seed: tiap konfigurasi memakai seed sendiri (independen antar-konfigurasi), sehingga derau menambah ST
    tepat sebesar lantai derau σ²/V dan dapat dikurangkan, sedangkan S1 tidak bias. Di dalam satu konfigurasi
    semua intervensi memakai seed yang sama (bilangan acak bersama), jadi efek % terhadap A tetap berpasangan.
    """
    scenario = scenario or load_scenario()
    interventions, params = load_interventions(), load_params()
    rentang = rentang or load_yaml(CONFIG_DIR / "sensitivitas.yaml")["rentang"]
    kode = "A" + kode.replace("A", "")
    keys, d, k_seed = list(rentang), len(rentang), max(1, int(seed_per_konfig))
    if mode != "abm" and not beku:
        raise ValueError("Sensitivitas dengan LLM wajib memakai tabel beku (--beku).")
    m = int(np.ceil(np.log2(max(2, n_dasar))))
    base = qmc.Sobol(2 * d, scramble=True, seed=seed_dunia).random_base2(m)
    N = len(base)
    lo, hi = np.array([rentang[k][0] for k in keys], float), np.array([rentang[k][1] for k in keys], float)
    mA, mB = qmc.scale(base[:, :d], lo, hi), qmc.scale(base[:, d:], lo, hi)
    blok = [mA, mB]
    for i in range(d):
        ab = mA.copy()
        ab[:, i] = mB[:, i]
        blok.append(ab)
    blok.append(mB)                                   # B ulang, seed lain → lantai derau
    configs = [(b * N + j, {k: round(float(v), 5) for k, v in zip(keys, X[j])})
               for b, X in enumerate(blok) for j in range(N)]
    build_world(n, seed_dunia, params=params)

    jobs = [(ci, ubah, code, ci * k_seed + r) for ci, ubah in configs for r in range(k_seed) for code in kode]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    progress = progress or (lambda s: None)
    progress(f"Sobol: N={N} × (d={d} + 3) = {len(configs)} konfigurasi × {k_seed} seed × "
             f"{len(kode)} intervensi = {len(jobs)} putaran")
    rows, t0, done = [], time.perf_counter(), 0
    init = (n, seed_dunia, params, scenario, interventions, mode, beku)
    if workers == 1:
        _init(*init)
        it = (_one(*j) for j in jobs)
    else:
        ex = ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=init)
        it = (f.result() for f in as_completed([ex.submit(_one, *j) for j in jobs]))
    for r in it:
        rows += r
        done += 1
        if done % max(1, len(jobs) // 10) == 0 or done == len(jobs):
            progress(f"  {done}/{len(jobs)} putaran selesai ({time.perf_counter() - t0:.0f} dtk)")
    if workers != 1:
        ex.shutdown()

    df = pd.DataFrame(rows)
    v = (df[df["tipe"] == "semua"].groupby(["konfig", "intervensi"])[METRIK_UTAMA].mean()
         .unstack("intervensi").sort_index())
    target = {"beban_siaga_baseline_A": v["A"]}
    for c in kode[1:]:
        target[f"efek_{c}_persen"] = 100 * (v[c] - v["A"]) / v["A"].where(v["A"] > 0)
    rng = np.random.default_rng(seed_dunia)
    c_ulang = N * (d + 2)
    hasil_t = {}
    for t, y in target.items():
        y = y.to_numpy(float)
        fA, fB = y[:N], y[N:2 * N]
        fAB = y[2 * N:c_ulang].reshape(d, N)
        fB2 = y[c_ulang:c_ulang + N]
        ok = ~(np.isnan(fA) | np.isnan(fB) | np.isnan(fB2) | np.isnan(fAB).any(0))
        ind = sobol_indeks(fA[ok], fB[ok], fAB[:, ok], n_boot, rng)
        V = ind["varians"]
        sigma2 = float(0.5 * np.mean((fB[ok] - fB2[ok]) ** 2))        # derau rata-rata di seluruh ruang parameter
        lantai = sigma2 / V if V > 0 else float("nan")
        par = {}
        for i, k in enumerate(keys):
            q = {s: round(float(ind[s][i]), 3) for s in ("S1", "S1_lo", "S1_hi", "ST", "ST_lo", "ST_hi")}
            q["ST_bersih"] = round(max(0.0, float(ind["ST"][i]) - lantai), 3)
            q["berpengaruh"] = bool(ind["ST_lo"][i] > lantai)
            par[k] = q
        hasil_t[t] = {"varians_total": round(V, 4), "varians_derau": round(sigma2, 4), "lantai_derau": round(lantai, 3),
                      "jumlah_S1": round(float(np.nansum(ind["S1"])), 3),
                      "jumlah_ST_bersih": round(float(sum(q["ST_bersih"] for q in par.values())), 3),
                      "parameter": par}
    hasil = {"metode": "Sobol (rancangan Saltelli; S1 Saltelli 2010, ST Jansen dikurangi lantai derau; IK bootstrap 95%)",
             "metrik": METRIK_UTAMA, "wilayah": "semua", "N": N, "parameter_dihitung": d,
             "rentang": {k: list(map(float, rentang[k])) for k in keys}, "target": hasil_t,
             "pengaturan": {"agen": n, "seed_dunia": seed_dunia, "intervensi": kode, "seed_per_konfig": k_seed,
                            "skenario": scenario["nama"], "mode_llm": mode, "tabel_beku": beku,
                            "versi_kode": code_version(), "detik": round(time.perf_counter() - t0, 1)}}
    out = OUT_DIR / (nama or f"sobol_{scenario.get('kode', 'skenario')}_{n}")
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "hasil.parquet", index=False)
    (out / "sobol.json").write_text(json.dumps(hasil, ensure_ascii=False, indent=1), encoding="utf-8")
    md = sobol_markdown(hasil, {c: interventions[c]["nama"] for c in kode})
    (out / "sobol.md").write_text(md, encoding="utf-8")
    from .report import html_page, md_to_html

    (out / "sobol.html").write_text(html_page(md_to_html(md), "Indeks Sobol"), encoding="utf-8")
    return out


def sobol_markdown(h: dict, names: dict[str, str]) -> str:
    st = h["pengaturan"]
    agen = f"{st['agen']:,}".replace(",", ".")
    md = ["## Indeks Sobol: parameter mana yang menggerakkan hasil", "",
          f"Dekomposisi varians atas {h['parameter_dihitung']} parameter di `config/sensitivitas.yaml` "
          f"(N = {h['N']}, {st.get('seed_per_konfig', 1)} seed per konfigurasi, {agen} agen; metrik: beban siaga aktif "
          "per 1.000 penduduk, semua wilayah). **S1** = porsi varians dari parameter itu sendiri; **ST** = termasuk "
          "interaksinya. Sebagian varians berasal dari keacakan simulasi (lantai derau) dan menggelembungkan setiap ST "
          "sebesar lantai itu; kolom **ST bersih** sudah dikurangi. Parameter **berpengaruh** bila batas bawah IK ST-nya "
          "di atas lantai derau.", ""]
    for t, v in h["target"].items():
        kode = t.split("_")[1] if t.startswith("efek_") else "A"
        judul = ("Beban siaga tanpa intervensi (A)" if t.startswith("beban") else
                 f"Efek {kode} — {names.get(kode, '')} terhadap A (%)")
        md += [f"### {judul}", "",
               f"- Lantai derau ≈ **{v['lantai_derau']:.0%}** dari varians total (keacakan simulasi, rata-rata di seluruh "
               "ruang parameter)."]
        if v["lantai_derau"] > 0.25:
            md.append("- **Derau tinggi**: indeks untuk target ini belum bisa dibedakan dengan baik dari keacakan "
                      "simulasi. Naikkan `--seed-per-konfig` (derau turun sebanding 1/k) atau jumlah agen.")
        md += [f"- Jumlah S1 = {v['jumlah_S1']:.2f}; jumlah ST bersih = {v['jumlah_ST_bersih']:.2f}. Jumlah S1 mendekati "
               "1 − lantai berarti efek hampir aditif; jumlah ST bersih jauh di atas jumlah S1 berarti interaksi penting.",
               "", "| Parameter | S1 [IK 95%] | ST [IK 95%] | ST bersih | Berpengaruh? |", "|---|---|---|---|---|"]
        for k, q in sorted(v["parameter"].items(), key=lambda kv: -kv[1]["ST"]):
            md.append(f"| `{k}` | {q['S1']:.2f} [{q['S1_lo']:.2f}, {q['S1_hi']:.2f}] | "
                      f"{q['ST']:.2f} [{q['ST_lo']:.2f}, {q['ST_hi']:.2f}] | {q['ST_bersih']:.2f} | "
                      f"{'ya' if q['berpengaruh'] else 'tidak'} |")
        md.append("")
    md += [f"Catatan: N = {h['N']} memberi IK yang lebar; indeks negatif kecil adalah galat penduga. "
           "Parameter yang tidak dipakai intervensi mana pun di sini (mis. `beta.radio` tanpa F) menjadi pembanding: "
           "ST-nya hanya derau, jadi parameter lain dengan ST setara itu praktis tidak berpengaruh. "
           "Rentang parameter masih perkiraan sebelum kalibrasi, jadi urutan kepentingan ikut bergantung padanya.", ""]
    return "\n".join(md)
