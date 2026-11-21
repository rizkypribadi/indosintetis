"""R2a Pustaka skenario yang sudah dihitung: semua kombinasi skenario penting dihitung JAUH SEBELUM krisis,
sehingga Ruang Situasi (R2b) dan Kartu Respons (R1) menjawab dalam hitungan detik tanpa internet.

Desain penuh: jenis isu (scenarios/templat/*.yaml) × kabupaten asal (9) × kanal awal (wa | fb | tatap_muka)
× ajakan bertindak (ada | tidak) = 270 sel. Setiap sel diuji dengan semua intervensi (bawaan ABCDEFK)
lewat Monte Carlo berseed berpasangan. Sel prioritas (ajakan bertindak, kabupaten kota/pesisir) juga
diberi kurva harga keterlambatan (R3).

Disimpan per versi model (F6) di out/pustaka/<versi>/:
  sel/<id>/mc/        hasil Monte Carlo sel (hasil*.parquet, distrik.parquet, kurva_persentil.parquet)
  sel/<id>/kurva/     kurva keterlambatan (sel prioritas)
  indeks.parquet      sel × intervensi × wilayah (tipe, kabupaten, semua): rata-rata, IK 95%, P10/P50/P90,
                      efek terhadap A, keyakinan
  distrik.parquet     sel × intervensi × distrik: peluang memanas, jam sampai memanas (P10/P50/P90)
  kurva.parquet       sel × intervensi × jam: penyebar & siaga, P10/P50/P90 (semua wilayah)
  keterlambatan.parquet  sel × wilayah × intervensi: harga per jam, batas waktu, kalimat
  manifest.json       versi model, cakupan, tanggal

Bisa dilanjutkan bila terputus: sel yang punya selesai.json tidak dihitung ulang. Bila versi model berubah
(parameter, wilayah, kalibrasi), pustaka lama ditandai USANG dan dashboard menampilkan peringatan.
"""
from __future__ import annotations

import copy
import itertools
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import TIPE_WILAYAH
from .config import OUT_DIR, SCENARIO_DIR, load_interventions, load_params, load_region, load_yaml, validate_scenario, versi_model

TEMPLAT_DIR = SCENARIO_DIR / "templat"
KANAL_AWAL = ("wa", "fb", "tatap_muka")
KANAL_NAMA = {"wa": "WhatsApp", "fb": "Facebook", "tatap_muka": "tatap muka"}
KODE_BAWAAN = "ABCDEFK"
KODE_KURVA = "CDFK"
METRIK_P = ("siaga_jam_per_1000", "jam_ke_eskalasi", "jam_puncak_penyebar", "jam_puncak_siaga", "jangkauan_hoaks")


def akar() -> Path:
    return OUT_DIR / "pustaka"


def muat_templat() -> dict[str, dict]:
    out = {}
    for p in sorted(TEMPLAT_DIR.glob("*.yaml")):
        t = load_yaml(p)
        out[t["jenis"]] = t
    return out


def sel_id(jenis: str, kab: str, kanal: str, ajakan: bool) -> str:
    return f"{jenis}__{kab}__{kanal}__{'ajak' if ajakan else 'tanpa'}"


def urai_sel(sid: str) -> dict:
    j, k, c, a = sid.split("__")
    return {"jenis": j, "kabupaten": k, "kanal": c, "ajakan": a == "ajak"}


def tipe_kab(region: dict | None = None) -> dict[str, str]:
    """Tipe wilayah dominan per kabupaten menurut config/papua.yaml."""
    region = region or load_region()
    return {k["id"]: max(k["tipe"], key=k["tipe"].get) for k in region["kabupaten"]}


def desain(jenis=None, kabupaten=None, kanal=None, ajakan=None, prioritas: bool = False) -> list[str]:
    tmpl = muat_templat()
    region = load_region()
    kabs = [k["id"] for k in region["kabupaten"]]
    tk = tipe_kab(region)
    pilih = lambda semua, f: [x for x in semua if f is None or x in (f if isinstance(f, (list, tuple, set)) else [f])]  # noqa: E731
    out = []
    for j, k, c, a in itertools.product(pilih(list(tmpl), jenis), pilih(kabs, kabupaten), pilih(KANAL_AWAL, kanal),
                                        [x for x in (True, False) if ajakan is None or x == ajakan]):
        if prioritas and not (a and tk[k] in ("kota", "pesisir")):
            continue
        out.append(sel_id(j, k, c, a))
    return out


_GEO: dict = {}


def geografi(seed_dunia: int = 42):
    """Geografi (tanpa populasi) untuk memeriksa nama distrik; disimpan selama proses berjalan."""
    from .config import DATA_DIR
    from .population import build_geography

    csv = DATA_DIR / "kampung.csv"
    key = (seed_dunia, csv.stat().st_mtime if csv.exists() else None)
    if key not in _GEO:
        _GEO.clear()
        _GEO[key] = build_geography(load_region(), np.random.default_rng([seed_dunia, 1]))
    return _GEO[key]


def distrik_sah(kab: str, distrik: str | None) -> str | None:
    """Nama distrik bila ada di geografi (termasuk alias), selain itu None."""
    if not distrik:
        return None
    try:
        geografi().distrik_index(kab, distrik)
        return distrik
    except (KeyError, ValueError):
        return None


def skenario_sel(sid: str, tmpl: dict | None = None, region: dict | None = None, durasi_jam: int = 48,
                 distrik: str | None = None, jeda_deteksi_jam: float | None = None) -> dict:
    """Skenario untuk satu sel pustaka (juga dipakai Ruang Situasi 'Hitung khusus' dan kajian)."""
    c = urai_sel(sid)
    tmpl = tmpl or muat_templat()[c["jenis"]]
    region = region or load_region()
    kab = next(k for k in region["kabupaten"] if k["id"] == c["kabupaten"])
    isu = copy.deepcopy(tmpl["isu"])
    isu["teks"] = isu["teks"].format(lokasi=kab["nama"]) + (tmpl.get("ajakan_teks", "") if c["ajakan"] else "")
    isu["ajakan_tindakan"] = c["ajakan"]
    distrik = distrik_sah(c["kabupaten"], distrik)
    loc = {"kabupaten": c["kabupaten"], **({"distrik": distrik} if distrik else {})}
    tanam = {"wa": {"t_jam": 0, "kanal": "wa", "jenis_grup": "keluarga", "jumlah_grup": 3, **loc},
             "fb": {"t_jam": 0, "kanal": "fb", "akun_medsos": 2, **loc},
             "tatap_muka": {"t_jam": 0, "kanal": "tatap_muka", "jumlah": 10, **loc}}[c["kanal"]]
    sc = {"nama": f"{tmpl['nama']} · {kab['nama']} · {KANAL_NAMA[c['kanal']]} · "
                  f"{'dengan' if c['ajakan'] else 'tanpa'} ajakan bertindak",
          "kode": sid, "mulai": dict(tmpl.get("mulai") or {"hari": "Senin", "jam": 8}), "langkah_menit": 30,
          "durasi_jam": int(tmpl.get("durasi_jam", durasi_jam)), "isu": isu, "tanam": [tanam],
          "pustaka": c, "catatan": "Skenario pustaka (R2a), dibangkitkan dari templat. Fiktif."}
    if jeda_deteksi_jam is not None:
        sc["jeda_deteksi_jam"] = float(jeda_deteksi_jam)
    validate_scenario(sc)
    return sc


def intervensi_sel(sid: str, iv: dict | None = None) -> dict:
    """Intervensi bawaan; tim keliling (jenis tim_keliling) disasarkan ke kabupaten asal isu."""
    iv = copy.deepcopy(iv or load_interventions())
    kab = urai_sel(sid)["kabupaten"]
    for spec in iv.values():
        for s in spec.get("langkah", []):
            if s["jenis"] == "tim_keliling" and not s.get("kabupaten"):
                s["kabupaten"] = kab
    return iv


# ---------------------------------------------------------------------------------------------- membangun
def bangun(agen: int = 20_000, putaran: int = 30, kode: str = KODE_BAWAAN, prioritas: bool = False,
           jenis=None, kabupaten=None, kanal=None, ajakan=None, keterlambatan: bool | None = None,
           jam_kurva: str = "0:12:2", putaran_kurva: int | None = None, seed_dunia: int = 42,
           workers: int | None = None, progress=print, root: str | Path | None = None) -> Path:
    """Hitung sel yang belum selesai lalu konsolidasikan. `keterlambatan` bawaan: hanya sel prioritas."""
    from .keterlambatan import hitung_keterlambatan
    from .montecarlo import run_montecarlo

    say = progress or (lambda s: None)
    versi = versi_model()
    root = Path(root) if root else akar() / versi["id"]
    (root / "sel").mkdir(parents=True, exist_ok=True)
    tmpl, region = muat_templat(), load_region()
    sel = desain(jenis, kabupaten, kanal, ajakan, prioritas)
    tk = tipe_kab(region)
    t0 = time.perf_counter()
    for i, sid in enumerate(sel, 1):
        d = root / "sel" / sid
        if (d / "selesai.json").exists():
            continue
        c = urai_sel(sid)
        sc = skenario_sel(sid, tmpl[c["jenis"]], region)
        iv = intervensi_sel(sid)
        kd = "A" + "".join(k for k in dict.fromkeys(kode) if k != "A" and k in iv)
        say(f"[{i}/{len(sel)}] {sid}: {putaran} putaran × {len(kd)} intervensi, {agen:,} agen".replace(",", "."))
        run_montecarlo(agen, seed_dunia, putaran, kd, sc, iv, workers=workers, progress=None, out_dir=d / "mc")
        prio = c["ajakan"] and tk[c["kabupaten"]] in ("kota", "pesisir")
        if keterlambatan or (keterlambatan is None and prio):
            kk = "".join(k for k in KODE_KURVA if k in kd)
            if kk:
                hitung_keterlambatan(sc, kk, jam_kurva, putaran_kurva or max(10, putaran // 2), agen, seed_dunia,
                                     interventions=iv, workers=workers, out_dir=d / "kurva", progress=None)
        ringkas_sel(d, sid)
        (d / "selesai.json").write_text(json.dumps({"sel": sid, **c, "agen": agen, "putaran": putaran, "kode": kd,
                                                    "kurva": (d / "kurva" / "ringkasan.json").exists(),
                                                    "selesai": datetime.now().isoformat(timespec="seconds"),
                                                    "detik": round(time.perf_counter() - t0, 1)}, ensure_ascii=False),
                                        encoding="utf-8")
    konsolidasi(root, versi, agen=agen, putaran=putaran)
    say(f"Pustaka {root}: {status(root)['selesai']} dari {status(root)['total_desain']} sel")
    return root


def ringkas_sel(d: Path, sid: str) -> None:
    """Ringkasan satu sel (ditulis sekali, dibaca konsolidasi)."""
    from .alur import keyakinan
    from .analysis import paired_effect, ranking, summarize

    mc = d / "mc"
    df = pd.concat([pd.read_parquet(mc / "hasil.parquet"), pd.read_parquet(mc / "hasil_kabupaten.parquet")],
                   ignore_index=True)
    man = json.loads((mc / "manifest.json").read_text(encoding="utf-8"))
    summ, eff, rank = summarize(df), paired_effect(df), ranking(df, n_boot=200)
    summ["tipe"] = summ["tipe"].astype(str)
    q = df.groupby(["tipe", "intervensi"])[list(METRIK_P)].quantile([0.1, 0.5, 0.9]).unstack()
    q.columns = [f"{m}_p{int(p * 100)}" for m, p in q.columns]
    out = summ.merge(q.reset_index(), on=["tipe", "intervensi"], how="left")
    out = out.merge(eff[["tipe", "intervensi", "efek_persen", "selisih_lo", "selisih_hi", "signifikan"]],
                    on=["tipe", "intervensi"], how="left")
    out = out.merge(rank[["tipe", "intervensi", "peringkat", "stabilitas"]], on=["tipe", "intervensi"], how="left")
    base = out[out["intervensi"] == "A"].set_index("tipe")["siaga_jam_per_1000"]
    bt = out["tipe"].map(base).replace(0, np.nan)
    out["efek_lo"] = 100 * out["selisih_lo"] / bt          # IK 95% efek dalam % terhadap tanpa intervensi
    out["efek_hi"] = 100 * out["selisih_hi"] / bt
    out["keyakinan"] = [keyakinan(bool(s) if s == s else False, float(st) if st == st else 0.0)
                        if iv != "A" else None for s, st, iv in zip(out["signifikan"], out["stabilitas"], out["intervensi"])]
    out["tingkat"] = np.where(out["tipe"].isin([*TIPE_WILAYAH, "semua"]), "tipe", "kabupaten")
    out["nama_intervensi"] = out["intervensi"].map(man["intervensi"])
    c = urai_sel(sid)
    out.insert(0, "sel", sid)
    for k, v in c.items():
        out.insert(1, k if k != "kabupaten" else "kabupaten_asal", v)
    out["putaran"] = man["putaran"]
    out["agen"] = man["agen"]
    out.to_parquet(d / "ringkas_indeks.parquet", index=False)
    dist = pd.read_parquet(mc / "distrik.parquet")
    dist.insert(0, "sel", sid)
    dist.to_parquet(d / "ringkas_distrik.parquet", index=False)
    kp = pd.read_parquet(mc / "kurva_persentil.parquet")
    kp = kp[kp["wilayah"] == "semua"].copy()
    kp["jam"] = kp["langkah"] * float(man["isi_skenario"].get("langkah_menit", 30)) / 60
    kp.insert(0, "sel", sid)
    kp.to_parquet(d / "ringkas_kurva.parquet", index=False)
    kt = d / "kurva" / "ringkasan.json"
    rows = []
    if kt.exists():
        h = json.loads(kt.read_text(encoding="utf-8"))
        for wil, per in h["ringkasan"].items():
            for kode, r in per.items():
                rows.append({"sel": sid, "wilayah": wil, "kode": kode, **{k: r.get(k) for k in (
                    "nama", "jenis", "harga_per_jam_persen", "harga_lo", "harga_hi", "batas_waktu_jam",
                    "melewati_rentang", "jam_maks", "efek_jam0_persen", "keyakinan", "kalimat")},
                    "peringatan": "; ".join(r.get("peringatan") or [])})
    pd.DataFrame(rows).to_parquet(d / "ringkas_keterlambatan.parquet", index=False)


def konsolidasi(root: Path, versi: dict | None = None, **info) -> dict:
    root = Path(root)
    parts = {k: [] for k in ("indeks", "distrik", "kurva", "keterlambatan")}
    selesai = []
    for d in sorted((root / "sel").glob("*/selesai.json")):
        selesai.append(json.loads(d.read_text(encoding="utf-8")))
        for k in parts:
            p = d.parent / f"ringkas_{k}.parquet"
            if p.exists():
                x = pd.read_parquet(p)
                if len(x):
                    parts[k].append(x)
    for k, v in parts.items():
        (pd.concat(v, ignore_index=True) if v else pd.DataFrame()).to_parquet(root / f"{k}.parquet", index=False)
    lama = json.loads((root / "manifest.json").read_text(encoding="utf-8")) if (root / "manifest.json").exists() else {}
    versi = versi or lama.get("versi_model") or versi_model()
    man = {"versi_model": versi, "dibuat": lama.get("dibuat") or datetime.now().isoformat(timespec="seconds"),
           "diperbarui": datetime.now().isoformat(timespec="seconds"), "total_desain": len(desain()),
           "selesai": len(selesai), "agen": sorted({s["agen"] for s in selesai}),
           "putaran": sorted({s["putaran"] for s in selesai}), "sel_berkurva": sum(bool(s.get("kurva")) for s in selesai),
           "jenis_isu": sorted({s["jenis"] for s in selesai}), "kabupaten": sorted({s["kabupaten"] for s in selesai}),
           "catatan": "SIMULASI — BUKAN PRAKIRAAN. Angka dari warga fiktif; pakai untuk memilih langkah, bukan meramal."}
    (root / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    return man


# ---------------------------------------------------------------------------------------------- status & muat
def daftar() -> list[Path]:
    a = akar()
    return sorted((p.parent for p in a.glob("*/manifest.json")), key=lambda p: p.stat().st_mtime, reverse=True) if a.exists() else []


def status(root: str | Path | None = None) -> dict:
    """Cakupan, versi, usang/tidak, dan sel yang belum dihitung."""
    kini = versi_model()
    if root is None:
        ada = daftar()
        root = next((p for p in ada if p.name == kini["id"]), ada[0] if ada else akar() / kini["id"])
    root = Path(root)
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8")) if (root / "manifest.json").exists() else {}
    selesai = {p.parent.name for p in (root / "sel").glob("*/selesai.json")} if (root / "sel").exists() else set()
    semua = desain()
    belum = [s for s in semua if s not in selesai]
    pid = (man.get("versi_model") or {}).get("id", root.name)
    return {"folder": str(root), "versi": pid, "versi_sekarang": kini["id"], "usang": bool(man) and pid != kini["id"],
            "ada": bool(man), "selesai": len(selesai), "total_desain": len(semua), "belum": belum,
            "prioritas_belum": [s for s in desain(prioritas=True) if s not in selesai],
            "agen": man.get("agen"), "putaran": man.get("putaran"), "sel_berkurva": man.get("sel_berkurva", 0),
            "diperbarui": man.get("diperbarui"), "status_validasi": (man.get("versi_model") or {}).get("status_validasi")}


_CACHE: dict = {}


def muat(root: str | Path | None = None) -> dict | None:
    """Muat pustaka (versi sekarang bila ada, kalau tidak yang terbaru dan ditandai usang)."""
    st = status(root)
    r = Path(st["folder"])
    if not st["ada"] or not (r / "indeks.parquet").exists():
        return None
    key = (str(r), (r / "manifest.json").stat().st_mtime)
    if _CACHE.get("key") != key:
        _CACHE.clear()
        _CACHE["key"] = key
        _CACHE["data"] = {k: pd.read_parquet(r / f"{k}.parquet") if (r / f"{k}.parquet").exists() else pd.DataFrame()
                          for k in ("indeks", "distrik", "kurva", "keterlambatan")}
        _CACHE["manifest"] = json.loads((r / "manifest.json").read_text(encoding="utf-8"))
    return {**_CACHE["data"], "manifest": _CACHE["manifest"], "status": st}


def teks_status(st: dict) -> str:
    if not st["ada"]:
        return f"Belum ada pustaka untuk versi {st['versi_sekarang']}. Jalankan: python -m sintetis pustaka bangun"
    s = (f"Pustaka {st['versi']}: {st['selesai']}/{st['total_desain']} sel selesai, {st['sel_berkurva']} berkurva "
         f"keterlambatan; agen {st['agen']}, putaran {st['putaran']}; diperbarui {st['diperbarui']}.")
    if st["usang"]:
        s += f"\nUSANG: versi model sekarang {st['versi_sekarang']}. Bangun ulang sebelum dipakai di lapangan."
    if st["prioritas_belum"]:
        s += f"\nSel prioritas yang belum dihitung: {len(st['prioritas_belum'])} (mis. {', '.join(st['prioritas_belum'][:3])})"
    return s


__all__ = ["bangun", "status", "muat", "desain", "skenario_sel", "intervensi_sel", "muat_templat", "load_params"]
