"""Aturan bawaan ABM untuk agen kunci — dipakai saat LLM mati, gagal validasi, melebihi anggaran,
atau kunci tidak ada di tabel beku. Juga menjadi otak penyedia 'mock' (untuk uji tanpa jaringan)."""
from __future__ import annotations

import numpy as np

from .schema import Keputusan

# peluang dasar percaya, peluang ajakan jaga kampung bila percaya, peluang meluruskan bila tidak percaya
PROFIL = {
    "pendeta": (0.30, 0.05, 0.65),
    "tokoh_adat": (0.45, 0.30, 0.55),
    "kepala_kampung": (0.45, 0.35, 0.50),
    "tokoh_pemuda": (0.65, 0.60, 0.20),
    "mama_pasar": (0.60, 0.10, 0.20),
    "akun_medsos": (0.70, 0.15, 0.30),
}

KANAL_PERAN = {
    "pendeta": "mimbar_ibadah", "tokoh_adat": "tatap_muka", "kepala_kampung": "tatap_muka",
    "tokoh_pemuda": "tatap_muka", "mama_pasar": "tatap_muka", "akun_medsos": "wa",
}

PESAN = {
    "teruskan": ["Bapa mama dorang, jaga ana-ana baik e. Jang biar dong jalan sendiri.",
                 "Sa dengar kabar ini dari keluarga. Hati-hati skali, jang kasi lepas ana sekolah."],
    "teruskan_ajakan": ["Pemuda dong, malam ini tong jaga kampung. Kalau ada mobil asing, tahan dulu.",
                        "Kitorang harus siaga. Orang tra dikenal masuk kampung, lapor ke sa."],
    "luruskan": ["Jang dulu percaya kabar itu e. Belum ada bukti, polisi deng gereja bilang itu hoaks.",
                 "Sodara dong, tahan diri. Kabar culik ana itu tra benar, jang sebar lagi."],
    "abaikan": ["", ""],
    "tanya_dulu": ["Sa tanya dulu ke kepala kampung deng gereja, baru sa kasi tahu.", ""],
}


def rule_decision(persona: dict, konteks: dict, rng: np.random.Generator, intensitas: float = 0.9) -> Keputusan:
    peran = persona.get("kode_peran", "kepala_kampung")
    p_percaya, p_ajak, p_lurus = PROFIL.get(peran, PROFIL["kepala_kampung"])
    klar = konteks.get("klarifikasi_terlihat") or []
    tepercaya = konteks.get("klarifikasi_tepercaya", False)
    p = p_percaya * (0.6 + 0.4 * intensitas)
    if persona.get("ortu_anak_sekolah"):
        p *= 1.2
    if klar:
        p *= 0.1 if tepercaya else 0.4
    p = min(p, 0.95)

    sudah_tanya = konteks.get("sudah_tanya", 0)
    if not klar and sudah_tanya == 0 and rng.random() < 0.25:
        aksi, kanal, yakin, ajak = "tanya_dulu", "tidak_ada", 0.5, False
    elif rng.random() < p:
        aksi, kanal = "teruskan", KANAL_PERAN[peran]
        yakin = float(np.clip(0.55 + 0.4 * rng.random(), 0, 1))
        ajak = bool(rng.random() < p_ajak)
    else:
        lurus = p_lurus + (0.35 if tepercaya else 0.15 if klar else 0.0)
        aksi = "luruskan" if rng.random() < min(lurus, 0.97) else "abaikan"
        kanal = KANAL_PERAN[peran] if aksi == "luruskan" else "tidak_ada"
        yakin, ajak = float(np.clip(0.1 + 0.3 * rng.random(), 0, 1)), False
    if kanal == "mimbar_ibadah" and not konteks.get("dekat_ibadah", True):
        kanal = "wa"
    key = "teruskan_ajakan" if ajak else aksi
    pesan = PESAN[key][int(rng.integers(0, len(PESAN[key])))]
    alasan = {
        "teruskan": "Kabar menyangkut keselamatan anak, lebih baik waspada.",
        "luruskan": "Belum ada bukti dan sudah ada klarifikasi; takut warga salah tangkap orang.",
        "abaikan": "Tidak yakin kabar itu benar, tapi tidak mau ribut.",
        "tanya_dulu": "Perlu konfirmasi dari pihak yang dipercaya.",
    }[aksi]
    return Keputusan(aksi=aksi, kanal=kanal, keyakinan=round(yakin, 2), ajakan_jaga_kampung=ajak,
                     pesan_tambahan=pesan, alasan_singkat=alasan)
