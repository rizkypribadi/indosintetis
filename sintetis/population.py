"""FR-01 Generator populasi Papua & FR-02 peta sinyal per kampung.

Geografi (kabupaten → distrik → kampung) dibaca dari data/kampung.csv bila ada (format Podes yang
sudah dirapikan), atau dibangkitkan sintetis dari config/papua.yaml. Populasi agen kemudian
dialokasikan ke kampung secara proporsional dengan seed tetap.

Tidak ada atribut etnis. Agama tidak dipakai sebagai faktor; kehadiran ibadah bersifat umum.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import TIPE_WILAYAH
from .config import DATA_DIR

SINYAL_4G, SINYAL_LEMAH, SINYAL_TIDAK = 1.0, 0.3, 0.0


@dataclass
class Geography:
    kab_id: list[str]
    kab_nama: list[str]
    kab_pusat: np.ndarray            # (K, 2) lon, lat
    distrik_nama: list[str]
    distrik_kab: np.ndarray          # (D,)
    distrik_tipe: np.ndarray         # (D,) indeks TIPE_WILAYAH
    distrik_lonlat: np.ndarray       # (D, 2)
    kampung_nama: list[str]
    kampung_distrik: np.ndarray      # (V,)
    kampung_lonlat: np.ndarray       # (V, 2)
    kampung_bobot: np.ndarray        # (V,) jumlah penduduk nyata (perkiraan)
    kampung_sinyal: np.ndarray       # (V,) 1.0 = 4G, 0.3 = lemah, 0 = tanpa sinyal
    kampung_pusat_distrik: np.ndarray  # (V,) bool, kampung ibu kota distrik
    sumber: str = "sintetis"
    alias_distrik: dict = field(default_factory=dict)   # (kab_id, nama lama huruf kecil) → indeks distrik

    @property
    def kampung_kab(self) -> np.ndarray:
        return self.distrik_kab[self.kampung_distrik]

    @property
    def kampung_tipe(self) -> np.ndarray:
        return self.distrik_tipe[self.kampung_distrik]

    def kab_index(self, kab_id: str) -> int:
        return self.kab_id.index(kab_id)

    def distrik_index(self, kab_id: str, nama: str) -> int:
        k = self.kab_index(kab_id)
        for d, (n, kk) in enumerate(zip(self.distrik_nama, self.distrik_kab)):
            if kk == k and n.lower() == nama.lower():
                return d
        if (kab_id, nama.lower()) in self.alias_distrik:   # nama distrik sintetis lama di skenario lama
            return self.alias_distrik[(kab_id, nama.lower())]
        raise KeyError(f"Distrik '{nama}' tidak ada di {kab_id}")


@dataclass
class Population:
    n: int
    kampung: np.ndarray          # int32
    distrik: np.ndarray          # int16
    kab: np.ndarray              # int8
    tipe: np.ndarray             # int8
    umur: np.ndarray             # int8
    perempuan: np.ndarray        # bool
    rumah_tangga: np.ndarray     # int32 id rumah tangga
    ortu_sekolah: np.ndarray     # bool: orang tua/wali anak usia sekolah
    anak_sekolah: np.ndarray     # bool
    hp: np.ndarray               # bool: punya HP & memakai WhatsApp
    fb: np.ndarray               # bool: memakai Facebook
    literasi: np.ndarray         # float32 0..1 literasi digital
    sinyal: np.ndarray           # float32 kekuatan sinyal di kampung agen
    extra: dict = field(default_factory=dict)


def build_geography(region: dict, rng: np.random.Generator) -> Geography:
    csv = DATA_DIR / "kampung.csv"
    if csv.exists():
        return _geography_from_csv(region, pd.read_csv(csv))
    return _synthetic_geography(region, rng)


def _synthetic_geography(region: dict, rng: np.random.Generator) -> Geography:
    tipe_idx = {t: i for i, t in enumerate(TIPE_WILAYAH)}
    kab_id, kab_nama, kab_pusat = [], [], []
    d_nama, d_kab, d_tipe, d_ll = [], [], [], []
    v_nama, v_d, v_ll, v_w, v_sig, v_pusat = [], [], [], [], [], []

    for k, kab in enumerate(region["kabupaten"]):
        kab_id.append(kab["id"])
        kab_nama.append(kab["nama"])
        cx, cy = kab["pusat"]
        kab_pusat.append((cx, cy))
        sd = kab["sebaran"]
        n_d = kab["distrik"]
        named = list(kab.get("distrik_kota", []))

        # Urutan tipe distrik: distrik pertama (ibu kota) mendapat tipe "kota" bila ada porsinya.
        tipe_list: list[int] = []
        for t, share in sorted(kab["tipe"].items(), key=lambda x: x[0] != "kota"):
            tipe_list += [tipe_idx[t]] * int(round(share * n_d))
        tipe_list = (tipe_list + [tipe_list[-1]] * n_d)[:n_d]

        # Distrik pusat dekat titik pusat; makin jauh makin acak.
        d_pos = np.empty((n_d, 2))
        d_pos[0] = (cx, cy)
        d_pos[1:] = rng.normal((cx, cy), sd, size=(n_d - 1, 2)) if n_d > 1 else d_pos[1:]
        # Ukuran distrik: ibu kota lebih besar.
        d_w = rng.lognormal(0, 0.5, n_d)
        d_w[0] *= 4 if tipe_list[0] == tipe_idx["kota"] else 2.5
        d_w /= d_w.sum()
        n_v_d = np.maximum(1, np.round(d_w * kab["kampung"]).astype(int))

        base_d = len(d_nama)
        for j in range(n_d):
            d_nama.append(named[j] if j < len(named) else f"{kab['nama']} {j + 1:02d}")
            d_kab.append(k)
            d_tipe.append(tipe_list[j])
            d_ll.append(d_pos[j])

        # Kampung per distrik
        kab_v_start = len(v_nama)
        for j in range(n_d):
            nv = n_v_d[j]
            pos = rng.normal(d_pos[j], sd * 0.28, size=(nv, 2))
            pos[0] = d_pos[j]
            w = rng.lognormal(0, 0.7, nv)
            w[0] *= 3.0                                  # kampung ibu kota distrik lebih padat
            w *= d_w[j] / w.sum()
            for v in range(nv):
                v_nama.append(f"Kampung {d_nama[base_d + j]}-{v + 1:02d}")
                v_d.append(base_d + j)
                v_ll.append(pos[v])
                v_w.append(w[v] * kab["penduduk"])
                v_pusat.append(v == 0)

        # FR-02: sinyal. Proporsi kampung ber-4G = kab.sinyal; kampung pusat & dekat ibu kota didahulukan.
        idx = np.arange(kab_v_start, len(v_nama))
        ll = np.asarray(v_ll)[idx]
        dist = np.hypot(ll[:, 0] - cx, ll[:, 1] - cy) / max(sd, 1e-6)
        score = -dist + rng.normal(0, 0.8, len(idx)) + 2.0 * np.asarray(v_pusat)[idx]
        order = np.argsort(-score)
        n4g = int(round(kab["sinyal"] * len(idx)))
        n_lemah = int(round((len(idx) - n4g) * kab.get("sinyal_lemah", 0.4)))  # porsi sisa kampung bersinyal lemah
        sig = np.full(len(idx), SINYAL_TIDAK)
        sig[order[:n4g]] = SINYAL_4G
        sig[order[n4g:n4g + n_lemah]] = SINYAL_LEMAH
        v_sig.extend(sig.tolist())

    return Geography(
        kab_id=kab_id, kab_nama=kab_nama, kab_pusat=np.asarray(kab_pusat),
        distrik_nama=d_nama, distrik_kab=np.asarray(d_kab, np.int16),
        distrik_tipe=np.asarray(d_tipe, np.int8), distrik_lonlat=np.asarray(d_ll),
        kampung_nama=v_nama, kampung_distrik=np.asarray(v_d, np.int16),
        kampung_lonlat=np.asarray(v_ll), kampung_bobot=np.asarray(v_w),
        kampung_sinyal=np.asarray(v_sig, np.float32), kampung_pusat_distrik=np.asarray(v_pusat),
    )


def _geography_from_csv(region: dict, df: pd.DataFrame) -> Geography:
    """Kolom wajib: kabupaten_id, distrik, kampung, lon, lat, penduduk.
    Opsional: sinyal (4g|lemah|tidak atau 0..1), tipe (kota|pesisir|pedalaman|kepulauan), pusat_distrik (0/1)."""
    tipe_idx = {t: i for i, t in enumerate(TIPE_WILAYAH)}
    kabs = {k["id"]: k for k in region["kabupaten"]}
    kab_id = [k["id"] for k in region["kabupaten"]]
    df = df[df["kabupaten_id"].isin(kab_id)].copy()
    d_keys = list(dict.fromkeys(zip(df["kabupaten_id"], df["distrik"])))
    d_index = {key: i for i, key in enumerate(d_keys)}
    df["d"] = [d_index[(a, b)] for a, b in zip(df["kabupaten_id"], df["distrik"])]

    def tipe_of(kid: str, sub: pd.DataFrame) -> int:
        if "tipe" in sub and sub["tipe"].notna().any():
            return tipe_idx[sub["tipe"].mode().iat[0]]
        return tipe_idx[max(kabs[kid]["tipe"].items(), key=lambda x: x[1])[0]]

    d_kab, d_tipe, d_ll = [], [], []
    for kid, dn in d_keys:
        sub = df[(df["kabupaten_id"] == kid) & (df["distrik"] == dn)]
        d_kab.append(kab_id.index(kid))
        d_tipe.append(tipe_of(kid, sub))
        d_ll.append((sub["lon"].mean(), sub["lat"].mean()))

    def parse_sig(x) -> float:
        if isinstance(x, str):
            return {"4g": SINYAL_4G, "lemah": SINYAL_LEMAH, "tidak": SINYAL_TIDAK}[x.strip().lower()]
        return float(x)

    if "sinyal" in df:
        sig = df["sinyal"].map(parse_sig).to_numpy(np.float32)
    else:
        sig = np.array([kabs[k]["sinyal"] for k in df["kabupaten_id"]], np.float32)
    pusat = df["pusat_distrik"].astype(bool).to_numpy() if "pusat_distrik" in df else (
        ~df.duplicated("d").to_numpy())
    # Pusat kabupaten = kampung berpenduduk terbanyak (dipakai untuk letak stasiun radio dan label).
    kab_pusat = []
    for kid in kab_id:
        sub = df[df["kabupaten_id"] == kid]
        kab_pusat.append(sub.loc[sub["penduduk"].idxmax(), ["lon", "lat"]].to_numpy(float) if len(sub)
                         else kabs[kid]["pusat"])
    # Alias: "<Nama kab> NN" (penamaan sintetis lama) → distrik ke-NN menurut penduduk; plus alias_distrik di papua.yaml.
    d_pop = df.groupby("d")["penduduk"].sum()
    alias = {}
    for k, kid in enumerate(kab_id):
        ds = [d for d in range(len(d_keys)) if d_kab[d] == k]
        for r, d in enumerate(sorted(ds, key=lambda d: -d_pop.get(d, 0))):
            alias[(kid, f"{kabs[kid]['nama']} {r + 1:02d}".lower())] = d
    for kid, peta in (region.get("alias_distrik") or {}).items():
        for lama, baru in peta.items():
            cocok = [d for d, (a, b) in enumerate(d_keys) if a == kid and b.lower() == str(baru).lower()]
            if cocok:
                alias[(kid, str(lama).lower())] = cocok[0]
    return Geography(
        kab_id=kab_id, kab_nama=[k["nama"] for k in region["kabupaten"]],
        kab_pusat=np.asarray(kab_pusat, float), alias_distrik=alias,
        distrik_nama=[d for _, d in d_keys], distrik_kab=np.asarray(d_kab, np.int16),
        distrik_tipe=np.asarray(d_tipe, np.int8), distrik_lonlat=np.asarray(d_ll),
        kampung_nama=df["kampung"].astype(str).tolist(), kampung_distrik=df["d"].to_numpy(np.int16),
        kampung_lonlat=df[["lon", "lat"]].to_numpy(), kampung_bobot=df["penduduk"].to_numpy(float),
        kampung_sinyal=sig, kampung_pusat_distrik=pusat,
        sumber="data/kampung.csv" + (" (data terbuka, lihat geodata_sumber.json)" if "kode_kampung" in df else ""),
    )


def allocate(weights: np.ndarray, n: int) -> np.ndarray:
    """Alokasi sisa terbesar: membagi n agen ke kampung tepat berjumlah n."""
    q = weights / weights.sum() * n
    base = np.floor(q).astype(np.int64)
    rem = n - base.sum()
    if rem > 0:
        base[np.argsort(-(q - base), kind="stable")[:rem]] += 1
    return base


def build_population(geo: Geography, region: dict, n: int, rng: np.random.Generator) -> Population:
    counts = allocate(geo.kampung_bobot, n)
    kampung = np.repeat(np.arange(len(counts), dtype=np.int32), counts)
    distrik = geo.kampung_distrik[kampung]
    kab = geo.distrik_kab[distrik].astype(np.int8)
    tipe = geo.distrik_tipe[distrik].astype(np.int8)
    tipe_cfg = [region["tipe_wilayah"][t] for t in TIPE_WILAYAH]

    # Struktur umur muda (Papua): piramida lebar di bawah.
    umur_bins = np.array([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 75])
    umur_p = np.array([11, 11, 10, 10, 9.5, 9, 8.5, 7.5, 6.5, 5.5, 4.5, 3.5, 2.5, 1.0])
    b = rng.choice(len(umur_p), size=n, p=umur_p / umur_p.sum())
    umur = (umur_bins[b] + rng.integers(0, 5, n)).astype(np.int8)
    perempuan = rng.random(n) < 0.48

    # Rumah tangga: blok berurutan dalam kampung, rata-rata ±4,5 orang.
    rt_size = rng.poisson(3.6, n) + 1
    rt_start = np.cumsum(rt_size)
    rt = np.searchsorted(rt_start, np.arange(n), side="right").astype(np.int32)
    # Rumah tangga tidak melintasi batas kampung.
    pindah = np.r_[False, kampung[1:] != kampung[:-1]]
    rt = (np.cumsum(pindah | np.r_[False, rt[1:] != rt[:-1]])).astype(np.int32)

    anak_sekolah = (umur >= 6) & (umur <= 17)
    rt_ada_anak = np.bincount(rt, weights=anak_sekolah, minlength=rt.max() + 1) > 0
    ortu_sekolah = rt_ada_anak[rt] & (umur >= 22) & (umur <= 65)

    kota = tipe == TIPE_WILAYAH.index("kota")
    hp_base = np.array([c["hp"] for c in tipe_cfg])[tipe]
    umur_f = np.select([umur < 13, umur < 18, umur < 50, umur < 65], [0.0, 0.7, 1.0, 0.65], 0.3)
    sinyal = geo.kampung_sinyal[kampung].astype(np.float32)
    hp = rng.random(n) < hp_base * umur_f * np.where(sinyal > 0, 1.0, 0.35)
    fb = hp & (rng.random(n) < np.where(kota, 0.65, 0.45) * np.where(umur < 45, 1.0, 0.6))

    lit_base = np.array([c["literasi"] for c in tipe_cfg])[tipe]
    lit = lit_base + rng.normal(0, 0.12, n) + np.where(umur < 35, 0.08, -0.05)
    literasi = np.clip(lit, 0.02, 0.98).astype(np.float32)

    return Population(
        n=n, kampung=kampung, distrik=distrik.astype(np.int16), kab=kab, tipe=tipe, umur=umur,
        perempuan=perempuan, rumah_tangga=rt, ortu_sekolah=ortu_sekolah, anak_sekolah=anak_sekolah,
        hp=hp, fb=fb, literasi=literasi, sinyal=sinyal,
    )
