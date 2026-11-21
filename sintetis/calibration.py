"""FR-11 Kalibrasi pola terhadap linimasa kasus rujukan.

Dua metode, keduanya memakai statistik ringkasan dan galat log kuadrat yang sama:
  - grid (`calibrate`): pencarian grid atas beberapa parameter di `kalibrasi_rujukan.yaml`. Hasilnya satu
    titik terbaik.
  - ABC (`calibrate_abc`): Approximate Bayesian Computation dengan penolakan. Semua parameter di
    `config/sensitivitas.yaml` diambil dari prior seragam (Latin hypercube), disimulasikan, lalu porsi
    sampel yang paling dekat dengan target disimpan sebagai sampel posterior. Hasilnya SEBARAN parameter
    yang cocok dengan rujukan, yang bisa dipakai Monte Carlo (`montecarlo --posterior`) supaya interval
    kepercayaan ikut memuat ketidakpastian parameter.

R6: rujukan boleh berisi BANYAK kasus (`kasus:` berupa daftar, atau kasus dari kajian pasca-kejadian lewat
`kalibrasi --kasus kajian/`). Tiap kasus punya skenario, tindakan nyata, wilayah inti (tipe wilayah atau id
kabupaten), target, dan `bobot_kasus` (keyakinan linimasa). Galat gabungan = jumlah galat per kasus × bobot.
Format lama satu kasus (kunci `target` di tingkat atas) tetap berlaku.
"""
from __future__ import annotations

import copy
import itertools
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import qmc

from . import TIPE_WILAYAH
from .config import CONFIG_DIR, OUT_DIR, _set_dotted, code_version, load_interventions, load_params, load_scenario, load_yaml
from .engine import Simulation
from .world import build_world

_W = None
_CTX: dict = {}


def _statistik(world, sc: dict, iv: dict, dm: np.ndarray, putaran: int) -> dict:
    """Statistik ringkasan di wilayah inti, rata-rata antar-seed (seed 0..putaran-1 untuk semua konfigurasi)."""
    esk, reach = [], []
    for s in range(putaran):
        sim = Simulation(world, sc, iv, seed=s, log_events=False)
        t24 = sim.step_of_hour(24)
        for _ in range(sim.T):
            sim.step()
            if sim.t == t24:
                heard = np.bincount(world.pop.distrik, weights=sim.pernah_dengar, minlength=sim.D)
                reach.append(heard[dm].sum() / sim.dpop[dm].sum())
        e = sim.eskalasi_step[dm]
        esk.append(e[e >= 0].min() * sim.dt_h if (e >= 0).any() else sim.T * sim.dt_h * 1.5)
    return {"jam_ke_eskalasi": float(np.mean(esk)), "jangkauan_24_jam": float(np.mean(reach))}


def muat_rujukan(rujukan: dict | list | None = None) -> list[dict]:
    """Normalisasi rujukan menjadi daftar kasus."""
    ref = rujukan if rujukan is not None else load_yaml(CONFIG_DIR / "kalibrasi_rujukan.yaml")
    if isinstance(ref, list):
        cases = ref
    elif isinstance(ref.get("kasus"), list):
        cases = ref["kasus"]
    else:
        cases = [ref]
    out = []
    for i, c in enumerate(cases):
        out.append({"kasus": str(c.get("kasus") or f"kasus{i + 1}"), "target": dict(c["target"]),
                    "bobot": dict(c.get("bobot") or {}), "bobot_kasus": float(c.get("bobot_kasus", 1.0)),
                    "wilayah_inti": c.get("wilayah_inti", "kota"), "skenario": c.get("skenario"),
                    "intervensi": c.get("intervensi")})
    return out


def _ratakan(cases: list[dict]) -> dict:
    """Satu rujukan datar {target, bobot}: satu kasus → nama statistik apa adanya; banyak → '<kasus>:<statistik>'."""
    if len(cases) == 1:
        c = cases[0]
        return {"target": c["target"], "bobot": {k: c["bobot"].get(k, 1.0) * c["bobot_kasus"] for k in c["target"]},
                "kasus": c["kasus"]}
    tgt, bob = {}, {}
    for c in cases:
        for k, v in c["target"].items():
            tgt[f"{c['kasus']}:{k}"] = v
            bob[f"{c['kasus']}:{k}"] = c["bobot"].get(k, 1.0) * c["bobot_kasus"]
    return {"target": tgt, "bobot": bob, "kasus": ", ".join(c["kasus"] for c in cases)}


def _siapkan(world, cases: list[dict], scenario: dict | None = None) -> list[tuple]:
    out = []
    for c in cases:
        sc = c["skenario"]
        sc = sc if isinstance(sc, dict) else load_scenario(sc) if sc else (scenario or load_scenario())
        iv = c["intervensi"] or load_interventions()["A"]
        w = c["wilayah_inti"]
        dm = (world.geo.distrik_tipe == TIPE_WILAYAH.index(w)) if w in TIPE_WILAYAH else \
            (world.geo.distrik_kab == world.geo.kab_index(w))
        out.append((c, sc, iv, dm))
    return out


def _stat_semua(world, siap: list[tuple], putaran: int) -> dict:
    banyak = len(siap) > 1
    out = {}
    for c, sc, iv, dm in siap:
        v = _statistik(world, sc, iv, dm, putaran)
        for k in c["target"]:
            out[f"{c['kasus']}:{k}" if banyak else k] = v[k]
    return out


def _galat(sim_v: dict, ref: dict) -> float:
    return float(sum(ref["bobot"].get(k, 1.0) * np.log((sim_v[k] + 1e-3) / (v + 1e-3)) ** 2
                     for k, v in ref["target"].items()))


def _dengan(base: dict, ubah: dict) -> dict:
    params = copy.deepcopy(base)
    for k, v in ubah.items():
        _set_dotted(params, k, v)
    return params


def calibrate(n: int = 20_000, seed_dunia: int = 42, putaran: int = 5, rujukan: dict | None = None,
              progress=print) -> pd.DataFrame:
    raw = rujukan or load_yaml(CONFIG_DIR / "kalibrasi_rujukan.yaml")
    cases = muat_rujukan(raw)
    ref = _ratakan(cases)
    base = load_params()
    world = build_world(n, seed_dunia, params=base)
    siap = _siapkan(world, cases)
    keys = list(raw["grid"])
    rows = []
    for combo in itertools.product(*(raw["grid"][k] for k in keys)):
        ubah = dict(zip(keys, combo))
        world.params = _dengan(base, ubah)
        sim_v = _stat_semua(world, siap, putaran)
        loss = _galat(sim_v, ref)
        rows.append({**ubah, **sim_v, "galat": loss})
        progress(f"  {ubah} → {sim_v} galat {loss:.3f}")
    world.params = base
    return pd.DataFrame(rows).sort_values("galat").reset_index(drop=True)


# ---------------------------------------------------------------------------------------------- ABC
def _init(n, seed_dunia, params, cases, scenario):
    global _W, _CTX
    _W = build_world(n, seed_dunia, params=params)
    _CTX = {"base": params, "siap": _siapkan(_W, cases, scenario)}


def _satu(i: int, ubah: dict, putaran: int) -> tuple[int, dict]:
    _W.params = _dengan(_CTX["base"], ubah)
    return i, _stat_semua(_W, _CTX["siap"], putaran)


def calibrate_abc(n: int = 20_000, seed_dunia: int = 42, sampel: int = 200, putaran: int = 3, terima: float = 0.1,
                  rujukan: dict | None = None, rentang: dict | None = None, scenario: dict | None = None,
                  workers: int | None = None, nama: str | None = None, progress=print) -> Path:
    """ABC penolakan: `sampel` set parameter dari prior seragam, simpan porsi `terima` yang terdekat ke target.

    Menulis ke out/<nama>/: `sampel.parquet` (semua sampel + galat), `posterior.json` (set parameter
    yang diterima, dipakai `montecarlo --posterior`), dan `kalibrasi_abc.md` (ringkasan).
    """
    if not 0 < terima <= 1:
        raise ValueError("terima harus di antara 0 dan 1")
    cases = muat_rujukan(rujukan)
    ref = _ratakan(cases)
    rentang = rentang or load_yaml(CONFIG_DIR / "sensitivitas.yaml")["rentang"]
    sc = scenario or load_scenario()
    base = load_params()
    keys = list(rentang)
    lo, hi = np.array([rentang[k][0] for k in keys], float), np.array([rentang[k][1] for k in keys], float)
    theta = qmc.scale(qmc.LatinHypercube(len(keys), seed=seed_dunia).random(sampel), lo, hi).round(4)
    build_world(n, seed_dunia, params=base)          # bangun & simpan cache sekali sebelum worker

    progress = progress or (lambda s: None)
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    jobs = [(i, dict(zip(keys, map(float, row))), putaran) for i, row in enumerate(theta)]
    init = (n, seed_dunia, base, cases, sc)
    progress(f"ABC: {sampel} set parameter × {putaran} seed = {sampel * putaran} putaran, {len(keys)} parameter")
    t0, stat, done = time.perf_counter(), {}, 0
    if workers == 1:
        _init(*init)
        it = (_satu(*j) for j in jobs)
    else:
        ex = ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=init)
        it = (f.result() for f in as_completed([ex.submit(_satu, *j) for j in jobs]))
    for i, v in it:
        stat[i] = v
        done += 1
        if done % max(1, sampel // 10) == 0 or done == sampel:
            progress(f"  {done}/{sampel} set selesai ({time.perf_counter() - t0:.0f} dtk)")
    if workers != 1:
        ex.shutdown()

    df = pd.DataFrame(theta, columns=keys)
    for k in ref["target"]:
        df[k] = [stat[i][k] for i in range(sampel)]
    df["galat"] = [_galat(stat[i], ref) for i in range(sampel)]
    k_terima = min(sampel, max(1, int(round(terima * sampel))))
    urut = df["galat"].to_numpy().argsort(kind="stable")[:k_terima]
    df["diterima"] = False
    df.loc[urut, "diterima"] = True
    post = df.loc[urut]

    ringkas = {}
    for j, k in enumerate(keys):
        p5, p50, p95 = (float(np.percentile(post[k], q)) for q in (5, 50, 95))
        # lebar P5–P95 posterior dibanding lebar yang sama pada prior seragam (0,9 × rentang)
        sempit = 1 - (p95 - p5) / (0.9 * (hi[j] - lo[j])) if hi[j] > lo[j] else 0.0
        ringkas[k] = {"prior": [float(lo[j]), float(hi[j])], "p5": round(p5, 4), "median": round(p50, 4),
                      "p95": round(p95, 4), "penyempitan": round(float(sempit), 3)}
    # Penyempitan sebesar apa yang masih bisa muncul kebetulan bila parameter sama sekali tidak terbatasi:
    # persentil 95 dari penyempitan k_terima sampel seragam.
    u = np.random.default_rng(0).random((4000, k_terima))
    derau = float(np.percentile(1 - (np.percentile(u, 95, axis=1) - np.percentile(u, 5, axis=1)) / 0.9, 95))
    for v in ringkas.values():
        v["terbatasi"] = bool(v["penyempitan"] > derau)
    # Pemeriksaan prior-prediktif: apakah target berada di dalam sebaran simulasi seluruh sampel prior?
    cakupan = {}
    for k, v in ref["target"].items():
        mn, mx = float(df[k].min()), float(df[k].max())
        cakupan[k] = {"target": float(v), "simulasi_min": round(mn, 3), "simulasi_maks": round(mx, 3),
                      "terjangkau": bool(mn <= float(v) <= mx)}
    hasil = {
        "metode": "ABC penolakan, prior seragam (Latin hypercube)", "kasus": ref.get("kasus"),
        "target": ref["target"], "bobot": ref["bobot"],
        "wilayah_inti": ", ".join(str(c["wilayah_inti"]) for c in cases),
        "skenario": "; ".join((c["skenario"] or {}).get("nama", "?") if isinstance(c["skenario"], dict) else str(c["skenario"] or sc["nama"])
                              for c in cases),
        "jumlah_kasus": len(cases), "bobot_kasus": {c["kasus"]: c["bobot_kasus"] for c in cases}, "agen": n, "seed_dunia": seed_dunia, "sampel": sampel, "seed_per_sampel": putaran,
        "diterima": k_terima, "toleransi_galat": round(float(post["galat"].max()), 4),
        "galat_terbaik": round(float(post["galat"].min()), 4),
        "cocok_posterior": {k: {"target": float(v), "median": round(float(post[k].median()), 3),
                                "p5": round(float(np.percentile(post[k], 5)), 3),
                                "p95": round(float(np.percentile(post[k], 95)), 3)} for k, v in ref["target"].items()},
        "cakupan_prior": cakupan, "ambang_derau_penyempitan": round(derau, 3),
        "ringkasan_parameter": ringkas, "versi_kode": code_version(), "detik": round(time.perf_counter() - t0, 1),
        "set_parameter": [{k: float(r[k]) for k in keys} for _, r in post.iterrows()],
    }
    out = OUT_DIR / (nama or "posterior_abc")
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "sampel.parquet", index=False)
    (out / "posterior.json").write_text(json.dumps(hasil, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "kalibrasi_abc.md").write_text(abc_markdown(hasil), encoding="utf-8")
    return out


def abc_markdown(h: dict) -> str:
    h_agen = h["agen"]
    md = ["## Kalibrasi ABC", "",
          f"- Rujukan: {h['kasus']} · wilayah inti: {h['wilayah_inti']} · skenario simulasi: {h['skenario']}",
          f"- {h['sampel']} set parameter × {h['seed_per_sampel']} seed, {f'{h_agen:,}'.replace(',', '.')} agen; diterima "
          f"**{h['diterima']}** set terdekat (galat ≤ {h['toleransi_galat']:g}, terbaik {h['galat_terbaik']:g})"]
    luar = [k for k, v in h["cakupan_prior"].items() if not v["terjangkau"]]
    if luar:
        md += ["", "**PERINGATAN: target di luar jangkauan model.** " + "; ".join(
            f"`{k}` = {h['cakupan_prior'][k]['target']:g}, padahal seluruh sampel prior hanya menghasilkan "
            f"{h['cakupan_prior'][k]['simulasi_min']:g}–{h['cakupan_prior'][k]['simulasi_maks']:g}" for k in luar)
            + ". Tidak ada kombinasi parameter dalam rentang yang mereproduksi rujukan, jadi posterior di bawah hanya "
              "\"yang paling tidak meleset\". Periksa nilai target, rentang prior, struktur model (mis. `ambang_eskalasi`), "
              "atau kecocokan skenario dengan kasus rujukan sebelum memakai posterior ini."]
    md += ["", "### Kecocokan dengan target", "",
           "| Statistik | Target | Seluruh prior (min–maks) | Posterior (median) | Posterior P5–P95 |", "|---|---|---|---|---|"]
    md += [f"| `{k}` | {v['target']:g} | {h['cakupan_prior'][k]['simulasi_min']:g}–{h['cakupan_prior'][k]['simulasi_maks']:g} | "
           f"{v['median']:g} | {v['p5']:g}–{v['p95']:g} |" for k, v in h["cocok_posterior"].items()]
    md += ["", "### Parameter: prior → posterior", "",
           "Penyempitan = seberapa jauh rujukan mempersempit rentang parameter (0% = tidak terbatasi oleh data). "
           f"Dengan {h['diterima']} set diterima, penyempitan sampai {h['ambang_derau_penyempitan']:.0%} masih bisa muncul "
           "kebetulan; hanya yang di atasnya ditandai terbatasi.", "",
           "| Parameter | Prior | Posterior median | P5–P95 | Penyempitan | Terbatasi rujukan? |", "|---|---|---|---|---|---|"]
    for k, v in sorted(h["ringkasan_parameter"].items(), key=lambda kv: -kv[1]["penyempitan"]):
        md.append(f"| `{k}` | {v['prior'][0]:g}–{v['prior'][1]:g} | {v['median']:g} | {v['p5']:g}–{v['p95']:g} | "
                  f"{v['penyempitan']:.0%} | {'ya' if v['terbatasi'] else 'tidak'} |")
    md += ["", f"Catatan: dengan {len(h['target'])} statistik target, hanya sebagian parameter yang bisa dibatasi; "
               "parameter dengan penyempitan kecil tetap selebar prior dan ketidakpastiannya ikut terbawa ke Monte Carlo. "
               "Posterior hanya sebaik linimasa rujukannya.", ""]
    return "\n".join(md)


def load_posterior(nama: str | Path) -> dict:
    """Muat posterior ABC dari nama folder di out/, path folder, atau path posterior.json."""
    p = Path(nama)
    if not p.exists():
        p = OUT_DIR / str(nama)
    if p.is_dir():
        p = p / "posterior.json"
    if not p.exists():
        raise FileNotFoundError(f"Posterior '{nama}' tidak ditemukan. Jalankan 'kalibrasi --metode abc' dulu.")
    h = json.loads(p.read_text(encoding="utf-8"))
    if not h.get("set_parameter"):
        raise ValueError(f"{p} tidak berisi set parameter")
    return h


def aktifkan(posterior: str | Path, cases: list[dict] | None = None, config_dir: str | Path | None = None) -> Path:
    """Jadikan posterior ini kalibrasi aktif: versi model (F6) berubah, pustaka lama ditandai usang, kartu perlu
    dicetak ulang, dan Monte Carlo tanpa --posterior memakai posterior ini."""
    import yaml

    p = Path(config_dir or CONFIG_DIR) / "kalibrasi_aktif.yaml"
    isi = {"posterior": Path(posterior).name, "jumlah_kasus": len(cases or []),
           "kasus": [c["kasus"] for c in cases or []], "diaktifkan": time.strftime("%Y-%m-%d %H:%M")}
    p.write_text("# Kalibrasi aktif (R6). Hapus berkas ini untuk kembali ke parameter.yaml nominal.\n"
                 + yaml.safe_dump(isi, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p
