"""Backend dashboard: FastAPI + WebSocket yang mengalirkan langkah simulasi ke peramban.

  GET  /                      dashboard (web/index.html)
  GET  /api/meta              skenario, intervensi, status LLM & cache
  GET  /api/dunia             geografi, peta sinyal, poligon distrik, posisi sampel agen, persona
  WS   /ws/simulasi           jalankan 1 atau 2 skenario (mode banding) dan alirkan frame
  GET  /api/runs              daftar putaran tersimpan;  GET /api/runs/{nama}  replay dari log Parquet
  POST /api/skenario          simpan skenario dari editor (validasi FR-05)
  GET  /api/mc                daftar hasil Monte Carlo; GET /api/mc/{nama} ringkasan & kurva
  POST /api/mc                mulai Monte Carlo di latar belakang; GET /api/mc-status
  POST /api/ai/skenario       susun skenario dari deskripsi bebas (LLM)
  POST /api/ai/contoh-pesan   isi contoh pesan buzzer/provokator untuk skenario di editor (LLM)
  POST /api/ai/laporan        laporan naratif LLM dari putaran terakhir, log tersimpan, atau Monte Carlo
  POST /api/alur              alur otomatis sampai laporan PDF tim lapangan; GET /api/alur-status
  GET  /lapangan, /latihan    halaman tim lapangan (tablet, tanpa peta) dan latihan meja
  GET  /api/lapangan/meta     kabupaten, distrik, jenis isu, profil, skrip, status pustaka, versi model
  GET  /api/kartu/{kab}.pdf   R1 Kartu Respons;  GET /api/pustaka  R2a status pustaka
  POST /api/situasi           R2b Ruang Situasi (murni baca pustaka); POST /api/situasi/{id}/hitung, GET .../status
  POST /api/keterlambatan     R3 kurva; GET /api/keterlambatan-status, GET /api/keterlambatan/{nama}
  POST /api/perencana         R4; GET /api/perencana-status; POST /api/perencana/simpan
  WS   /ws/latihan            R5 latihan meja (fasilitator & peserta)
  GET/POST /api/kajian...     R6 kajian pasca-kejadian, analisis, PDF
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from scipy.spatial import ConvexHull

from . import STATUS, TIPE_WILAYAH
from .config import DATA_DIR, FAKTOR_IDENTITAS, OUT_DIR, ROOT, SCENARIO_DIR, load_interventions, load_params, load_scenario, validate_scenario
from .engine import Simulation
from .eventlog import load_run, save_run
from .keyagents import PERAN
from .readiness import check as kesiapan
from .report import html_page, md_to_html
from .llm.asisten import buat_contoh_pesan, ringkas_mc, ringkas_putaran, susun_skenario, tanpa_grafik, tulis_laporan
from .llm.cache import DecisionCache
from .llm.orchestrator import make_decider, resolve_api_key
from .world import build_world

WEB = ROOT / "web"
app = FastAPI(title="Indonesia Sintetis")
app.mount("/static", StaticFiles(directory=WEB), name="static")

_worlds: dict[tuple[int, int], object] = {}
_lock = threading.Lock()
_mc_state: dict = {"jalan": False, "log": [], "hasil": None}
_alur_state: dict = {"jalan": False, "log": [], "hasil": None, "tahap": [], "error": None}
_last_run: dict = {}       # fakta ringkas putaran dashboard terakhir, untuk laporan AI
VIZ_SAMPLE = 30_000


def get_world(n: int, seed: int):
    with _lock:
        key = (n, seed)
        if key not in _worlds:
            if len(_worlds) >= 3:
                _worlds.pop(next(iter(_worlds)))
            _worlds[key] = build_world(n, seed, params=load_params())
        return _worlds[key]


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


def _scenarios() -> list[dict]:
    out = []
    for p in sorted(SCENARIO_DIR.glob("*.yaml")):
        if p.name == "intervensi.yaml":
            continue
        try:
            sc = load_scenario(p)
            out.append({"berkas": p.name, "nama": sc["nama"], "isi": sc})
        except Exception as e:  # skenario rusak tetap ditampilkan agar bisa diperbaiki
            out.append({"berkas": p.name, "nama": f"(tidak valid) {p.name}", "error": str(e)})
    return out


@app.get("/api/meta")
def meta():
    iv = load_interventions()
    return {
        "skenario": _scenarios(),
        "intervensi": {k: v["nama"] for k, v in iv.items()},
        "llm": {"deepseek_kunci": bool(resolve_api_key()), "sumopod_kunci": bool(resolve_api_key("SUMOPOD_API_KEY")), "cache": DecisionCache().stats()},
        "tipe_wilayah": TIPE_WILAYAH, "status": STATUS,
        "ambang_eskalasi": float(load_params()["ambang_eskalasi"]),
        "faktor_identitas": FAKTOR_IDENTITAS, "kesiapan": kesiapan(),
    }


@app.get("/api/dunia")
def dunia(agen: int = 100_000, seed: int = 42):
    w = get_world(agen, seed)
    geo, pop, key = w.geo, w.pop, w.key
    V = len(geo.kampung_nama)
    kam_pop = np.bincount(pop.kampung, minlength=V)
    d_pop = np.bincount(pop.distrik, minlength=len(geo.distrik_nama))
    sim = Simulation(w, load_scenario(), None, seed=1, log_events=False, viz_sample=_viz_n(agen))
    idx = sim.viz_idx
    rng = np.random.default_rng([seed, 31])
    rad = 0.003 + 0.00045 * np.sqrt(kam_pop[pop.kampung[idx]])
    ang = rng.random(len(idx)) * 2 * np.pi
    rr = rad * np.sqrt(rng.random(len(idx)))
    ll = geo.kampung_lonlat[pop.kampung[idx]]
    pos = np.c_[ll[:, 0] + rr * np.cos(ang), ll[:, 1] + rr * np.sin(ang)]
    key_of = {int(a): i for i, a in enumerate(key.agen)}
    stations = []
    for st in w.region.get("radio", []):
        pts = np.array([geo.kab_pusat[geo.kab_index(k)] for k in st["jangkauan"]])
        stations.append({"id": st["id"], "nama": st["nama"], "pos": pts[0].tolist(),
                         "jangkauan": st["jangkauan"]})
    batas = _batas_distrik() if geo.sumber.startswith("data/") else {}
    return JSONResponse({
        "ringkasan": w.summary(),
        "kabupaten": [{"id": geo.kab_id[k], "nama": geo.kab_nama[k], "pusat": geo.kab_pusat[k].tolist(),
                       "penduduk": int((pop.kab == k).sum())} for k in range(len(geo.kab_id))],
        "distrik": [{"nama": geo.distrik_nama[d], "kab": int(geo.distrik_kab[d]), "tipe": TIPE_WILAYAH[geo.distrik_tipe[d]],
                     "pusat": np.round(geo.distrik_lonlat[d], 4).tolist(), "penduduk": int(d_pop[d]),
                     "poligon": batas.get((geo.kab_id[geo.distrik_kab[d]], geo.distrik_nama[d].lower()))
                     or [[_hull(geo.kampung_lonlat[geo.kampung_distrik == d])]]}
                    for d in range(len(geo.distrik_nama))],
        "batas_kabupaten": _batas_kab(),
        "kampung": {"lonlat": np.round(geo.kampung_lonlat, 4).tolist(), "distrik": geo.kampung_distrik.tolist(),
                    "sinyal": geo.kampung_sinyal.tolist(), "penduduk": kam_pop.tolist(), "nama": geo.kampung_nama},
        "sampel": {"pos": np.round(pos, 5).tolist(), "kampung": pop.kampung[idx].tolist(),
                   "kunci": [key_of.get(int(a), -1) for a in idx], "umur": pop.umur[idx].tolist(),
                   "hp": pop.hp[idx].astype(int).tolist()},
        "persona": key.persona, "radio": stations, "sumber_geografi": geo.sumber,
    })


def _viz_n(n: int) -> int:
    return min(n, VIZ_SAMPLE if n <= 200_000 else 40_000)


@lru_cache(maxsize=4)
def _geojson(nama: str, mtime: float) -> list:
    return json.loads((DATA_DIR / nama).read_text(encoding="utf-8"))["features"]


def _fitur(nama: str) -> list:
    p = DATA_DIR / nama
    return _geojson(nama, p.stat().st_mtime) if p.exists() else []


def _batas_distrik() -> dict:
    """Poligon distrik resmi (data/batas_distrik.geojson dari perintah 'geodata'), kunci (kab_id, nama huruf kecil)."""
    return {(f["properties"]["kabupaten_id"], f["properties"]["nama"].lower()): f["geometry"]["coordinates"]
            for f in _fitur("batas_distrik.geojson")}


def _batas_kab() -> list:
    return [{"id": f["properties"]["kabupaten_id"], "nama": f["properties"]["nama"], "poligon": f["geometry"]["coordinates"]}
            for f in _fitur("batas_kabupaten.geojson")]


def _hull(pts: np.ndarray) -> list:
    """Poligon distrik perkiraan: selubung cembung kampung, diperlebar sedikit.
    Ganti dengan batas resmi (GeoJSON BPS) bila tersedia."""
    c = pts.mean(0)
    if len(pts) >= 3:
        try:
            h = pts[ConvexHull(pts).vertices]
        except Exception:
            h = None
    else:
        h = None
    if h is None:
        ang = np.linspace(0, 2 * np.pi, 9)[:-1]
        h = c + 0.03 * np.c_[np.cos(ang), np.sin(ang)]
    h = c + (h - c) * 1.15 + 0.012 * (h - c) / np.maximum(np.linalg.norm(h - c, axis=1, keepdims=True), 1e-9)
    return np.round(np.vstack([h, h[:1]]), 4).tolist()


class _Diff:
    """Kirim hanya perubahan status sampel antar-frame agar ringan."""

    def __init__(self):
        self.prev = None

    def __call__(self, fr: dict) -> dict:
        cur = np.asarray(fr.pop("sampel"), np.int8)
        if self.prev is None:
            fr["sampel_penuh"] = cur.tolist()
        else:
            ch = np.flatnonzero(cur != self.prev)
            fr["sampel_ubah"] = np.c_[ch, cur[ch]].tolist()
        self.prev = cur
        return fr


@app.websocket("/ws/simulasi")
async def ws_sim(ws: WebSocket):
    await ws.accept()
    try:
        req = await ws.receive_json()
        n, sd = int(req.get("agen", 100_000)), int(req.get("seed_dunia", 42))
        w = await asyncio.to_thread(get_world, n, sd)
        sc = load_scenario(SCENARIO_DIR / req.get("skenario", "isu_sentani.yaml"))
        iv = load_interventions()
        codes = [req.get("intervensi", "A")] + ([req["banding"]] if req.get("banding") else [])
        sims = []
        for c in codes:
            try:
                dec = make_decider(req.get("llm", "abm"), req.get("beku") or None, sc)
            except Exception as e:
                await ws.send_json({"error": f"LLM tidak siap: {e}. Memakai aturan ABM."})
                dec = make_decider("abm")
            sims.append(Simulation(w, sc, iv[c], seed=int(req.get("seed", 1)), decider=dec,
                                   log_events=bool(req.get("simpan", False)), viz_sample=_viz_n(n), kode_intervensi=c))
        diffs = [_Diff() for _ in sims]
        await ws.send_json({"mulai": True, "T": sims[0].T, "kode": codes, "dt_jam": sims[0].dt_h,
                            "nama_intervensi": {c: iv[c]["nama"] for c in codes}})
        await ws.send_json({"frame": [d(s.frame0()) for s, d in zip(sims, diffs)]})
        stop = asyncio.Event()

        async def listen():
            try:
                while True:
                    msg = await ws.receive_json()
                    if msg.get("stop"):
                        stop.set()
            except Exception:
                stop.set()

        listener = asyncio.create_task(listen())
        for _ in range(sims[0].T):
            if stop.is_set():
                break
            frames = await asyncio.to_thread(lambda: [s.step() for s in sims])
            await ws.send_json({"frame": [d(f) for f, d in zip(frames, diffs)]})
        listener.cancel()
        out, fakta = [], []
        for s in sims:
            res = s.result()
            info = {"kode": s.kode, "manifest": res.manifest,
                    "pernah_dengar": float(s.pernah_dengar.mean()),
                    "distrik_eskalasi": int((res.eskalasi_step >= 0).sum())}
            if req.get("simpan"):
                info["folder"] = str(await asyncio.to_thread(save_run, res, w)).replace(str(ROOT), ".")
            out.append(info)
            fakta.append(ringkas_putaran(res.timeseries, res.risk, res.eskalasi_step, res.events, res.manifest, w.geo,
                                         s.dpop, s.dt_h, sc, iv[s.kode]["nama"], info["pernah_dengar"], res.kanal_masuk,
                                         res.paparan_provokator))
        _last_run.clear()
        _last_run.update(putaran=fakta, kode=sc.get("kode", "skenario") + "_" + "-".join(codes))
        await ws.send_json({"selesai": True, "hasil": out})
    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await ws.send_json({"error": f"{type(e).__name__}: {e}"})
        except Exception:
            pass


@app.get("/api/runs")
def runs():
    d = OUT_DIR / "runs"
    out = []
    for p in sorted(d.glob("*/manifest.json"), key=lambda x: x.stat().st_mtime, reverse=True) if d.exists() else []:
        m = json.loads(p.read_text(encoding="utf-8"))
        out.append({"nama": p.parent.name, "agen": m["agen"], "seed_dunia": m["seed_dunia"], "seed": m["seed"],
                    "intervensi": m["intervensi"], "llm": m["llm"]["mode"]})
    return out


@app.get("/api/runs/{nama}")
def replay(nama: str):
    """Replay dari log Parquet (FR-12/V-07): frame direkonstruksi tanpa menjalankan ulang simulasi."""
    if not re.fullmatch(r"[\w.\-]+", nama):
        raise HTTPException(400, "nama tidak valid")
    d = OUT_DIR / "runs" / nama
    if not d.exists():
        raise HTTPException(404, "putaran tidak ditemukan")
    run = load_run(d)
    m = run["manifest"]
    w = get_world(m["agen"], m["seed_dunia"])
    sim = Simulation(w, load_scenario(), None, seed=1, log_events=False, viz_sample=_viz_n(m["agen"]))
    idx = sim.viz_idx
    ts = run["deret_waktu"]
    T = int(ts["langkah"].max())
    status = np.zeros(w.pop.n, np.int8)
    pe = run["peristiwa"]
    groups = {int(k): g for k, g in pe.groupby("langkah")} if pe is not None else {}
    V = len(w.geo.kampung_nama)
    kam = w.pop.kampung.astype(np.int64)
    frames, diff = [], _Diff()
    for t in range(T + 1):
        g = groups.get(t)
        if g is not None:
            status[g["agen"].to_numpy()] = g["ke"].to_numpy()
        kk = np.bincount(kam * 5 + status, minlength=V * 5).reshape(V, 5)
        sub = ts[ts["langkah"] == t]
        fr = {"t": t, "waktu": sim.label_waktu(t), "jumlah": sub[list(STATUS)].sum().astype(int).tolist(),
              "kampung": kk[:, 1:].T.tolist(), "risiko": sub["risiko"].round(5).tolist(), "arcs": [], "radio": [],
              "sampel": status[idx].tolist(), "events": [e for e in run["naratif"] if e["t"] == t]}
        frames.append(diff(fr))
    return {"manifest": m, "T": T, "frames": frames}


@app.post("/api/skenario")
def simpan_skenario(body: dict):
    sc = body.get("isi") or {}
    try:
        validate_scenario(sc)
        w = get_world(int(body.get("agen", 100_000)), int(body.get("seed_dunia", 42)))
        for tn in sc["tanam"]:
            if "kabupaten" in tn:
                w.geo.kab_index(tn["kabupaten"])
            if "distrik" in tn:
                w.geo.distrik_index(tn["kabupaten"], tn["distrik"])
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    name = re.sub(r"[^\w\-]+", "_", body.get("berkas") or sc.get("kode") or "skenario_baru").strip("_")
    path = SCENARIO_DIR / f"{name}.yaml"
    if path.name == "intervensi.yaml":
        raise HTTPException(422, "nama berkas dicadangkan")
    path.write_text("# Dibuat dari editor skenario dashboard\n" + yaml.safe_dump(sc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return {"berkas": path.name}


@app.get("/api/mc")
def mc_list():
    return [p.parent.name for p in sorted(OUT_DIR.glob("*/hasil.parquet"), key=lambda x: x.stat().st_mtime, reverse=True)]


@app.get("/api/mc/{nama}")
def mc_detail(nama: str):
    if not re.fullmatch(r"[\w.\-]+", nama):
        raise HTTPException(400, "nama tidak valid")
    d = OUT_DIR / nama
    if not (d / "hasil.parquet").exists():
        raise HTTPException(404, "hasil tidak ditemukan")
    from .analysis import paired_effect, ranking, recommendations, summarize

    df = pd.read_parquet(d / "hasil.parquet")
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    summ, eff, rank = summarize(df), paired_effect(df), ranking(df, n_boot=500)
    summ["tipe"] = summ["tipe"].astype(str)
    return JSONResponse(json.loads(json.dumps({
        "manifest": man, "ringkasan": summ.to_dict("records"), "efek": eff.to_dict("records"),
        "peringkat": rank.to_dict("records"), "rekomendasi": recommendations(rank, eff, man["intervensi"]),
        "kurva": json.loads((d / "kurva_rata2.json").read_text(encoding="utf-8")),
        "laporan": f"/out/{nama}/laporan.html" if (d / "laporan.html").exists() else None,
        "keterlambatan": _ket_mc(d),
    }, default=lambda o: None if isinstance(o, float) and np.isnan(o) else str(o)).replace("NaN", "null")))


def _ket_mc(d: Path):
    """R3: kurva harga keterlambatan bila folder Monte Carlo ini hasil `keterlambatan`."""
    from .keterlambatan import muat, untuk_laporan

    h = muat(d)
    return untuk_laporan(h) if h else None


@app.get("/out/{nama}/laporan.html")
def mc_report(nama: str):
    if not re.fullmatch(r"[\w.\-]+", nama):
        raise HTTPException(400, "nama tidak valid")
    p = OUT_DIR / nama / "laporan.html"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/api/mc")
def mc_start(body: dict):
    if _mc_state["jalan"]:
        raise HTTPException(409, "Monte Carlo lain sedang berjalan")
    from .montecarlo import run_montecarlo
    from .report import build_report

    def job():
        _mc_state.update(jalan=True, log=[], hasil=None, error=None)
        try:
            d = run_montecarlo(int(body.get("agen", 100_000)), int(body.get("seed_dunia", 42)), int(body.get("putaran", 50)),
                               body.get("intervensi", "ABCDEF"), load_scenario(SCENARIO_DIR / body.get("skenario", "isu_sentani.yaml")),
                               mode=body.get("llm", "abm"), beku=body.get("beku") or None,
                               progress=lambda s: _mc_state["log"].append(s))
            build_report(d)
            _mc_state["hasil"] = d.name
        except Exception as e:
            _mc_state["error"] = f"{type(e).__name__}: {e}"
        finally:
            _mc_state["jalan"] = False

    threading.Thread(target=job, daemon=True).start()
    return {"mulai": True}


@app.get("/api/mc-status")
def mc_status():
    return {k: v for k, v in _mc_state.items() if k != "log"} | {"log": _mc_state["log"][-5:]}


@app.post("/api/alur")
def alur_start(body: dict):
    """Alur otomatis: eksplorasi → bekukan → Monte Carlo → putaran contoh peta → laporan lapangan PDF."""
    if _mc_state["jalan"] or _alur_state["jalan"]:
        raise HTTPException(409, "Monte Carlo atau alur otomatis lain sedang berjalan")
    from .alur import TAHAP, jalankan_alur

    berkas = body.get("skenario", "isu_sentani.yaml")
    if not re.fullmatch(r"[\w.\-]+\.yaml", berkas) or not (SCENARIO_DIR / berkas).exists():
        raise HTTPException(422, "skenario tidak ditemukan")
    llm = body.get("llm", "abm")
    if llm not in ("abm", "mock", "sumopod", "deepseek", "ollama"):
        raise HTTPException(422, "penalar tokoh tidak dikenal")

    def tahap(tid, status, catatan=""):
        for t in _alur_state["tahap"]:
            if t["id"] == tid:
                t.update(status=status, catatan=catatan)
        _alur_state["log"].append(f"[{tid}] {catatan}")

    _alur_state.update(jalan=True, log=[], hasil=None, error=None,
                       tahap=[{"id": i, "judul": j, "status": "menunggu", "catatan": ""} for i, j in TAHAP])
    _mc_state["jalan"] = True           # cegah Monte Carlo lain berebut CPU

    def job():
        try:
            _alur_state["hasil"] = jalankan_alur(
                SCENARIO_DIR / berkas, int(body.get("agen", 100_000)), int(body.get("seed_dunia", 42)), llm,
                pilot=max(1, min(int(body.get("pilot", 2)), 10)), eksplorasi=max(1, min(int(body.get("eksplorasi", 3)), 10)),
                putaran=max(3, min(int(body.get("putaran", 50)), 1000)), kode=str(body.get("intervensi", "ABCDEF")),
                penyedia_laporan=body.get("penyedia"), fokus=str(body.get("fokus") or ""),
                progress=lambda s: _alur_state["log"].append(s), tahap=tahap,
                kurva_keterlambatan=bool(body.get("kurva_keterlambatan")))
        except Exception as e:
            _alur_state["error"] = f"{type(e).__name__}: {e}"
            for t in _alur_state["tahap"]:
                if t["status"] == "jalan":
                    t.update(status="gagal", catatan=_alur_state["error"])
        finally:
            _alur_state["jalan"] = False
            _mc_state["jalan"] = False

    threading.Thread(target=job, daemon=True).start()
    return {"mulai": True}


@app.get("/api/alur-status")
def alur_status():
    h = _alur_state.get("hasil")
    return {k: v for k, v in _alur_state.items() if k not in ("log", "hasil")} | {
        "log": _alur_state["log"][-6:],
        "hasil": h and {**h, "pdf": f"/unduh/{h['laporan_pdf']}" if h.get("laporan_pdf") else None,
                        "html": f"/unduh/{h['laporan_html']}"}}


@app.get("/api/peran")
def peran():
    return list(PERAN)


def _cari_skenario(kode: str) -> dict | None:
    for s in _scenarios():
        isi = s.get("isi") or {}
        if kode in (isi.get("kode"), isi.get("nama")):
            return isi
    return None


@app.post("/api/ai/skenario")
def ai_skenario(body: dict):
    w = get_world(int(body.get("agen", 100_000)), int(body.get("seed_dunia", 42)))
    try:
        sc, info = susun_skenario(str(body.get("deskripsi", "")), w.geo, body.get("penyedia"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # kunci tidak ada, jaringan, JSON rusak dari LLM
        raise HTTPException(502, f"Gagal menyusun skenario: {type(e).__name__}: {e}")
    return {"isi": sc, "info": info}


@app.post("/api/ai/contoh-pesan")
def ai_contoh_pesan(body: dict):
    sc = body.get("isi") or {}
    if not sc.get("provokator"):
        raise HTTPException(422, "Skenario belum punya provokator/buzzer.")
    try:
        sc, info = buat_contoh_pesan(sc, body.get("penyedia"), bool(body.get("timpa", False)))
    except Exception as e:
        raise HTTPException(502, f"Gagal membuat contoh pesan: {type(e).__name__}: {e}")
    return {"isi": sc, "info": info}


def _fakta_log(nama: str) -> dict:
    """Fakta ringkas dari log putaran tersimpan (out/runs/<nama>)."""
    run = load_run(OUT_DIR / "runs" / nama)
    m, dt = run["manifest"], run["deret_waktu"]
    w = get_world(m["agen"], m["seed_dunia"])
    T, D = int(dt["langkah"].max()) + 1, int(dt["distrik"].max()) + 1
    dt = dt.sort_values(["langkah", "distrik"])
    ts = dt[list(STATUS)].to_numpy().reshape(T, D, 5)
    risk = dt["risiko"].to_numpy().reshape(T, D)
    over = risk > float(load_params()["ambang_eskalasi"])
    esk = np.where(over.any(0), over.argmax(0), -1)
    dpop = np.maximum(np.bincount(w.pop.distrik, minlength=D), 1)
    iv = load_interventions().get(m["intervensi"], {})
    km, pe = None, run["peristiwa"]
    if pe is not None:   # log tidak menyimpan tipe wilayah per transisi: semua dimasukkan ke baris pertama
        km = np.zeros((2, 1, 8), np.int64)
        for j, ke in enumerate((1, 3)):
            km[j, 0] = np.bincount(pe.loc[pe["ke"] == ke, "kanal"].to_numpy(np.int64), minlength=8)[:8]
    return ringkas_putaran(ts, risk, esk, run["naratif"], m, w.geo, dpop, m["langkah_menit"] / 60,
                           _cari_skenario(m["skenario"]), iv.get("nama", m["intervensi"]), kanal_masuk=km)


@app.post("/api/ai/laporan")
def ai_laporan(body: dict):
    """sumber: putaran (dashboard terakhir) | log (nama = folder out/runs) | mc (nama = folder Monte Carlo)."""
    sumber, nama = body.get("sumber", "putaran"), str(body.get("nama") or "")
    if nama and not re.fullmatch(r"[\w.\-]+", nama):
        raise HTTPException(400, "nama tidak valid")
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    if sumber == "mc":
        d = OUT_DIR / nama
        if not nama or not (d / "hasil.parquet").exists():
            raise HTTPException(404, "hasil Monte Carlo tidak ditemukan")
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        fakta, jenis = ringkas_mc(d, _cari_skenario(man.get("skenario", ""))), "mc"
        folder, berkas = d, f"laporan_ai_{stamp}"
    elif sumber == "log":
        if not nama or not (OUT_DIR / "runs" / nama / "manifest.json").exists():
            raise HTTPException(404, "log putaran tidak ditemukan")
        fakta, jenis = {"putaran": [_fakta_log(nama)]}, "putaran"
        folder, berkas = OUT_DIR / "runs" / nama, f"laporan_ai_{stamp}"
    else:
        if not _last_run:
            raise HTTPException(404, "Belum ada putaran di dashboard. Jalankan simulasi sampai selesai dulu.")
        fakta, jenis = {"putaran": _last_run["putaran"]}, "putaran"
        folder, berkas = OUT_DIR / "laporan_ai", f"{_last_run['kode']}_{stamp}"
    try:
        md, info = tulis_laporan(fakta, jenis, body.get("penyedia"), str(body.get("fokus") or ""))
    except Exception as e:
        raise HTTPException(502, f"Gagal membuat laporan: {type(e).__name__}: {e}")
    md += (f"\n\n---\n\n> Draf dibuat otomatis oleh {info['model']} ({info['penyedia']}) pada {stamp} dari angka hasil "
           "simulasi. Periksa ulang sebelum dipakai; semua agen fiktif dan parameter belum dikalibrasi penuh.\n")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{berkas}.md").write_text(md, encoding="utf-8")
    # fakta disimpan agar PDF bisa dibangun ulang: python -m sintetis pdf <berkas.md>
    (folder / f"{berkas}.json").write_text(json.dumps({"jenis": jenis, "info": info, "fakta": fakta}, ensure_ascii=False,
                                                      default=str), encoding="utf-8")
    body_html = md_to_html(md)
    (folder / f"{berkas}.html").write_text(html_page(body_html, "Laporan AI Indonesia Sintetis"), encoding="utf-8")
    pdf, pdf_err = None, None
    try:
        from .laporan_pdf import build_pdf

        pdf = build_pdf(md, fakta, jenis, info, folder / f"{berkas}.pdf")
    except Exception as e:  # PDF gagal tidak boleh menggagalkan laporan teks
        pdf_err = f"{type(e).__name__}: {e}"
    rel = lambda p: p.relative_to(OUT_DIR).as_posix()  # noqa: E731
    return {"markdown": md, "html": body_html, "berkas": str(folder / f"{berkas}.md").replace(str(ROOT), "."),
            "pdf": f"/unduh/{rel(pdf)}" if pdf else None, "pdf_error": pdf_err, "info": info,
            "fakta": tanpa_grafik(fakta)}


@app.get("/unduh/{path:path}")
def unduh(path: str):
    """Unduh berkas laporan dari out/ (hanya .pdf/.html/.md, tidak bisa keluar dari folder out/)."""
    p = (OUT_DIR / path).resolve()
    if not p.is_relative_to(OUT_DIR.resolve()) or p.suffix not in (".pdf", ".html", ".md") or not p.is_file():
        raise HTTPException(404, "berkas tidak ditemukan")
    media = {".pdf": "application/pdf", ".html": "text/html", ".md": "text/markdown"}[p.suffix]
    return FileResponse(p, media_type=media, filename=p.name if p.suffix != ".html" else None,
                        content_disposition_type="inline")


# ================================================================================================ tim lapangan
# R1 kartu · R2 pustaka & Ruang Situasi · R3 keterlambatan · R4 perencana · R5 latihan meja · R6 kajian
from . import pustaka as _pustaka  # noqa: E402

_jobs: dict[str, dict] = {}


def _mulai(jenis: str, fn) -> dict:
    """Pekerjaan latar berat (satu per jenis); ditolak bila Monte Carlo/alur sedang memakai CPU."""
    if _mc_state["jalan"] or _alur_state["jalan"]:
        raise HTTPException(409, "Monte Carlo atau alur otomatis sedang berjalan; tunggu selesai")
    if (_jobs.get(jenis) or {}).get("jalan"):
        raise HTTPException(409, f"{jenis} lain sedang berjalan")
    st = _jobs[jenis] = {"jalan": True, "log": [], "hasil": None, "error": None}

    def job():
        try:
            st["hasil"] = fn(lambda s: st["log"].append(str(s)))
        except Exception as e:
            st["error"] = f"{type(e).__name__}: {e}"
        finally:
            st["jalan"] = False

    threading.Thread(target=job, daemon=True).start()
    return {"mulai": True}


def _status_job(jenis: str) -> dict:
    st = _jobs.get(jenis) or {"jalan": False, "log": [], "hasil": None, "error": None}
    return {k: v for k, v in st.items() if k != "log"} | {"log": st["log"][-4:]}


def _bersih_obj(o):
    def bawaan(x):
        if isinstance(x, (np.integer,)):
            return int(x)
        if isinstance(x, (np.floating,)):
            return None if np.isnan(x) else float(x)
        if isinstance(x, np.bool_):
            return bool(x)
        if isinstance(x, set):
            return sorted(x)
        return str(x)
    return json.loads(json.dumps(o, ensure_ascii=False, default=bawaan).replace("NaN", "null"))


def _bersih_json(o):
    return JSONResponse(_bersih_obj(o))


@app.get("/lapangan")
def halaman_lapangan():
    return FileResponse(WEB / "lapangan.html")


@app.get("/latihan")
def halaman_latihan():
    return FileResponse(WEB / "latihan.html")


@app.get("/api/lapangan/meta")
def lapangan_meta():
    from .config import CONFIG_DIR, load_region, versi_model
    from .latihan import daftar_skrip
    from .perencana import rencana_tersimpan

    geo = _pustaka.geografi()
    region = load_region()
    st = _pustaka.status()
    return _bersih_json({
        "kabupaten": [{"id": k["id"], "nama": k["nama"],
                       "distrik": sorted(geo.distrik_nama[d] for d in np.flatnonzero(geo.distrik_kab == geo.kab_index(k["id"])))}
                      for k in region["kabupaten"]],
        "jenis": [{"id": j, "nama": t["nama"], "perlu_tinjauan": bool(t.get("perlu_tinjauan"))}
                  for j, t in _pustaka.muat_templat().items()],
        "kanal": [{"id": k, "nama": v} for k, v in _pustaka.KANAL_NAMA.items()],
        "profil": sorted(p.stem for p in (CONFIG_DIR / "sumber_daya").glob("*.yaml")),
        "skrip": daftar_skrip(), "skenario": [s["berkas"] for s in _scenarios() if "error" not in s],
        "pustaka": {**{k: st[k] for k in ("versi", "versi_sekarang", "usang", "ada", "selesai", "total_desain", "sel_berkurva")},
                    "teks": _pustaka.teks_status(st)},
        "versi_model": versi_model(), "rencana": rencana_tersimpan(),
    })


# --- R1 kartu ------------------------------------------------------------------------------------------
@app.get("/api/kartu/{kab}.pdf")
def kartu_pdf(kab: str, bahasa: str = "id"):
    from .config import load_region
    from .kartu import buat_kartu

    if kab not in {k["id"] for k in load_region()["kabupaten"]}:
        raise HTTPException(404, "kabupaten tidak dikenal")
    try:
        p = buat_kartu(kab, bahasa=bahasa)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return FileResponse(p, media_type="application/pdf", filename=p.name, content_disposition_type="inline")


# --- R2 pustaka & Ruang Situasi --------------------------------------------------------------------------
@app.get("/api/pustaka")
def pustaka_status():
    st = _pustaka.status()
    return {**{k: v for k, v in st.items() if k != "belum"}, "belum": len(st["belum"]), "teks": _pustaka.teks_status(st)}


@app.post("/api/situasi")
def situasi_post(body: dict):
    from .situasi import nilai

    try:
        return _bersih_json(nilai(body))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/situasi")
def situasi_daftar():
    from .situasi import daftar_log

    return daftar_log()


@app.post("/api/situasi/{sid}/hitung")
def situasi_hitung(sid: str):
    from .situasi import mulai_hitung

    if _mc_state["jalan"] or _alur_state["jalan"]:
        raise HTTPException(409, "Monte Carlo atau alur otomatis sedang berjalan")
    try:
        return mulai_hitung(sid)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/situasi/{sid}/status")
def situasi_hitung_status(sid: str):
    from .situasi import status_hitung

    try:
        return _bersih_json(status_hitung(sid))
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))


# --- R3 keterlambatan ----------------------------------------------------------------------------------
@app.post("/api/keterlambatan")
def keterlambatan_post(body: dict):
    from .keterlambatan import hitung_keterlambatan

    berkas = body.get("skenario", "isu_sentani.yaml")
    if not re.fullmatch(r"[\w.\-]+\.yaml", berkas) or not (SCENARIO_DIR / berkas).exists():
        raise HTTPException(422, "skenario tidak ditemukan")
    kode = re.sub(r"[^A-Z]", "", str(body.get("intervensi", "CDF")).upper()) or "CDF"

    def fn(log):
        d = hitung_keterlambatan(SCENARIO_DIR / berkas, kode, str(body.get("jam", "0:24:2")),
                                 max(3, min(int(body.get("putaran", 30)), 500)), int(body.get("agen", 20_000)), progress=log)
        return d.name

    return _mulai("keterlambatan", fn)


@app.get("/api/keterlambatan-status")
def keterlambatan_status():
    return _status_job("keterlambatan")


@app.get("/api/keterlambatan/{nama}")
def keterlambatan_get(nama: str):
    from .keterlambatan import muat

    if not re.fullmatch(r"[\w.\-]+", nama):
        raise HTTPException(400, "nama tidak valid")
    h = muat(OUT_DIR / nama)
    if h is None:
        raise HTTPException(404, "kurva keterlambatan tidak ditemukan")
    return _bersih_json(h)


# --- R4 perencana --------------------------------------------------------------------------------------
@app.post("/api/perencana")
def perencana_post(body: dict):
    from .perencana import muat_profil, rencanakan

    prof = body.get("profil_isi") or str(body.get("profil") or "")
    try:
        prof = muat_profil(prof if isinstance(prof, dict) else (prof if re.fullmatch(r"[\w\-]+", prof) else "x"))
    except Exception as e:
        raise HTTPException(422, f"profil tidak valid: {e}")
    sk = str(body.get("skenario") or "")
    try:
        if "__" in sk:
            sc = _pustaka.skenario_sel(sk)
        elif re.fullmatch(r"[\w.\-]+\.yaml", sk) and (SCENARIO_DIR / sk).exists():
            sc = load_scenario(SCENARIO_DIR / sk)
        else:
            raise ValueError
    except Exception:
        raise HTTPException(422, "skenario tidak ditemukan")

    def fn(log):
        return rencanakan(sc, prof, agen=int(body.get("agen", 20_000)), putaran_awal=max(3, min(int(body.get("putaran_awal", 10)), 50)),
                          sampel_ketahanan=max(0, min(int(body.get("ketahanan", 6)), 30)), progress=log)

    return _mulai("perencana", fn)


@app.get("/api/perencana-status")
def perencana_status():
    return _bersih_json(_status_job("perencana"))


@app.post("/api/perencana/simpan")
def perencana_simpan(body: dict):
    from .perencana import simpan_rencana

    h = (_jobs.get("perencana") or {}).get("hasil")
    if not h:
        raise HTTPException(404, "belum ada hasil perencana")
    p = simpan_rencana(h, body.get("kode"))
    return {"berkas": p.name}


# --- R6 kajian -----------------------------------------------------------------------------------------
@app.get("/api/kajian")
def kajian_daftar():
    from .kajian import CONTOH, daftar

    return {"daftar": daftar(), "contoh": CONTOH}


@app.get("/api/kajian/{kid}.pdf")
def kajian_pdf(kid: str):
    from .kajian import KAJIAN_DIR

    if not re.fullmatch(r"[\w\-]+", kid) or not (KAJIAN_DIR / f"{kid}.pdf").exists():
        raise HTTPException(404, "PDF kajian belum ada; jalankan analisis")
    return FileResponse(KAJIAN_DIR / f"{kid}.pdf", media_type="application/pdf", content_disposition_type="inline")


@app.get("/api/kajian/{kid}")
def kajian_get(kid: str):
    from .kajian import KAJIAN_DIR, muat

    try:
        k = muat(kid)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "kajian tidak ditemukan")
    a = KAJIAN_DIR / f"{kid}_analisis.json"
    return _bersih_json({"isi": k, "analisis": json.loads(a.read_text(encoding="utf-8")) if a.exists() else None})


@app.post("/api/kajian")
def kajian_simpan(body: dict):
    from .kajian import simpan

    try:
        p = simpan(body.get("isi") or {}, str(body.get("pengguna") or "") or None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"berkas": p.name}


@app.post("/api/kajian/dari-situasi/{sid}")
def kajian_dari_situasi(sid: str):
    from .kajian import dari_situasi

    try:
        return _bersih_json(dari_situasi(sid))
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))


@app.post("/api/kajian/{kid}/analisis")
def kajian_analisis(kid: str, body: dict | None = None):
    from .kajian import analisis, muat

    try:
        muat(kid)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "kajian tidak ditemukan")
    body = body or {}
    return _mulai("kajian", lambda log: analisis(kid, agen=int(body.get("agen", 20_000)),
                                                 putaran=max(3, min(int(body.get("putaran", 20)), 200)), progress=log))


@app.get("/api/kajian-status")
def kajian_status():
    return _bersih_json(_status_job("kajian"))


# --- R5 latihan meja -----------------------------------------------------------------------------------
_sesi: dict[str, dict] = {}       # id → {"sesi": SesiLatihan, "soket": [(ws, peran)], "kunci": asyncio.Lock}


@app.get("/api/latihan")
def latihan_daftar():
    from .latihan import daftar_skrip

    return {"sesi": [{"id": k, "skenario": v["sesi"].sc.get("nama"), "ronde": v["sesi"].ronde_ke,
                      "n_ronde": v["sesi"].n_ronde} for k, v in _sesi.items()], "skrip": daftar_skrip()}


async def _siarkan(sid: str) -> None:
    s = _sesi[sid]
    mati = []
    for ws, peran in list(s["soket"]):
        try:
            await ws.send_json({"status": _bersih_obj(s["sesi"].status(peran))})
        except Exception:
            mati.append((ws, peran))
    for m in mati:
        if m in s["soket"]:
            s["soket"].remove(m)


@app.websocket("/ws/latihan")
async def ws_latihan(ws: WebSocket):
    """Pesan pertama: {buat: {...}} (fasilitator) atau {gabung: id, peran: fasilitator|peserta}. Lalu aksi:
    ronde · suntik {indeks|teks} · putuskan {pilihan, jumlah, distrik, catatan} · nilai {ronde, nilai, catatan} ·
    setuju {persetujuan} · debrief. Peserta hanya menerima status tanpa keadaan simulasi lengkap."""
    from .latihan import buat_sesi

    await ws.accept()
    sid, peran = None, "peserta"
    try:
        msg = await ws.receive_json()
        if "buat" in msg:
            try:
                sesi = await asyncio.to_thread(buat_sesi, msg["buat"])
            except (ValueError, KeyError, FileNotFoundError) as e:
                await ws.send_json({"error": f"Gagal membuat sesi: {e}"})
                return
            sid, peran = sesi.id, "fasilitator"
            _sesi[sid] = {"sesi": sesi, "soket": [], "kunci": asyncio.Lock()}
        else:
            sid, peran = str(msg.get("gabung")), ("fasilitator" if msg.get("peran") == "fasilitator" else "peserta")
            if sid not in _sesi:
                await ws.send_json({"error": "sesi tidak ditemukan"})
                return
        _sesi[sid]["soket"].append((ws, peran))
        await ws.send_json({"sesi": sid, "peran": peran})
        await _siarkan(sid)
        while True:
            m = await ws.receive_json()
            s = _sesi[sid]
            sesi = s["sesi"]
            try:
                async with s["kunci"]:
                    a = m.get("aksi")
                    if a == "putuskan":
                        sesi.putuskan(str(m["pilihan"]), m.get("jumlah"), m.get("distrik") or None, str(m.get("catatan") or ""))
                    elif a == "nilai":
                        sesi.nilai(int(m.get("ronde", sesi.ronde_ke)), str(m["nilai"]), str(m.get("catatan") or ""), peran)
                    elif peran != "fasilitator":
                        raise ValueError("aksi ini hanya untuk fasilitator")
                    elif a == "ronde":
                        await asyncio.to_thread(sesi.ronde_berikut)
                    elif a == "suntik":
                        sk = sesi.skrip.get("suntikan") or []
                        sesi.suntik(sk[int(m["indeks"])] if m.get("indeks") is not None else {"teks": str(m.get("teks", ""))[:300]})
                    elif a == "setuju":
                        sesi.persetujuan = bool(m.get("persetujuan"))
                    elif a == "debrief":
                        d = await asyncio.to_thread(sesi.debrief)
                        pdf = Path(d["pdf"]).resolve() if d.get("pdf") else None
                        if pdf is not None and pdf.is_relative_to(OUT_DIR.resolve()):
                            d["pdf_url"] = "/unduh/" + pdf.relative_to(OUT_DIR.resolve()).as_posix()
                        for w2, _ in list(s["soket"]):
                            try:
                                await w2.send_json({"debrief": _bersih_obj(d)})
                            except Exception:
                                pass
                    else:
                        raise ValueError(f"aksi tidak dikenal: {a}")
            except (ValueError, KeyError, IndexError, TypeError) as e:
                await ws.send_json({"error": str(e)})
            await _siarkan(sid)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"error": f"{type(e).__name__}: {e}"})
        except Exception:
            pass
    finally:
        if sid in _sesi:
            _sesi[sid]["soket"] = [x for x in _sesi[sid]["soket"] if x[0] is not ws]
