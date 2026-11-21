"""R4 Perencana intervensi dengan keterbatasan: "Dengan orang, waktu, dan sarana yang kami punya sekarang,
apa gabungan langkah terbaik?"

  1. Bangkitkan rencana yang LAYAK dari profil sumber daya (config/sumber_daya/<kabupaten>.yaml): setiap
     sumber daya dipakai atau tidak, pada jam paling cepatnya. Kapasitas dan batas waktu tidak pernah dilanggar
     karena rencana hanya disusun dari isian profil.
  2. Penyaringan cepat: semua kandidat × ±10 seed berpasangan.
  3. Penyempurnaan bertahap (successive halving): separuh terbaik diberi seed tambahan sampai tersisa 3.
  4. Uji ketahanan: 3 rencana teratas + tanpa tindakan diuji pada sampel Latin hypercube atas rentang
     parameter di config/sensitivitas.yaml; dilaporkan seberapa sering peringkatnya bertahan.
  5. Sumber daya paling berharga: nilai tiap sumber daya di rencana terbaik (dibandingkan rencana yang berbeda
     satu sumber daya) dan nilai menambah kapasitas, dihitung di Python.

Metrik: beban siaga aktif (orang-jam per 1.000) di kabupaten sasaran. Penindakan tidak pernah disarankan
terhadap orang atau kelompok tertentu.
"""
from __future__ import annotations

import copy
import itertools
import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import qmc

from .analysis import mean_ci
from .config import CONFIG_DIR, OUT_DIR, _set_dotted, load_params, load_region, load_scenario, load_yaml, versi_model

TINGKAT = {"rendah": 0.6, "sedang": 1.0, "tinggi": 1.3}
METRIK = "siaga_jam_per_1000"


def muat_profil(p: str | Path | dict) -> dict:
    if isinstance(p, dict):
        prof = copy.deepcopy(p)
    else:
        path = Path(p)
        if not path.exists():
            path = CONFIG_DIR / "sumber_daya" / f"{p}.yaml"
        prof = load_yaml(path)
    kabs = [k["id"] for k in load_region()["kabupaten"]]
    if prof.get("kabupaten") not in kabs:
        raise ValueError(f"profil.kabupaten harus salah satu dari {kabs}")
    for k, v in (prof.get("kepercayaan") or {}).items():
        if v not in TINGKAT:
            raise ValueError(f"kepercayaan.{k} harus rendah | sedang | tinggi")
    return prof


def _peng(prof: dict, *kunci: str) -> float:
    kp = prof.get("kepercayaan") or {}
    v = [TINGKAT[kp[k]] for k in kunci if k in kp]
    return float(np.mean(v)) if v else 1.0


def opsi(prof: dict) -> list[dict]:
    """Daftar sumber daya yang bisa dipakai (masing-masing satu langkah intervensi) dalam batas waktu."""
    kab = prof["kabupaten"]
    batas = float(prof.get("batas_waktu_jam", 24))
    out = []
    for i, t in enumerate(prof.get("tokoh") or []):
        loc = {"kabupaten": kab, **({"distrik": t["distrik"]} if t.get("distrik") else {})}
        out.append({"id": f"tokoh{i + 1}", "jenis": "tokoh", "jam": float(t.get("jam_paling_cepat", 0)),
                    "label": f"{t['jumlah']} tokoh gereja/adat" + (f" di {', '.join(t['distrik'])}" if t.get("distrik") else ""),
                    "spec": {"jenis": "gereja_adat", "jumlah_tokoh": int(t["jumlah"]), **loc,
                             "pengali_kepercayaan": _peng(prof, "gereja", "adat")}})
    nama_st = {x["id"]: x["nama"] for x in load_region().get("radio", [])}
    for r in prof.get("radio") or []:
        out.append({"id": f"radio_{r['stasiun']}", "jenis": "radio", "jam": float(r.get("jam_paling_cepat", 0)),
                    "label": f"siaran {nama_st.get(r['stasiun'], r['stasiun'])} ({r.get('durasi_jam', 24):g} jam)",
                    "spec": {"jenis": "radio", "stasiun": [r["stasiun"]], "durasi_jam": float(r.get("durasi_jam", 24)),
                             "pengali_kepercayaan": _peng(prof, "radio", "pemerintah")}})
    m = prof.get("medsos") or {}
    if m.get("ada"):
        out.append({"id": "medsos", "jenis": "medsos", "jam": float(m.get("jam_paling_cepat", 0)),
                    "label": f"akun resmi medsos (jangkauan ±{float(m.get('jangkauan', 0.35)):.0%})",
                    "spec": {"jenis": "medsos_resmi", "kabupaten": kab, "durasi_jam": float(m.get("durasi_jam", 24)),
                             "jangkauan": float(m.get("jangkauan", 0.35)), "pengali_kepercayaan": _peng(prof, "pemerintah")}})
    tk = prof.get("tim_keliling") or {}
    if tk.get("orang"):
        loc = {"kabupaten": kab, **({"distrik": tk["distrik"]} if tk.get("distrik") else {})}
        out.append({"id": "tim", "jenis": "tim", "jam": float(tk.get("jam_paling_cepat", 0)),
                    "label": f"tim keliling {tk['orang']} orang (±{tk['orang'] * float(tk.get('kampung_per_jam', 0.5)):g} kampung/jam)",
                    "spec": {"jenis": "tim_keliling", "orang": int(tk["orang"]), "kampung_per_jam": float(tk.get("kampung_per_jam", 0.5)),
                             "durasi_jam": float(tk.get("durasi_jam", 24)), **loc,
                             "pengali_kepercayaan": _peng(prof, "tim_lapangan", "pemerintah")}})
    return [o for o in out if o["jam"] <= batas]


def rencana_layak(ops: list[dict], maks: int = 64) -> dict[str, dict]:
    """Semua gabungan tak kosong dari opsi (dipotong ke `maks`, gabungan terbesar didahulukan agar rencana
    'pakai semua' selalu ikut). Kode P01, P02, ..."""
    combos = [c for r in range(len(ops), 0, -1) for c in itertools.combinations(range(len(ops)), r)][:maks]
    out = {}
    for i, c in enumerate(combos, 1):
        langkah = []
        for j in c:
            s = dict(ops[j]["spec"])
            s["t_sejak_deteksi"] = ops[j]["jam"]
            langkah.append(s)
        out[f"P{i:02d}"] = {"nama": " + ".join(ops[j]["label"] for j in c), "langkah": langkah,
                            "opsi": [ops[j]["id"] for j in c], "jam": [ops[j]["jam"] for j in c]}
    return out


def periksa_rencana(r: dict, prof: dict) -> list[str]:
    """Pelanggaran kapasitas/batas waktu (harus kosong untuk semua rencana yang dibangkitkan)."""
    salah = []
    batas = float(prof.get("batas_waktu_jam", 24))
    tok = sum(int(t["jumlah"]) for t in prof.get("tokoh") or [])
    tim = int((prof.get("tim_keliling") or {}).get("orang", 0))
    if sum(s.get("jumlah_tokoh", 0) for s in r["langkah"]) > tok:
        salah.append("tokoh melebihi kapasitas")
    if sum(s.get("orang", 0) for s in r["langkah"] if s["jenis"] == "tim_keliling") > tim:
        salah.append("tim keliling melebihi kapasitas")
    if any(float(s.get("t_sejak_deteksi", 0)) > batas for s in r["langkah"]):
        salah.append("melewati batas waktu")
    st = {x["stasiun"] for x in prof.get("radio") or []}
    if any(set(s.get("stasiun", [])) - st for s in r["langkah"] if s["jenis"] == "radio"):
        salah.append("stasiun radio di luar profil")
    return salah


def _skor(tabel: pd.DataFrame, kab: str, kode: list[str]) -> pd.DataFrame:
    g = tabel[tabel["tipe"] == kab].pivot_table(index="seed", columns="intervensi", values=METRIK)
    rows = []
    for k in kode:
        if k not in g:
            continue
        x = g[k].dropna().to_numpy(float)
        a = g.loc[g[k].notna(), "A"].to_numpy(float)
        mu, lo, hi = mean_ci(x - a)
        b = float(a.mean()) if len(a) else np.nan
        rows.append({"kode": k, "nilai": float(x.mean()), "selisih": mu, "selisih_lo": lo, "selisih_hi": hi,
                     "efek_persen": 100 * mu / b if b else np.nan, "efek_lo": 100 * lo / b if b else np.nan,
                     "efek_hi": 100 * hi / b if b else np.nan, "seed": len(x)})
    return pd.DataFrame(rows).sort_values("nilai").reset_index(drop=True)


def rencanakan(skenario: dict | str | Path | None, profil: str | Path | dict, agen: int = 20_000, seed_dunia: int = 42,
               putaran_awal: int = 10, sisa: int = 3, sampel_ketahanan: int = 8, seed_ketahanan: int = 2,
               workers: int | None = None, nama: str | None = None, progress=print, maks_rencana: int = 64) -> dict:
    from .alur import keyakinan
    from .montecarlo import run_montecarlo

    say = progress or (lambda s: None)
    t_mulai = time.perf_counter()
    prof = muat_profil(profil)
    kab = prof["kabupaten"]
    sc = copy.deepcopy(skenario if isinstance(skenario, dict) else load_scenario(skenario))
    if prof.get("tanpa_sinyal"):
        sc["tanpa_sinyal"] = list(sc.get("tanpa_sinyal") or []) + [{"kabupaten": kab, "distrik": prof["tanpa_sinyal"]}]
    ops = opsi(prof)
    if not ops:
        raise ValueError("Tidak ada sumber daya yang bisa dipakai dalam batas waktu")
    cand = rencana_layak(ops, maks_rencana)
    for k, r in cand.items():
        assert not periksa_rencana(r, prof), (k, periksa_rencana(r, prof))
    ivs = {"A": {"nama": "Tanpa tindakan", "langkah": []}, **{k: {"nama": v["nama"], "langkah": v["langkah"]} for k, v in cand.items()}}
    out = OUT_DIR / (nama or f"perencana_{kab}_{datetime.now():%Y%m%d-%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)
    tabel = []

    def jalan(kode: list[str], seeds: list[int], sub: str, params=None) -> pd.DataFrame:
        d = run_montecarlo(agen, seed_dunia, len(seeds), ["A", *kode], sc, ivs, params=params, workers=workers,
                           progress=None, seeds=seeds, out_dir=out / sub)
        return pd.read_parquet(d / "hasil_kabupaten.parquet")

    # 2–3. penyaringan & successive halving -----------------------------------------------------------
    hidup, seeds, ronde, riwayat = list(cand), list(range(putaran_awal)), 0, []
    say(f"Perencana {kab}: {len(cand)} rencana layak dari {len(ops)} sumber daya")
    while True:
        ronde += 1
        say(f"  ronde {ronde}: {len(hidup)} rencana × {len(seeds)} seed baru")
        tabel.append(jalan(hidup, seeds, f"ronde{ronde}"))
        T = pd.concat(tabel, ignore_index=True).drop_duplicates(["intervensi", "seed", "tipe"])
        sk = _skor(T, kab, hidup)
        riwayat.append({"ronde": ronde, "rencana": len(hidup), "seed_total": int(sk["seed"].max())})
        if len(hidup) <= sisa:
            break
        hidup = sk["kode"].head(max(sisa, math.ceil(len(hidup) / 2))).tolist()
        n0 = max(T["seed"]) + 1
        seeds = list(range(n0, n0 + putaran_awal * 2 ** ronde // 2))
    T = pd.concat(tabel, ignore_index=True).drop_duplicates(["intervensi", "seed", "tipe"])
    akhir = _skor(T, kab, hidup).head(sisa)

    # 4. uji ketahanan --------------------------------------------------------------------------------
    top = akhir["kode"].tolist()
    stabil = {k: {"peringkat_sama": 0, "lebih_baik_dari_A": 0} for k in top}
    sampel = []
    if sampel_ketahanan > 0:
        rentang = load_yaml(CONFIG_DIR / "sensitivitas.yaml")["rentang"]
        keys = list(rentang)
        lo_, hi_ = np.array([rentang[k][0] for k in keys]), np.array([rentang[k][1] for k in keys])
        theta = qmc.scale(qmc.LatinHypercube(len(keys), seed=seed_dunia).random(sampel_ketahanan), lo_, hi_)
        base = load_params()
        for i, row in enumerate(theta):
            params = copy.deepcopy(base)
            for k, v in zip(keys, row):
                _set_dotted(params, k, float(v))
            say(f"  ketahanan {i + 1}/{sampel_ketahanan}")
            s = _skor(jalan(top, list(range(seed_ketahanan)), f"tahan{i + 1}", params), kab, top)
            urut = s["kode"].tolist()
            for j, k in enumerate(top):
                stabil[k]["peringkat_sama"] += int(urut.index(k) == j) if k in urut else 0
                stabil[k]["lebih_baik_dari_A"] += int(s.loc[s["kode"] == k, "selisih"].iat[0] < 0)
            sampel.append({k: float(v) for k, v in zip(keys, row)} | {"urutan": urut})
        for k in stabil:
            stabil[k] = {kk: v / sampel_ketahanan for kk, v in stabil[k].items()}

    # 5. sumber daya paling berharga -------------------------------------------------------------------
    best = top[0]
    # seed BARU (tidak dipakai saat seleksi) agar rencana terbaik tidak tampak terlalu bagus (winner's curse)
    seed_baru = list(range(10_000, 10_000 + 2 * putaran_awal))
    berharga, tambah = uji_sumber_daya(best, cand, prof, ops, ivs, jalan, kab, seed_baru, say)
    kalimat = kalimat_berharga(berharga, tambah)

    rencana = []
    for _, r in akhir.iterrows():
        k = r["kode"]
        sig = bool(r["selisih_hi"] < 0)
        st = stabil.get(k, {}).get("peringkat_sama", 1.0) if sampel_ketahanan else 1.0
        rencana.append({
            "kode": k, "nama": cand[k]["nama"],
            "langkah": [{"opsi": o, "label": next(x["label"] for x in ops if x["id"] == o), "jam_sejak_diketahui": j}
                        for o, j in sorted(zip(cand[k]["opsi"], cand[k]["jam"]), key=lambda x: x[1])],
            "spec": {"nama": cand[k]["nama"], "langkah": cand[k]["langkah"]},
            "beban_siaga": _r(r["nilai"]), "efek_persen": _r(r["efek_persen"]), "efek_rentang": [_r(r["efek_lo"]), _r(r["efek_hi"])],
            "seed": int(r["seed"]), "keyakinan": keyakinan(sig, st),
            "ketahanan": stabil.get(k), "peringatan": ("Peringkat tidak stabil bila parameter digeser: rencana ini mungkin "
                                                       "menang karena celah model." if sampel_ketahanan and st < 0.5 else None)})
    hasil = {"kabupaten": kab, "profil": prof, "skenario": sc.get("nama"), "agen": agen, "versi_model": versi_model(),
             "jumlah_rencana_layak": len(cand), "ronde": riwayat, "rencana": rencana, "sumber_daya": berharga,
             "tambah_kapasitas": tambah, "kalimat_sumber_daya": kalimat, "sampel_ketahanan": sampel_ketahanan,
             "detik": round(time.perf_counter() - t_mulai, 1), "folder": out.name,
             "pelanggaran": {k: periksa_rencana(r, prof) for k, r in cand.items() if periksa_rencana(r, prof)}}
    (out / "rencana.json").write_text(json.dumps(hasil, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return hasil


def uji_sumber_daya(best: str, cand: dict, prof: dict, ops: list[dict], ivs: dict, jalan, kab: str, seeds: list[int],
                    say) -> tuple[list[dict], list[dict]]:
    """Pada seed baru: (1) nilai tiap sumber daya di rencana terbaik = kenaikan beban siaga bila sumber daya itu
    dilepas; (2) variasi dengan kapasitas lebih: +2 tokoh, +2 orang tim, +10 poin jangkauan medsos, slot radio kedua."""
    from .config import load_region

    set_best = set(cand[best]["opsi"])
    kurang = {o: next((k for k, r in cand.items() if set(r["opsi"]) == set_best - {o}), None) for o in sorted(set_best)}
    kurang = {o: k for o, k in kurang.items() if k}

    spec = copy.deepcopy(cand[best]["langkah"])
    var = {}
    for i, s in enumerate(spec):
        t = copy.deepcopy(spec)
        if s["jenis"] == "gereja_adat":
            t[i]["jumlah_tokoh"] += 2
            var["X_tokoh"] = ("menambah 2 tokoh gereja/adat", t)
        elif s["jenis"] == "tim_keliling":
            t[i]["orang"] += 2
            var["X_tim"] = ("menambah 2 orang tim keliling", t)
        elif s["jenis"] == "medsos_resmi":
            t[i]["jangkauan"] = min(0.95, t[i]["jangkauan"] + 0.1)
            var["X_medsos"] = ("menaikkan jangkauan medsos resmi 10 poin", t)
        elif s["jenis"] == "radio":
            lain = [r for r in load_region().get("radio", []) if kab in r["jangkauan"] and r["id"] not in s["stasiun"]]
            if lain:
                t[i]["stasiun"] = s["stasiun"] + [lain[0]["id"]]
                var["X_radio"] = (f"menambah siaran di {lain[0]['nama']}", t)
            else:
                t.append({**s, "t_sejak_deteksi": float(s["t_sejak_deteksi"]) + 12})
                var["X_radio"] = ("slot siaran radio kedua (12 jam kemudian)", t)
    for k, (lbl, t) in var.items():
        ivs[k] = {"nama": lbl, "langkah": t}
    kode = list(dict.fromkeys([best, *kurang.values(), *var]))
    say(f"  nilai sumber daya: {len(kurang)} dilepas, {len(var)} ditambah, {len(seeds)} seed baru")
    T = jalan(kode, seeds, "sumber_daya")
    sk = _skor(T, kab, kode).set_index("kode")
    nb = float(sk.loc[best, "nilai"])
    berharga = []
    for o, k in kurang.items():
        naik = float(sk.loc[k, "nilai"]) - nb
        berharga.append({"opsi": o, "label": next(x["label"] for x in ops if x["id"] == o), "naik_bila_dilepas": _r(naik),
                         "naik_persen": _r(100 * naik / max(nb, 1e-9))})
    tambah = []
    for k, (lbl, _) in var.items():
        turun = nb - float(sk.loc[k, "nilai"])
        tambah.append({"variasi": k, "label": lbl, "turun": _r(turun), "turun_persen": _r(100 * turun / max(nb, 1e-9))})
    return (sorted(berharga, key=lambda x: -(x["naik_bila_dilepas"] or 0)),
            sorted(tambah, key=lambda x: -(x["turun"] or 0)))


def kalimat_berharga(berharga: list[dict], tambah: list[dict]) -> list[str]:
    out = []
    if tambah and (tambah[0]["turun"] or 0) > 0:
        a = tambah[0]
        if len(tambah) >= 2:
            b = tambah[-1]
            pb = f"±{b['turun_persen']:.0f}%" if (b["turun"] or 0) > 0 else "tidak terlihat"
            out.append(f"{a['label'].capitalize()} lebih berguna daripada {b['label']} "
                       f"(beban siaga turun ±{a['turun_persen']:.0f}% vs {pb}).")
        else:
            out.append(f"{a['label'].capitalize()} menurunkan beban siaga ±{a['turun_persen']:.0f}% lagi.")
    elif tambah:
        out.append("Menambah kapasitas sumber daya yang ada belum terlihat menurunkan beban siaga pada jumlah putaran ini.")
    if berharga and (berharga[0]["naik_bila_dilepas"] or 0) > 0:
        b = berharga[0]
        out.append(f"Sumber daya paling penting di rencana terbaik: {b['label']} (bila dilepas, beban siaga naik ±{b['naik_persen']:.0f}%).")
    if not out:
        out.append("Belum terlihat perbedaan jelas antar-sumber daya pada jumlah putaran ini.")
    return [s.replace("-", "−") for s in out]


def simpan_rencana(hasil: dict, kode: str | None = None, nama: str | None = None) -> Path:
    """Simpan satu rencana sebagai berkas intervensi (dipakai Kartu dan Latihan Meja): out/rencana/<nama>.yaml."""
    r = next(x for x in hasil["rencana"] if kode in (None, x["kode"]))
    d = OUT_DIR / "rencana"
    d.mkdir(parents=True, exist_ok=True)
    nama = nama or f"rencana_{hasil['kabupaten']}_{datetime.now():%Y%m%d-%H%M%S}"
    isi = {"kabupaten": hasil["kabupaten"], "nama": r["nama"], "langkah": r["spec"]["langkah"],
           "efek_persen": r["efek_persen"], "efek_rentang": r["efek_rentang"], "keyakinan": r["keyakinan"],
           "versi_model": hasil["versi_model"]["id"], "disimpan": datetime.now().isoformat(timespec="seconds")}
    p = d / f"{nama}.yaml"
    p.write_text("# Rencana dari Perencana intervensi (R4). Jam = sejak isu diketahui.\n"
                 + yaml.safe_dump(isi, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def rencana_tersimpan(kab: str | None = None) -> list[dict]:
    d = OUT_DIR / "rencana"
    out = []
    for p in sorted(d.glob("*.yaml"), key=lambda x: x.stat().st_mtime, reverse=True) if d.exists() else []:
        r = load_yaml(p)
        if kab is None or r.get("kabupaten") == kab:
            out.append({"berkas": p.name, **r})
    return out


def _r(x, n=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)
