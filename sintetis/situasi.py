"""R2b Ruang Situasi: masukkan apa yang diketahui tentang isu nyata, dapatkan perkiraan dan tindakan dari
pustaka (R2a) dalam hitungan detik, tanpa LLM dan tanpa internet.

Pencocokan:
  - jenis isu, kanal awal, ajakan bertindak, kabupaten: dicocokkan persis. Bila tidak ada sel yang cocok,
    dipakai sel terdekat dan label kecocokan diturunkan (tinggi → sedang → rendah).
  - distrik hanya menandai distrik asal di daftar distrik terdampak.
  - waktu berjalan = sekarang − isu mulai beredar; bila jam beredar tidak diketahui: jam diketahui + jeda
    deteksi bawaan (F5).
Intervensi yang jendela waktunya sudah lewat (R3) tidak direkomendasikan. Prebunking hanya bisa sebelum isu.

Setiap sesi disimpan di out/situasi/<id>.json tanpa nama orang atau akun; log ini bisa dijadikan draf
Kajian pasca-kejadian (R6).
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import pustaka
from .config import OUT_DIR, TANDA_SIMULASI, jeda_deteksi, load_interventions, load_params, load_region, versi_model
from .keterlambatan import nama_pendek

BOBOT_DIMENSI = {"kabupaten": 3, "jenis": 3, "kanal": 1, "ajakan": 1}
NAMA_DIMENSI = {"kabupaten": "kabupaten", "jenis": "jenis isu", "kanal": "kanal awal", "ajakan": "ajakan bertindak"}
FIELD = ("jenis", "deskripsi", "kabupaten", "distrik", "kanal", "ajakan", "tanda_bahaya", "waktu_beredar",
         "waktu_diketahui", "waktu_sekarang", "jam_sejak_beredar", "jam_sejak_diketahui")


def situasi_dir() -> Path:
    return OUT_DIR / "situasi"


def _jam(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600


def rapikan_masukan(m: dict) -> dict:
    """Hanya kolom yang diizinkan; tidak ada kolom nama orang, akun, atau tautan unggahan."""
    out = {k: m.get(k) for k in FIELD if m.get(k) not in (None, "")}
    if "deskripsi" in out:
        d = re.sub(r"https?://\S+|@\w+|\+?\d[\d\s\-]{7,}\d", "[dihapus]", str(out["deskripsi"]))[:200]
        out["deskripsi"] = d
    for k in ("ajakan", "tanda_bahaya"):
        if k in out:
            out[k] = out[k] in (True, "ya", "true", "1", 1)
    for k in ("jam_sejak_beredar", "jam_sejak_diketahui"):
        if k in out:
            out[k] = max(0.0, float(out[k]))
    if out.get("kanal") not in (None, *pustaka.KANAL_AWAL):
        raise ValueError(f"kanal harus salah satu dari {pustaka.KANAL_AWAL}")
    kabs = [k["id"] for k in load_region()["kabupaten"]]
    if out.get("kabupaten") not in kabs:
        raise ValueError(f"kabupaten harus salah satu dari {kabs}")
    return out


def cocokkan(m: dict, sel_ada: list[str]) -> dict:
    if not sel_ada:
        return {"sel": None, "label": "tidak ada", "tidak_cocok": list(NAMA_DIMENSI.values())}
    want = {"jenis": m.get("jenis"), "kabupaten": m["kabupaten"], "kanal": m.get("kanal", "wa"), "ajakan": bool(m.get("ajakan"))}
    best, skor_best, beda_best = None, 1e9, []
    for s in sel_ada:
        c = pustaka.urai_sel(s)
        beda = [k for k in BOBOT_DIMENSI if c[k] != want[k]]
        skor = sum(BOBOT_DIMENSI[k] for k in beda)
        if skor < skor_best:
            best, skor_best, beda_best = s, skor, beda
    label = "tinggi" if not beda_best else "sedang" if set(beda_best) <= {"kanal", "ajakan"} and len(beda_best) == 1 else "rendah"
    return {"sel": best, "label": label, "tidak_cocok": [NAMA_DIMENSI[k] for k in beda_best],
            "lainnya": m.get("jenis") in (None, "lainnya")}


def _waktu(m: dict, params: dict, tipe: str | None) -> dict:
    jd = jeda_deteksi({}, params, tipe)
    diket = m.get("jam_sejak_diketahui")
    if diket is None:
        diket = _jam(m.get("waktu_diketahui"), m.get("waktu_sekarang") or datetime.now().isoformat(timespec="minutes")) or 0.0
    beredar = m.get("jam_sejak_beredar")
    if beredar is None and m.get("waktu_beredar"):
        beredar = _jam(m["waktu_beredar"], m.get("waktu_sekarang") or datetime.now().isoformat(timespec="minutes"))
    perkiraan = beredar is None
    if perkiraan:
        beredar = diket + jd
    return {"jam_sejak_beredar": round(max(beredar, 0), 1), "jam_sejak_diketahui": round(max(diket, 0), 1),
            "jeda_dipakai_jam": round(max(beredar - diket, 0), 1), "jam_beredar_diperkirakan": perkiraan,
            "jeda_deteksi_bawaan_jam": jd}


def _t_lib(spec: dict, jeda: float) -> float:
    """Jadwal intervensi pustaka, dalam jam sejak isu DIKETAHUI."""
    t = []
    for s in spec.get("langkah", []):
        t.append(float(s["t_sejak_deteksi"]) if s.get("t_sejak_deteksi") is not None else float(s.get("t_jam", 0)) - jeda)
    return min(t) if t else 0.0


def nilai(masukan: dict, simpan: bool = True, root=None) -> dict:
    """Hasil Ruang Situasi dari pustaka. Tanpa LLM; murni membaca tabel."""
    m = rapikan_masukan(masukan)
    P = pustaka.muat(root)
    params, iv = load_params(), load_interventions()
    tk = pustaka.tipe_kab()
    kab = m["kabupaten"]
    region = {k["id"]: k for k in load_region()["kabupaten"]}
    w = _waktu(m, params, tk.get(kab))
    hasil = {"id": f"{datetime.now():%Y%m%d-%H%M%S}-{kab}", "tanda": TANDA_SIMULASI, "versi_model": versi_model(),
             "masukan": m, "waktu": w, "kabupaten_nama": region[kab]["nama"],
             "tautan": {"kartu": f"/api/kartu/{kab}.pdf", "perencana": f"/lapangan#perencana?kabupaten={kab}"}}
    if P is None:
        hasil.update(pustaka=pustaka.teks_status(pustaka.status(root)), kecocokan={"label": "tidak ada", "sel": None})
        return _simpan(hasil) if simpan else hasil
    st = P["status"]
    hasil["pustaka"] = {"versi": st["versi"], "usang": st["usang"], "teks": pustaka.teks_status(st)}
    ind, dist, ket = P["indeks"], P["distrik"], P["keterlambatan"]
    c = cocokkan(m, sorted(ind["sel"].unique()))
    hasil["kecocokan"] = c
    sel = c["sel"]
    ci = ind[ind["sel"] == sel]
    wil = kab if (ci["tipe"] == kab).any() else "semua"
    base = ci[(ci["tipe"] == wil) & (ci["intervensi"] == "A")]
    if len(base):
        b = base.iloc[0]
        p10, p50, p90 = (b.get(f"jam_puncak_penyebar_p{q}") for q in (10, 50, 90))
        j = w["jam_sejak_beredar"]
        if m.get("tanda_bahaya"):
            fase, kal = "memanas", "Sudah ada tanda bahaya di lapangan: isu kemungkinan sudah berubah menjadi tindakan."
        elif j < p10:
            fase, kal = "naik", "Isu kemungkinan sedang di fase naik."
        elif j <= p90:
            fase, kal = "sekitar puncak", "Isu kemungkinan sedang di sekitar puncak penyebaran."
        else:
            fase, kal = "menurun", "Pada skenario serupa, penyebaran biasanya sudah melewati puncak, tetapi siaga bisa bertahan."
        kal += (f" Pada skenario serupa, puncak penyebar biasanya T+{p10:.0f} sampai T+{p90:.0f} jam "
                f"(median T+{p50:.0f}); siaga aktif memuncak sekitar T+{b.get('jam_puncak_siaga_p50', np.nan):.0f} jam.")
        hasil["posisi"] = {"fase": fase, "kalimat": kal, "puncak_p10": p10, "puncak_p50": p50, "puncak_p90": p90,
                           "peluang_memanas_tanpa_tindakan": float(b["peluang_eskalasi"]), "wilayah": wil}
    # distrik yang kemungkinan terdampak berikutnya
    dd = dist[(dist["sel"] == sel) & (dist["intervensi"] == "A")].copy()
    if len(dd):
        dd = dd.sort_values(["peluang_memanas", "jam_median"], ascending=[False, True]).head(5)
        hasil["distrik"] = [{"nama": r["nama"], "kabupaten": r["kabupaten"], "peluang_memanas": round(float(r["peluang_memanas"]), 2),
                             "jam_p10": _r(r["jam_p10"]), "jam_p90": _r(r["jam_p90"]),
                             "asal": bool(m.get("distrik") and str(r["nama"]).lower() == str(m["distrik"]).lower())}
                            for _, r in dd.iterrows()]
    hasil["tindakan"] = tindakan(ci, ket[ket["sel"] == sel] if len(ket) else ket, ket, iv, wil, w, kab)
    ok = [t for t in hasil["tindakan"] if t["status"] == "bisa" and (t["efek_persen"] or 0) < 0]
    hasil["tindakan_sekarang"] = ok[0] if ok else None
    hasil["harga_keterlambatan"] = (ok[0].get("kalimat_keterlambatan") if ok else None) or \
        "Belum ada kurva harga keterlambatan untuk skenario serupa. Prinsip umumnya: makin cepat makin baik."
    if m.get("tanda_bahaya"):
        hasil["catatan_tanda_bahaya"] = ("Utamakan langkah tatap muka (tokoh gereja dan adat, tim keliling) di distrik yang "
                                         "melapor, hentikan penyebaran ulang, dan koordinasikan dengan aparat setempat.")
    return _simpan(hasil) if simpan else hasil


def tindakan(ci: pd.DataFrame, ket_sel: pd.DataFrame, ket_semua: pd.DataFrame, iv: dict, wil: str, w: dict, kab: str) -> list[dict]:
    out = []
    rows = ci[(ci["tipe"] == wil) & (ci["intervensi"] != "A")].sort_values("efek_persen")
    jeda = w["jeda_deteksi_bawaan_jam"]
    delay = w["jam_sejak_diketahui"]
    for _, r in rows.iterrows():
        kode = r["intervensi"]
        spec = iv.get(kode, {"langkah": []})
        t = {"kode": kode, "nama": r.get("nama_intervensi") or spec.get("nama", kode), "langkah": nama_pendek(spec),
             "efek_persen": _r(r["efek_persen"], 1), "efek_rentang": None, "keyakinan": r.get("keyakinan") or "rendah"}
        base = ci[(ci["tipe"] == wil) & (ci["intervensi"] == "A")]["siaga_jam_per_1000"]
        if len(base) and base.iat[0]:
            t["efek_rentang"] = [_r(100 * r["selisih_lo"] / base.iat[0], 0), _r(100 * r["selisih_hi"] / base.iat[0], 0)]
        if any(s["jenis"] == "prebunking" for s in spec.get("langkah", [])):
            t.update(status="sebelum_isu", alasan="Prebunking hanya bisa dilakukan sebelum isu muncul; siapkan untuk isu berikutnya.")
            out.append(t)
            continue
        k = _kurva(ket_sel, ket_semua, kode, wil)
        if k is not None:
            t["kalimat_keterlambatan"] = k["kalimat"]
            t["kurva_dari"] = k["dari"]
            bw = _r(k.get("batas_waktu_jam"))
            jm = _r(k.get("jam_maks"))
            if bw is not None and delay > bw:
                t.update(status="lewat", alasan=f"Jendela efektif ±{bw:.0f} jam sejak isu diketahui sudah lewat "
                                                f"(sekarang jam ke-{delay:g}).")
            elif bw is None and jm is not None and delay > jm:
                t.update(status="bisa", alasan=f"Kurva hanya dihitung sampai jam ke-{jm:g}; efek bila dimulai sekarang "
                                                "belum diketahui dan kemungkinan lebih kecil.")
            else:
                t.update(status="bisa", alasan="Masih di dalam jendela efektif." + ("" if bw is None else f" Batas ±jam {bw:.0f}."))
        else:
            tl = _t_lib(spec, jeda)
            if delay <= max(tl, 0) + 0.01:
                t.update(status="bisa", alasan=f"Sesuai jadwal pustaka (mulai ±jam {max(tl, 0):g} sejak isu diketahui).")
            elif delay <= tl + 12:
                t.update(status="bisa", alasan=f"Lebih lambat dari jadwal pustaka (±jam {max(tl, 0):g}); efek kemungkinan lebih kecil.")
            else:
                t.update(status="lewat", alasan=f"Sudah lebih dari 12 jam melewati jadwal pustaka (±jam {max(tl, 0):g}).")
        out.append(t)
    urut = {"bisa": 0, "lewat": 1, "sebelum_isu": 2}
    return sorted(out, key=lambda x: (urut[x["status"]], x["efek_persen"] if x["efek_persen"] is not None else 0))


def _kurva(ket_sel: pd.DataFrame, ket_semua: pd.DataFrame, kode: str, wil: str) -> dict | None:
    for df, dari in ((ket_sel, "skenario ini"), (ket_semua, "skenario lain di pustaka")):
        if df is None or not len(df):
            continue
        x = df[(df["kode"] == kode) & (df["wilayah"].isin([wil, "semua"]))]
        if len(x):
            x = x.sort_values("wilayah", key=lambda s: s != wil)
            r = x.iloc[0].to_dict()
            if dari != "skenario ini":
                bw = pd.to_numeric(df[(df["kode"] == kode)]["batas_waktu_jam"], errors="coerce").median()
                r["batas_waktu_jam"] = None if np.isnan(bw) else float(bw)
            r["dari"] = dari
            return r
    return None


def _r(x, n=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def _simpan(h: dict) -> dict:
    d = situasi_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{h['id']}.json").write_text(json.dumps(h, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return h


def muat_log(sid: str) -> dict:
    if not re.fullmatch(r"[\w.\-]+", sid):
        raise ValueError("id tidak valid")
    return json.loads((situasi_dir() / f"{sid}.json").read_text(encoding="utf-8"))


def daftar_log() -> list[dict]:
    d = situasi_dir()
    out = []
    for p in sorted(d.glob("*.json"), reverse=True) if d.exists() else []:
        try:
            h = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": h["id"], "kabupaten": h.get("kabupaten_nama"), "jenis": h["masukan"].get("jenis"),
                        "kecocokan": (h.get("kecocokan") or {}).get("label")})
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------------------------- hitung khusus
_JOBS: dict[str, dict] = {}


def skenario_khusus(h: dict) -> tuple[dict, dict, list[str]]:
    """Skenario & intervensi yang persis sesuai masukan: tindakan dimulai SEKARANG (jam sejak isu diketahui)."""
    m, w = h["masukan"], h["waktu"]
    sel = h["kecocokan"]["sel"] or pustaka.sel_id(m.get("jenis") if m.get("jenis") in pustaka.muat_templat() else
                                                  "penculikan_anak", m["kabupaten"], m.get("kanal", "wa"), bool(m.get("ajakan")))
    c = pustaka.urai_sel(sel)
    jenis = m.get("jenis") if m.get("jenis") in pustaka.muat_templat() else c["jenis"]
    sid = pustaka.sel_id(jenis, m["kabupaten"], m.get("kanal", "wa"), bool(m.get("ajakan")))
    dis = m.get("distrik")
    try:
        sc = pustaka.skenario_sel(sid, distrik=dis, jeda_deteksi_jam=w["jeda_dipakai_jam"])
    except Exception:
        sc = pustaka.skenario_sel(sid, jeda_deteksi_jam=w["jeda_dipakai_jam"])
    iv = pustaka.intervensi_sel(sid)
    kode = ["A"]
    for k, spec in iv.items():
        if k == "A" or any(s["jenis"] in ("prebunking", "penindakan") for s in spec["langkah"]):
            continue
        t0 = min(float(s.get("t_sejak_deteksi", s.get("t_jam", 0))) for s in spec["langkah"])
        for s in spec["langkah"]:
            off = float(s.get("t_sejak_deteksi", s.get("t_jam", 0))) - t0
            s.pop("t_jam", None)
            s["t_sejak_deteksi"] = w["jam_sejak_diketahui"] + off
        kode.append(k)
    sc["durasi_jam"] = max(int(sc["durasi_jam"]), int(w["jam_sejak_beredar"] + 36))
    return sc, iv, kode


def mulai_hitung(sid: str, agen: int = 20_000, putaran: int = 20) -> dict:
    if any(j["jalan"] for j in _JOBS.values()):
        raise RuntimeError("Hitung khusus lain sedang berjalan")
    h = muat_log(sid)
    sc, iv, kode = skenario_khusus(h)
    _JOBS[sid] = {"jalan": True, "log": [], "hasil": None, "error": None}

    def job():
        from .analysis import paired_effect
        from .montecarlo import run_montecarlo

        try:
            d = run_montecarlo(agen, 42, putaran, kode, sc, iv, out_dir=situasi_dir() / f"{sid}_khusus",
                               progress=lambda s: _JOBS[sid]["log"].append(s))
            df = pd.concat([pd.read_parquet(d / "hasil.parquet"), pd.read_parquet(d / "hasil_kabupaten.parquet")])
            e = paired_effect(df)
            kab = h["masukan"]["kabupaten"]
            e = e[e["tipe"].isin([kab, "semua"])]
            res = [{"kode": r["intervensi"], "nama": iv[r["intervensi"]]["nama"], "wilayah": r["tipe"],
                    "efek_persen": _r(r["efek_persen"]), "signifikan": bool(r["signifikan"])} for _, r in e.iterrows()]
            _JOBS[sid]["hasil"] = sorted(res, key=lambda x: (x["wilayah"] != kab, x["efek_persen"] or 0))
            h["hitung_khusus"] = {"agen": agen, "putaran": putaran, "mulai_dari_jam_sejak_diketahui": h["waktu"]["jam_sejak_diketahui"],
                                  "hasil": _JOBS[sid]["hasil"]}
            _simpan(h)
        except Exception as ex:
            _JOBS[sid]["error"] = f"{type(ex).__name__}: {ex}"
        finally:
            _JOBS[sid]["jalan"] = False

    threading.Thread(target=job, daemon=True).start()
    return {"mulai": True, "kode": kode}


def status_hitung(sid: str) -> dict:
    j = _JOBS.get(sid)
    if not j:
        return {"jalan": False, "hasil": (muat_log(sid).get("hitung_khusus") or {}).get("hasil")}
    return {k: v for k, v in j.items() if k != "log"} | {"log": j["log"][-3:]}
