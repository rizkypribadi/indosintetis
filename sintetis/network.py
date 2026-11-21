"""FR-03 Generator jaringan empat kanal: WhatsApp, Facebook, tatap muka, radio.

Jaringan disimpan sebagai graf bipartit agen × kelompok (matriks keanggotaan jarang M). Setiap
kelompok — grup WA, grup FB, rumah ibadah, pasar, pertemuan adat, rumah tangga, pangkalan ojek,
rombongan perjalanan antarpulau, pendengar stasiun radio — punya kanal, kepercayaan relasi,
peluang baca/kontak, dan jadwal. Penyebaran per langkah cukup dua perkalian matriks-vektor
(M^T a lalu M g), sehingga skala 1 juta agen tetap ringan.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp

from . import K_F2F, K_FB, K_RADIO, K_WA, TIPE_WILAYAH
from .population import Geography, Population

# Jenis kelompok: (nama, kanal, kunci kepercayaan, kontak per langkah, jadwal)
JENIS = [
    ("rumah_tangga", K_F2F, "rumah_tangga", 0.0, "selalu"),   # kontak 0 = semua anggota (baca 0,9)
    ("tetangga", K_F2F, "tetangga", 3.0, "selalu"),
    ("ibadah", K_F2F, "ibadah", 6.0, "ibadah"),
    ("pasar", K_F2F, "pasar", 5.0, "pasar"),
    ("adat", K_F2F, "adat", 8.0, "adat"),
    ("pangkalan", K_F2F, "pangkalan", 5.0, "pangkalan"),
    ("perjalanan", K_F2F, "tetangga", 3.0, "selalu"),
    ("wa_keluarga", K_WA, "keluarga", -0.5, "selalu"),        # negatif = peluang baca tetap
    ("wa_marga", K_WA, "marga", -0.35, "selalu"),
    ("wa_jemaat", K_WA, "jemaat", -0.35, "selalu"),
    ("wa_sekolah", K_WA, "sekolah", -0.45, "selalu"),
    ("fb_info", K_FB, "info_kota", 25.0, "selalu"),
    ("radio", K_RADIO, "radio", -1.0, "radio_dengar"),
]
JENIS_NAMA = [j[0] for j in JENIS]
J = {n: i for i, n in enumerate(JENIS_NAMA)}
JADWAL = ["selalu", "ibadah", "pasar", "adat", "pangkalan", "radio_dengar"]


@dataclass
class Network:
    M: sp.csr_matrix          # agen × kelompok (0/1)
    MT: sp.csr_matrix         # transpos, kelompok × agen
    jenis: np.ndarray         # (G,) int8
    kanal: np.ndarray         # (G,) int8
    kepercayaan: np.ndarray   # (G,) float32
    baca: np.ndarray          # (G,) float32 peluang anggota menerima pesan per penyebar-langkah
    jadwal: np.ndarray        # (G,) int8
    distrik: np.ndarray       # (G,) int16 distrik "rumah" kelompok
    kampung: np.ndarray       # (G,) int32 kampung titik temu (untuk visual)
    ukuran: np.ndarray        # (G,) int32
    hari_pasar: np.ndarray    # (D, 7) bool hari pasar per distrik
    radio_stasiun: list[str]  # nama stasiun untuk kelompok radio (urut kemunculan)
    radio_group: np.ndarray   # (R,) indeks kelompok tiap stasiun

    @property
    def G(self) -> int:
        return self.M.shape[1]

    def groups_of(self, agent: int) -> np.ndarray:
        return self.M.indices[self.M.indptr[agent]:self.M.indptr[agent + 1]]

    def members_of(self, group: int) -> np.ndarray:
        return self.MT.indices[self.MT.indptr[group]:self.MT.indptr[group + 1]]


class _Builder:
    def __init__(self, n: int):
        self.n = n
        self.rows: list[np.ndarray] = []
        self.cols: list[np.ndarray] = []
        self.g_jenis: list[np.ndarray] = []
        self.g_distrik: list[np.ndarray] = []
        self.g_kampung: list[np.ndarray] = []
        self.G = 0

    def add(self, jenis: str, agents: np.ndarray, keys: np.ndarray, distrik_of_agent: np.ndarray,
            kampung_of_agent: np.ndarray) -> np.ndarray:
        """Tambah kelompok dari pasangan (agen, kunci kelompok). Mengembalikan id global per kunci unik."""
        if len(agents) == 0:
            return np.empty(0, np.int64)
        uniq, inv, first = _factorize(keys)
        gid = self.G + np.arange(len(uniq))
        self.rows.append(agents)
        self.cols.append(gid[inv])
        self.g_jenis.append(np.full(len(uniq), J[jenis], np.int8))
        self.g_distrik.append(distrik_of_agent[agents[first]])
        self.g_kampung.append(kampung_of_agent[agents[first]])
        self.G += len(uniq)
        return gid


def _factorize(keys: np.ndarray):
    uniq, first, inv = np.unique(keys, return_index=True, return_inverse=True)
    return uniq, inv, first


def _chunks(sizes_key: np.ndarray, target: int, rng: np.random.Generator) -> np.ndarray:
    """Pecah anggota per kunci menjadi potongan ±target orang secara acak."""
    _, inv, counts = np.unique(sizes_key, return_inverse=True, return_counts=True)
    n_chunk = np.maximum(1, np.round(counts / target)).astype(np.int64)
    return (rng.random(len(sizes_key)) * n_chunk[inv]).astype(np.int64)


def build_network(pop: Population, geo: Geography, region: dict, params: dict,
                  rng: np.random.Generator) -> Network:
    n = pop.n
    b = _Builder(n)
    ids = np.arange(n, dtype=np.int64)
    dis, kam = pop.distrik.astype(np.int64), pop.kampung.astype(np.int64)
    dewasa = pop.umur >= 15
    kota = pop.tipe == TIPE_WILAYAH.index("kota")

    # --- Tatap muka ---------------------------------------------------------------------------
    b.add("rumah_tangga", ids, pop.rumah_tangga.astype(np.int64), dis, kam)
    b.add("tetangga", ids, kam * 100_000 + pop.rumah_tangga // 6, dis, kam)

    # Rumah ibadah: ±1 per 400 jiwa di kampung; satu rumah tangga satu jemaat.
    kam_pop = np.bincount(kam, minlength=len(geo.kampung_nama))
    n_ib = np.maximum(1, np.ceil(kam_pop / 400)).astype(np.int64)
    rt_rand = _hash_unit(pop.rumah_tangga, 11)
    jemaat_local = (rt_rand * n_ib[kam]).astype(np.int64)
    jemaat = kam * 10_000 + jemaat_local
    hadir = (pop.umur >= 4) & (rng.random(n) < 0.85)
    b.add("ibadah", ids[hadir], jemaat[hadir], dis, kam)

    # Pasar: satu per distrik; mama-mama lebih sering ke pasar.
    p_pasar = np.where(pop.perempuan, 0.45, 0.2) * dewasa * np.where(geo.kampung_pusat_distrik[kam], 1.3, 1.0)
    ke_pasar = rng.random(n) < p_pasar
    b.add("pasar", ids[ke_pasar], dis[ke_pasar], dis, kam)

    # Pertemuan adat / musyawarah kampung: hanya terbuka bila dipanggil tokoh adat atau kepala kampung.
    adat = (pop.umur >= 18) & (rng.random(n) < 0.7)
    b.add("adat", ids[adat], kam[adat], dis, kam)

    # Pangkalan ojek & tempat nongkrong pemuda.
    pang = (~pop.perempuan) & (pop.umur >= 16) & (pop.umur <= 40) & (rng.random(n) < np.where(kota, 0.2, 0.1))
    pa = ids[pang]
    b.add("pangkalan", pa, dis[pa] * 1000 + _chunks(dis[pa], 60, rng), dis, kam)

    # Perjalanan kampung–kota & perahu antarpulau: pelancong dari kabupaten asal bertemu warga ibu kota tujuan.
    kab_ids = geo.kab_id
    trav_a, trav_k = [], []
    pusat_kab = {k: np.flatnonzero((pop.kab == k) & geo.kampung_pusat_distrik[pop.kampung] & dewasa)
                 for k in range(len(kab_ids))}
    key_base = 0
    for k, kid in enumerate(kab_ids):
        tujuan = region.get("ikatan", {}).get(kid, {})
        if not tujuan:
            continue
        cand = np.flatnonzero((pop.kab == k) & dewasa & (rng.random(n) < 0.04))
        tk = [kab_ids.index(t) for t in tujuan]
        w = np.array(list(tujuan.values()), float)
        dest = rng.choice(tk, size=len(cand), p=w / w.sum())
        for t in tk:
            trav = cand[dest == t]
            host_pool = pusat_kab[t]
            if len(trav) == 0 or len(host_pool) == 0:
                continue
            n_grp = max(1, len(trav) // 20)
            g_local = rng.integers(0, n_grp, len(trav))
            hosts = rng.choice(host_pool, size=len(trav))
            trav_a += [trav, hosts]
            trav_k += [key_base + g_local, key_base + g_local]
            key_base += n_grp
    if trav_a:
        b.add("perjalanan", np.concatenate(trav_a), np.concatenate(trav_k), dis, kam)

    # --- WhatsApp -----------------------------------------------------------------------------
    hp = ids[pop.hp]
    b.add("wa_keluarga", hp, pop.rumah_tangga[hp].astype(np.int64) // 8 + kam[hp] * 1_000_000, dis, kam)
    # Grup marga/keluarga besar lintas kabupaten (perantau di kota tetap di grup kampung asal).
    marga_member = hp[rng.random(len(hp)) < 0.55]
    marga_key = pop.kab[marga_member].astype(np.int64) * 10_000_000 + pop.rumah_tangga[marga_member] // 60
    lintas = rng.random(len(marga_member)) < 0.25
    if lintas.any():
        src = marga_member[lintas]
        for k, kid in enumerate(kab_ids):
            sel = np.flatnonzero(pop.kab[src] == k)
            tujuan = region.get("ikatan", {}).get(kid, {})
            if len(sel) == 0 or not tujuan:
                continue
            tk = np.array([kab_ids.index(t) for t in tujuan])
            w = np.array(list(tujuan.values()), float)
            dest = rng.choice(tk, size=len(sel), p=w / w.sum())
            # bergabung ke grup marga acak milik kabupaten tujuan
            for t in np.unique(dest):
                tsel = sel[dest == t]
                pool = np.unique(marga_key[pop.kab[marga_member] == t])
                if len(pool):
                    marga_key[np.flatnonzero(lintas)[tsel]] = rng.choice(pool, size=len(tsel))
    b.add("wa_marga", marga_member, marga_key, dis, kam)

    jem_wa = hp[hadir[hp] & (rng.random(len(hp)) < 0.5)]
    b.add("wa_jemaat", jem_wa, jemaat[jem_wa], dis, kam)

    # Grup orang tua murid: ±1 sekolah per 700 jiwa di distrik, ditentukan per kampung.
    dis_pop = np.bincount(dis, minlength=len(geo.distrik_nama))
    n_sek = np.maximum(1, np.ceil(dis_pop / 700)).astype(np.int64)
    sek_of_kam = (_hash_unit(np.arange(len(geo.kampung_nama)), 7) * n_sek[geo.kampung_distrik]).astype(np.int64)
    ortu = hp[pop.ortu_sekolah[hp] & (rng.random(len(hp)) < 0.85)]
    b.add("wa_sekolah", ortu, dis[ortu] * 10_000 + sek_of_kam[kam[ortu]], dis, kam)

    # --- Facebook -----------------------------------------------------------------------------
    fb = ids[pop.fb]
    jayapura = kab_ids.index("kota_jayapura") if "kota_jayapura" in kab_ids else 0
    fb_a, fb_k = [], []
    for kind, prob in ((0, 0.7), (1, 0.4)):          # 0 = info kabupaten, 1 = jual-beli kabupaten
        m = fb[rng.random(len(fb)) < prob]
        fb_a.append(m)
        fb_k.append(pop.kab[m].astype(np.int64) * 10 + kind)
    m = fb[(pop.kab[fb] != jayapura) & (rng.random(len(fb)) < 0.3)]   # info kota Jayapura untuk perantau & kerabat
    fb_a.append(m)
    fb_k.append(np.full(len(m), jayapura * 10, np.int64))
    b.add("fb_info", np.concatenate(fb_a), np.concatenate(fb_k), dis, kam)

    # --- Radio --------------------------------------------------------------------------------
    stations = region.get("radio", [])
    radio_names, radio_groups = [], []
    if stations:
        cover = np.zeros((len(stations), len(kab_ids)), bool)
        for s, st in enumerate(stations):
            for kid in st["jangkauan"]:
                cover[s, kab_ids.index(kid)] = True
        dengar = dewasa & (rng.random(n) < np.where(kota, 0.2, 0.4))
        cand = ids[dengar]
        opts = cover[:, pop.kab[cand]].T                # (n_cand, S)
        has = opts.any(1)
        cand, opts = cand[has], opts[has]
        r = rng.random(opts.shape) * opts
        pick = r.argmax(1)
        gids = b.add("radio", cand, pick.astype(np.int64), dis, kam)
        used = np.unique(pick)
        radio_names = [stations[s]["nama"] for s in used]
        radio_groups = list(gids)

    # --- Rakitan ------------------------------------------------------------------------------
    rows = np.concatenate(b.rows)
    cols = np.concatenate(b.cols)
    M = sp.csr_matrix((np.ones(len(rows), np.float32), (rows, cols)), shape=(n, b.G))
    M.sum_duplicates()
    M.data[:] = 1.0
    jenis = np.concatenate(b.g_jenis)
    ukuran = np.asarray(M.sum(0)).ravel().astype(np.int32)
    kanal = np.array([JENIS[j][1] for j in jenis], np.int8)
    trust = np.array([params["kepercayaan"][JENIS[j][2]] for j in jenis], np.float32)
    kontak = np.array([JENIS[j][3] for j in jenis], np.float32)
    baca = np.where(kontak > 0, np.minimum(1.0, kontak / np.maximum(ukuran - 1, 1)),
                    np.where(kontak == 0, 0.9, -kontak)).astype(np.float32)
    jadwal = np.array([JADWAL.index(JENIS[j][4]) for j in jenis], np.int8)

    hari_pasar = np.zeros((len(geo.distrik_nama), 7), bool)
    for d in range(len(geo.distrik_nama)):
        per_minggu = region["tipe_wilayah"][TIPE_WILAYAH[geo.distrik_tipe[d]]]["pasar_per_minggu"]
        hari_pasar[d, rng.choice(7, size=min(7, per_minggu), replace=False)] = True

    return Network(
        M=M, MT=M.T.tocsr(), jenis=jenis, kanal=kanal, kepercayaan=trust, baca=baca, jadwal=jadwal,
        distrik=np.concatenate(b.g_distrik).astype(np.int16), kampung=np.concatenate(b.g_kampung).astype(np.int32),
        ukuran=ukuran, hari_pasar=hari_pasar, radio_stasiun=radio_names,
        radio_group=np.asarray(radio_groups, np.int64),
    )


def _hash_unit(x: np.ndarray, salt: int) -> np.ndarray:
    """Bilangan semu-acak deterministik di [0,1) dari bilangan bulat (untuk penugasan stabil)."""
    v = (x.astype(np.uint64) + np.uint64(salt)) * np.uint64(0x9E3779B97F4A7C15)
    v ^= v >> np.uint64(29)
    v *= np.uint64(0xBF58476D1CE4E5B9)
    v ^= v >> np.uint64(32)
    return (v >> np.uint64(11)).astype(np.float64) / float(1 << 53)
