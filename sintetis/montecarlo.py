"""FR-09 Runner Monte Carlo paralel per skenario.

Setiap putaran memakai dunia yang sama (seed dunia tetap) dan seed dinamika berbeda. Intervensi
dibandingkan dengan bilangan acak bersama (seed yang sama di semua intervensi), sehingga selisih
terhadap baseline bisa diuji berpasangan. Agen kunci TIDAK memanggil API selama Monte Carlo:
keputusan diambil dari tabel beku (FR-08) atau aturan ABM.

F1: selain per tipe wilayah (`hasil.parquet`), metrik juga dihitung per kabupaten (`hasil_kabupaten.parquet`,
kolom `tipe` = id kabupaten) dan per distrik (`distrik.parquet`: peluang memanas dan jam sampai memanas).
`kurva_persentil.parquet` menyimpan persentil 10/50/90 antar-seed untuk penyebar dan siaga aktif per jam.

Dengan `posterior` (hasil `kalibrasi --metode abc`), tiap seed dinamika juga memakai satu set parameter
yang diundi dari posterior. Set itu sama untuk semua intervensi pada seed yang sama, jadi uji berpasangan
tetap sah, dan sebaran antar-putaran memuat ketidakpastian parameter selain keacakan dinamika.
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

from . import KANAL, A, I, S, TIPE_WILAYAH
from .config import OUT_DIR, _set_dotted, code_version, load_interventions, load_params, load_scenario, versi_model
from .engine import Simulation
from .llm.orchestrator import make_decider
from .world import build_world

_W = None
_CTX: dict = {}


def _init(n, seed_dunia, params, scenario, interventions, mode, beku, post=None):
    global _W, _CTX
    _W = build_world(n, seed_dunia, params=params)
    _CTX = {"sc": scenario, "iv": interventions, "mode": mode, "beku": beku, "post": post, "base": params}


def _one(code: str, seed: int) -> tuple[list[dict], np.ndarray, dict, dict]:
    sc, iv = _CTX["sc"], _CTX["iv"]
    if _CTX.get("post"):                      # set parameter posterior untuk seed ini (sama di semua intervensi)
        params = copy.deepcopy(_CTX["base"])
        for k, v in _CTX["post"][seed].items():
            _set_dotted(params, k, v)
        _W.params = params
    decider = make_decider(_CTX["mode"], _CTX["beku"], sc)
    sim = Simulation(_W, sc, iv[code], seed=seed, decider=decider, log_events=False, kode_intervensi=code)
    res = sim.run()
    return (run_metrics(sim, res, code, seed, per_kabupaten=True), tipe_series(sim, res), decider.stats()["hitung"],
            distrik_metrics(sim, res))


def distrik_metrics(sim: Simulation, res) -> dict:
    """F1: per distrik, jam sejak isu sampai memanas (NaN bila tidak) dan beban siaga per 1.000."""
    esk = res.eskalasi_step
    return {"jam": np.where(esk >= 0, esk * sim.dt_h, np.nan).astype(np.float32),
            "siaga": (res.timeseries[:, :, A].sum(0) * sim.dt_h / sim.dpop * 1000).astype(np.float32)}


def run_metrics(sim: Simulation, res, code: str, seed: int, per_kabupaten: bool = False) -> list[dict]:
    """Metrik per tipe wilayah (FR-10) + 'semua'; dengan `per_kabupaten`, juga satu baris per kabupaten
    (kolom `tipe` = id kabupaten, `tingkat` = 'kabupaten'). Paparan per kanal hanya tersedia per tipe."""
    geo, pop = sim.w.geo, sim.w.pop
    ts, risk, esk = res.timeseries, res.risk, res.eskalasi_step
    rows = []
    groups = [(t, geo.distrik_tipe == i, i) for i, t in enumerate(TIPE_WILAYAH)] + [("semua", np.ones(sim.D, bool), len(TIPE_WILAYAH))]
    if per_kabupaten:
        groups += [(k, geo.distrik_kab == j, None) for j, k in enumerate(geo.kab_id)]
    dengar_d = np.bincount(pop.distrik, weights=sim.pernah_dengar, minlength=sim.D)
    km = res.kanal_masuk
    pp = res.paparan_provokator
    for tipe, dm, ti in groups:
        if not dm.any():
            continue
        if ti is None:
            porsi = np.full(4, np.nan)
        else:
            papar = (km[0, ti] if ti < len(TIPE_WILAYAH) else km[0].sum(0)).astype(float)
            porsi = papar[:4] / max(papar[:4].sum(), 1)       # WA, FB, tatap muka, radio
        popn = sim.dpop[dm].sum()
        s = ts[:, dm, :].sum(1)
        e = esk[dm]
        first = e[e >= 0].min() * sim.dt_h if (e >= 0).any() else np.nan
        rows.append({
            "intervensi": code, "seed": seed, "tipe": tipe, "tingkat": "tipe" if ti is not None else "kabupaten",
            "penduduk_agen": int(popn),
            "jangkauan_hoaks": float(dengar_d[dm].sum() / popn),
            "puncak_penyebar": float(s[:, I].max() / popn),
            "puncak_siaga": float(s[:, A].max() / popn),
            "siaga_jam_per_1000": float(s[:, A].sum() * sim.dt_h / popn * 1000),
            "akhir_siaga": float(s[-1, A] / popn),
            "jam_ke_eskalasi": float(first),
            "porsi_distrik_eskalasi": float((e >= 0).mean()),
            "risiko_maks": float(risk[:, dm].max()),
            "belum_dengar_akhir": float(s[-1, S] / popn),
            "jam_puncak_penyebar": float(s[:, I].argmax() * sim.dt_h),
            "jam_puncak_siaga": float(s[:, A].argmax() * sim.dt_h),
            **{f"paparan_{k}": float(v) for k, v in zip(KANAL[:4], porsi)},
            # porsi semua paparan hoaks (S→E) yang datang langsung dari buzzer/provokator lapangan
            "paparan_provokator": (float((pp[ti] if ti < len(TIPE_WILAYAH) else pp.sum())
                                         / max((km[0, ti] if ti < len(TIPE_WILAYAH) else km[0]).sum(), 1))
                                   if pp is not None and ti is not None else 0.0 if ti is not None else np.nan),
        })
    return rows


def tipe_series(sim: Simulation, res) -> np.ndarray:
    """(T+1, 5 tipe, 5 status) proporsi per tipe wilayah (indeks terakhir = semua)."""
    geo = sim.w.geo
    ts = res.timeseries.astype(np.float64)
    out = np.zeros((ts.shape[0], len(TIPE_WILAYAH) + 1, 5))
    for i in range(len(TIPE_WILAYAH)):
        dm = geo.distrik_tipe == i
        if dm.any():
            out[:, i] = ts[:, dm].sum(1) / sim.dpop[dm].sum()
    out[:, -1] = ts.sum(1) / sim.dpop.sum()
    return out


def run_montecarlo(n: int = 100_000, seed_dunia: int = 42, putaran: int = 200, kode: str | list[str] = "ABCDEF",
                   scenario: dict | None = None, interventions: dict | None = None, params: dict | None = None,
                   mode: str = "abm", beku: str | None = None, workers: int | None = None,
                   nama: str | None = None, progress=print, posterior: str | Path | None = None,
                   seeds: list[int] | None = None, out_dir: str | Path | None = None,
                   pakai_kalibrasi_aktif: bool = True) -> Path:
    """`kode`: string satu huruf per intervensi ("ABCD") atau daftar kode (mis. ["A", "D@t3"]).
    `seeds`: daftar seed dinamika (bawaan 0..putaran-1), dipakai perencana untuk menambah seed bertahap."""
    kode = list(kode)
    params_diberikan = params is not None      # params eksplisit (uji ketahanan/sensitivitas) tidak ditimpa posterior aktif
    seeds = list(range(putaran)) if seeds is None else [int(x) for x in seeds]
    putaran = len(seeds)
    scenario = scenario or load_scenario()
    interventions = interventions or load_interventions()
    params = params or load_params()
    if mode != "abm" and not beku:
        raise ValueError("Monte Carlo dengan LLM wajib memakai tabel beku (--beku). Jalankan 'bekukan' dulu.")
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    w_main = build_world(n, seed_dunia, params=params)        # bangun & simpan cache sekali sebelum worker
    post, post_info = None, None
    if posterior is None and pakai_kalibrasi_aktif and not params_diberikan:
        from .config import kalibrasi_aktif

        aktif = kalibrasi_aktif()           # R6: setelah kalibrasi ulang, Monte Carlo memakai posterior aktif
        if aktif and ((OUT_DIR / aktif / "posterior.json").exists() or Path(aktif).exists()):
            posterior = OUT_DIR / aktif if (OUT_DIR / aktif).exists() else aktif
    if posterior:
        from .calibration import load_posterior

        h = load_posterior(posterior)
        pilih = np.random.default_rng([seed_dunia, 7]).integers(0, len(h["set_parameter"]), max(seeds) + 1)
        post = {s: h["set_parameter"][pilih[s]] for s in seeds}
        pilih = pilih[seeds]
        post_info = {"nama": Path(posterior).name, "kasus": h.get("kasus"), "set_tersedia": len(h["set_parameter"]),
                     "set_terpakai": int(len(set(pilih.tolist()))), "indeks_per_seed": pilih.tolist()}
    nama = nama or f"mc_{scenario.get('kode', 'skenario')}_{n}_{putaran}x" + ("_posterior" if post else "")
    out = Path(out_dir) if out_dir else OUT_DIR / nama
    out.mkdir(parents=True, exist_ok=True)

    jobs = [(c, s) for s in seeds for c in kode]
    rows, series, hitung, dist = [], {c: [] for c in kode}, {}, {c: [] for c in kode}
    t0 = time.perf_counter()
    init = (n, seed_dunia, params, scenario, interventions, mode, beku, post)
    if workers == 1:
        _init(*init)
        results = (((c, s), _one(c, s)) for c, s in jobs)
        iterator = results
    else:
        ex = ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=init)
        futs = {ex.submit(_one, c, s): (c, s) for c, s in jobs}
        iterator = ((futs[f], f.result()) for f in as_completed(futs))
    done = 0
    for (c, s), (m, ser, h, dm) in iterator:
        rows += m
        series[c].append(ser)
        dist[c].append((s, dm))
        for k, v in h.items():
            hitung[k] = hitung.get(k, 0) + v
        done += 1
        if progress and (done % max(1, len(jobs) // 20) == 0 or done == len(jobs)):
            progress(f"  {done}/{len(jobs)} putaran selesai ({time.perf_counter() - t0:.0f} dtk)")
    if workers != 1:
        ex.shutdown()

    df = pd.DataFrame(rows).sort_values(["intervensi", "seed", "tipe"])
    kab = df["tingkat"] == "kabupaten"
    df[~kab].drop(columns="tingkat").to_parquet(out / "hasil.parquet", index=False)
    df[kab].drop(columns=["tingkat", *[c for c in df if c.startswith("paparan_")]]).to_parquet(
        out / "hasil_kabupaten.parquet", index=False)
    mean_series = {c: np.mean(v, 0).tolist() for c, v in series.items()}
    (out / "kurva_rata2.json").write_text(json.dumps(mean_series), encoding="utf-8")
    simpan_distrik(dist, out, w_main.geo)
    simpan_kurva_persentil(series, out)
    manifest = {
        "agen": n, "seed_dunia": seed_dunia, "putaran": putaran,
        "intervensi": {c: interventions[c]["nama"] for c in kode}, "versi_model": versi_model(post_info and post_info["nama"]),
        "skenario": scenario["nama"], "mode_llm": mode, "tabel_beku": beku, "versi_kode": code_version(),
        "keputusan_agen_kunci": hitung, "detik": round(time.perf_counter() - t0, 1), "workers": workers,
        "seed": (f"0..{putaran - 1}" if seeds == list(range(putaran)) else ",".join(map(str, seeds)))
                + " (sama untuk semua intervensi)", "daftar_seed": seeds, "isi_skenario": scenario,
        "posterior": post_info,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def simpan_distrik(dist: dict, out: Path, geo=None) -> pd.DataFrame:
    """distrik.parquet: per intervensi × distrik, peluang memanas dan jam sampai memanas (persentil antar-seed)."""
    import warnings

    rows = []
    for c, lst in dist.items():
        if not lst:
            continue
        jam = np.vstack([d["jam"] for _, d in lst])          # (seed, D)
        siaga = np.vstack([d["siaga"] for _, d in lst])
        ada = ~np.isnan(jam)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            q = np.nanpercentile(jam, [10, 50, 90], axis=0)
        for d in range(jam.shape[1]):
            rows.append({"intervensi": c, "distrik": d, "peluang_memanas": float(ada[:, d].mean()),
                         "jam_p10": float(q[0, d]), "jam_median": float(q[1, d]), "jam_p90": float(q[2, d]),
                         "siaga_jam_per_1000": float(siaga[:, d].mean()),
                         "siaga_p10": float(np.percentile(siaga[:, d], 10)), "siaga_p90": float(np.percentile(siaga[:, d], 90))})
    df = pd.DataFrame(rows)
    if geo is not None and len(df):
        df["nama"] = [geo.distrik_nama[d] for d in df["distrik"]]
        df["kabupaten"] = [geo.kab_id[geo.distrik_kab[d]] for d in df["distrik"]]
        df["tipe"] = [TIPE_WILAYAH[geo.distrik_tipe[d]] for d in df["distrik"]]
    df.to_parquet(out / "distrik.parquet", index=False)
    return df


def simpan_kurva_persentil(series: dict, out: Path, status=(I, A)) -> pd.DataFrame:
    """kurva_persentil.parquet: persentil 10/50/90 antar-seed proporsi penyebar (I) dan siaga (A) per langkah."""
    nama_w = [*TIPE_WILAYAH, "semua"]
    rows = []
    for c, v in series.items():
        if not v:
            continue
        X = np.asarray(v)                                     # (seed, T+1, 5 wilayah, 5 status)
        q = np.percentile(X, [10, 50, 90], axis=0)
        for wi, wn in enumerate(nama_w):
            if not X[:, :, wi].any():
                continue
            for st in status:
                rows.append(pd.DataFrame({"intervensi": c, "wilayah": wn, "status": "SEIZA"[st],
                                          "langkah": np.arange(X.shape[1]), "p10": q[0, :, wi, st],
                                          "p50": q[1, :, wi, st], "p90": q[2, :, wi, st]}))
    df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    df.to_parquet(out / "kurva_persentil.parquet", index=False)
    return df
