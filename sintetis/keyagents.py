"""Pemilihan agen kunci khas Papua dan persona fiktifnya (PRD §6, §10).

Agen kunci adalah agen biasa di populasi yang juga "memimpin" kelompok: pendeta di rumah ibadah
dan grup WA jemaat, tokoh adat & kepala kampung di pertemuan adat, mama-mama di pasar, tokoh
pemuda di pangkalan, dan akun medsos lokal di grup FB. Matriks pemimpin L (agen kunci × kelompok)
memberi jangkauan siaran (mimbar, pengumuman) yang jauh melebihi anggota biasa.

Semua nama fiktif dan generik (tanpa marga). Persona wajib ditinjau penutur asli sebelum dipakai;
status tinjauan dicatat di kolom "ditinjau".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import yaml

from .config import CONFIG_DIR
from .network import J, Network
from .population import Geography, Population

PERAN = ("pendeta", "tokoh_adat", "kepala_kampung", "tokoh_pemuda", "mama_pasar", "akun_medsos")

NAMA_L = ["Markus", "Yohanes", "Paulus", "Petrus", "Simon", "Daniel", "Frans", "Yakob", "Nikolas", "Elias",
          "Obet", "Lukas", "Yosua", "Matius", "Benyamin", "Herman", "Agus", "Yunus", "Stefanus", "Natan"]
NAMA_P = ["Maria", "Yuliana", "Agustina", "Elisabeth", "Ruth", "Natalia", "Martha", "Ester", "Debora", "Yohana",
          "Sara", "Rebeka", "Lea", "Mince", "Yosina", "Dorkas", "Naomi", "Selfina", "Margaretha", "Hana"]

GELAR_ADAT = {  # sebutan tokoh adat per wilayah budaya
    "kota_jayapura": "Ondoafi", "kab_jayapura": "Ondoafi", "keerom": "Ondoafi", "sarmi": "Ondoafi",
    "biak_numfor": "Mananwir", "supiori": "Mananwir",
}

NILAI = {
    "pendeta": ["melindungi jemaat", "hati-hati terhadap kabar dari luar", "menjaga damai"],
    "tokoh_adat": ["menjaga kehormatan kampung", "musyawarah sebelum bertindak", "melindungi anak cucu"],
    "kepala_kampung": ["keamanan warga", "koordinasi dengan distrik", "tidak mau kampung ribut"],
    "tokoh_pemuda": ["siap jaga kampung", "solidaritas pemuda", "cepat bergerak"],
    "mama_pasar": ["tahu semua kabar", "sayang anak-anak", "dagangan harus laku"],
    "akun_medsos": ["cepat posting info", "banyak pengikut", "suka konten viral"],
}

LABEL_PERAN = {
    "pendeta": "Pendeta jemaat", "tokoh_adat": "Tokoh adat", "kepala_kampung": "Kepala kampung",
    "tokoh_pemuda": "Tokoh pemuda", "mama_pasar": "Mama-mama pasar", "akun_medsos": "Akun media sosial lokal",
}

# Jangkauan pemimpin: peluang anggota kelompok menerima pesan dari pemimpin per langkah aktif.
BACA_PEMIMPIN = {"ibadah": 0.9, "wa_jemaat": 0.7, "adat": 0.85, "tetangga": 0.5, "wa_marga": 0.6,
                 "wa_keluarga": 0.7, "pangkalan": 0.5, "pasar": 40.0, "fb_info": 400.0}


@dataclass
class KeyAgents:
    agen: np.ndarray                  # (K,) indeks agen
    peran: np.ndarray                 # (K,) indeks PERAN
    L: sp.csr_matrix                  # (K, G) bobot jangkauan pemimpin
    persona: list[dict] = field(default_factory=list)

    @property
    def k(self) -> int:
        return len(self.agen)


def select_key_agents(pop: Population, geo: Geography, net: Network, params: dict,
                      rng: np.random.Generator, jumlah: int | None = None) -> KeyAgents:
    cfg = params["agen_kunci"]
    total = int(jumlah if jumlah is not None else cfg["jumlah"])
    taken = np.zeros(pop.n, bool)
    used_groups: set[int] = set()
    agen, peran, lead = [], [], []

    def pick_groups(jenis: str, count: int) -> np.ndarray:
        cand = np.flatnonzero((net.jenis == J[jenis]) & (net.ukuran >= 3))
        cand = np.array([g for g in cand if g not in used_groups], dtype=np.int64)
        if len(cand) == 0 or count <= 0:
            return np.empty(0, np.int64)
        w = np.sqrt(net.ukuran[cand].astype(float))
        return rng.choice(cand, size=min(count, len(cand)), replace=False, p=w / w.sum())

    def choose_leader(members: np.ndarray, cond: np.ndarray) -> int | None:
        ok = members[cond[members] & ~taken[members]]
        if len(ok) == 0:
            ok = members[~taken[members]]
        return int(rng.choice(ok)) if len(ok) else None

    def own_groups(a: int, jenis: str) -> list[int]:
        return [int(g) for g in net.groups_of(a) if net.jenis[g] == J[jenis]]

    counts = {p: int(round(total * cfg["porsi"].get(p, 0))) for p in PERAN}
    counts["pendeta"] += total - sum(counts.values())

    umur, perempuan = pop.umur, pop.perempuan
    rules = {
        "pendeta": ("ibadah", (umur >= 30) & (umur <= 68)),
        "tokoh_adat": ("adat", (umur >= 45) & ~perempuan),
        "kepala_kampung": ("adat", (umur >= 30) & (umur <= 65)),
        "tokoh_pemuda": ("pangkalan", (umur >= 20) & (umur <= 35)),
        "mama_pasar": ("pasar", perempuan & (umur >= 30) & (umur <= 65)),
    }
    for p_idx, p in enumerate(PERAN):
        if p == "akun_medsos":
            cand = np.flatnonzero(pop.fb & (umur >= 18) & (umur <= 45) & ~taken)
            if len(cand):
                chosen = rng.choice(cand, size=min(counts[p], len(cand)), replace=False)
                for a in chosen:
                    taken[a] = True
                    agen.append(int(a)); peran.append(p_idx)
                    lead.append({g: BACA_PEMIMPIN["fb_info"] for g in own_groups(int(a), "fb_info")})
            continue
        jenis, cond = rules[p]
        for g in pick_groups(jenis, counts[p]):
            a = choose_leader(net.members_of(int(g)), cond & (pop.hp | (p != "pendeta")))
            if a is None:
                continue
            if p != "tokoh_adat" and p != "kepala_kampung":
                used_groups.add(int(g))
            taken[a] = True
            agen.append(a); peran.append(p_idx)
            groups = {int(g): BACA_PEMIMPIN[jenis]}
            extra = {"pendeta": ["wa_jemaat"], "tokoh_adat": ["tetangga", "wa_marga"],
                     "kepala_kampung": ["tetangga", "wa_keluarga"], "tokoh_pemuda": ["wa_marga"],
                     "mama_pasar": ["wa_keluarga"]}[p]
            for e in extra:
                for og in own_groups(a, e):
                    groups[og] = BACA_PEMIMPIN[e]
            if p == "kepala_kampung":   # kepala kampung juga menjangkau semua tetangga di kampungnya
                for tg in np.flatnonzero((net.jenis == J["tetangga"]) & (net.kampung == pop.kampung[a])):
                    groups[int(tg)] = BACA_PEMIMPIN["tetangga"]
            lead.append(groups)

    rows, cols, vals = [], [], []
    for r, groups in enumerate(lead):
        for g, w in groups.items():
            rows.append(r); cols.append(g)
            vals.append(min(1.0, w / max(net.ukuran[g] - 1, 1)) if w > 1 else w)
    L = sp.csr_matrix((np.asarray(vals, np.float32), (rows, cols)), shape=(len(agen), net.G))
    ka = KeyAgents(agen=np.asarray(agen, np.int64), peran=np.asarray(peran, np.int8), L=L)
    ka.persona = build_personas(ka, pop, geo, net, rng)
    return ka


def build_personas(ka: KeyAgents, pop: Population, geo: Geography, net: Network,
                   rng: np.random.Generator) -> list[dict]:
    reviewed = _load_reviewed()
    out = []
    for i, (a, p_idx) in enumerate(zip(ka.agen, ka.peran)):
        p = PERAN[p_idx]
        kab_id = geo.kab_id[pop.kab[a]]
        nama = rng.choice(NAMA_P if pop.perempuan[a] else NAMA_L)
        prefix = {"pendeta": "Pdt.", "tokoh_adat": GELAR_ADAT.get(kab_id, "Kepala Suku"),
                  "kepala_kampung": "Bpk." if not pop.perempuan[a] else "Ibu",
                  "tokoh_pemuda": "Kaka", "mama_pasar": "Mama", "akun_medsos": "@info"}[p]
        if p == "akun_medsos":
            nama_lengkap = f"@info.{geo.kab_nama[pop.kab[a]].split()[-1].lower()}{i:03d}"
        else:
            nama_lengkap = f"{prefix} {nama}"
        reach = int(ka.L[i].multiply(net.ukuran[None, :]).sum())
        sig = pop.sinyal[a]
        persona = {
            "id": f"KA{i:03d}",
            "agen": int(a),
            "nama": nama_lengkap,
            "peran": LABEL_PERAN[p],
            "kode_peran": p,
            "wilayah": f"{geo.distrik_nama[pop.distrik[a]]}, {geo.kab_nama[pop.kab[a]]}",
            "kabupaten_id": kab_id,
            "usia": int(pop.umur[a]),
            "jangkauan": f"±{reach} orang lewat {ka.L[i].nnz} kelompok",
            "sinyal": "kuat" if sig >= 1 else ("sedang" if sig > 0 else "lemah/tidak ada"),
            "nilai": NILAI[p],
            "ortu_anak_sekolah": bool(pop.ortu_sekolah[a]),
            **_sifat(p, bool(pop.ortu_sekolah[a]), float(pop.literasi[a]), np.random.default_rng([int(a), 17])),
            "ditinjau": False,
        }
        persona.update(reviewed.get(persona["id"], {}))
        out.append(persona)
    return out


# Sifat psikologis agar persona tidak seragam "ideal". Angka: (peluang suka meneruskan pesan
# tanpa cek, peluang kepercayaan rendah pada pemerintah/aparat). PERKIRAAN — validasi lewat FGD.
SIFAT_PERAN = {
    "pendeta": (0.15, 0.30), "tokoh_adat": (0.35, 0.50), "kepala_kampung": (0.35, 0.30),
    "tokoh_pemuda": (0.60, 0.60), "mama_pasar": (0.60, 0.50), "akun_medsos": (0.80, 0.50),
}
PENGALAMAN = [
    "pernah dengar cerita anak hilang di kampung lain dan sampai sekarang belum jelas",
    "pernah ada orang tak dikenal datang ke kampung dan bikin masalah",
    "pernah lihat klarifikasi pemerintah yang ternyata keliru",
    "pernah ikut sebar kabar yang ternyata hoaks dan merasa malu",
    "tidak punya pengalaman khusus soal kabar semacam ini",
]


def _sifat(peran: str, ortu: bool, literasi: float, rng: np.random.Generator) -> dict:
    p_terus, p_curiga = SIFAT_PERAN[peran]
    p_terus = min(0.95, p_terus * (1.4 - literasi))          # literasi digital rendah → lebih cepat meneruskan
    terus = rng.random() < p_terus
    curiga = rng.random() < p_curiga
    khawatir = "sangat tinggi, punya anak usia sekolah" if ortu else ("sedang" if rng.random() < 0.6 else "rendah")
    return {
        "kekhawatiran_anak": khawatir,
        "kebiasaan_pesan": ("biasa langsung meneruskan pesan penting ke grup supaya keluarga waspada, jarang cek dulu"
                            if terus else "biasanya cek dulu sebelum meneruskan pesan"),
        "percaya_pemerintah": ("rendah: lebih percaya kabar dari keluarga dan orang dekat daripada pernyataan aparat"
                               if curiga else "sedang: mau dengar pernyataan resmi, tapi tetap waspada"),
        "pengalaman": PENGALAMAN[int(rng.integers(len(PENGALAMAN)))],
    }


def _load_reviewed() -> dict[str, dict]:
    """Persona hasil tinjauan penutur asli: config/persona_ditinjau.yaml (daftar dengan kunci 'id')."""
    path = Path(CONFIG_DIR) / "persona_ditinjau.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return {d["id"]: d for d in data}
