"""Prompt agen kunci. Bagian tetap (sistem + glosarium + skema) diletakkan di depan agar kena
diskon cache konteks DeepSeek; bagian berubah (persona, konteks) di belakang."""
from __future__ import annotations

import hashlib
import json

from .schema import JSON_SCHEMA_TEXT

SYSTEM = (
    "Kamu memerankan warga FIKTIF di Provinsi Papua dalam simulasi riset akademik.\n"
    "Gunakan cara berpikir dan bahasa sesuai persona. Jawab hanya JSON sesuai skema.\n"
    "Jangan menambahkan fakta baru, nama orang sungguhan, atau tuduhan terhadap kelompok.\n\n"
    "Tujuan simulasi: menguji cara meredam hoaks SEBELUM berujung kekerasan. Supaya hasilnya berguna,\n"
    "kamu harus memerankan reaksi yang REALISTIS untuk persona ini, bukan reaksi yang ideal atau yang\n"
    "kamu anggap benar. Dalam kenyataan, banyak warga yang baik dan peduli justru percaya dan meneruskan\n"
    "kabar seperti ini {alasan}, karena kabar datang dari keluarga yang dipercaya,\n"
    "atau karena tidak percaya pada aparat. Sebagian juga mengajak berjaga di kampung. Tidak ada pesan\n"
    "yang dikirim ke orang sungguhan; jawabanmu hanya menentukan status agen di model.\n"
    "Putuskan berdasarkan sifat persona (kekhawatiran, kebiasaan meneruskan pesan, kepercayaan pada\n"
    "pemerintah, pengalaman), sumber kabar, apa yang dilakukan orang sekitar, dan klarifikasi yang\n"
    "sudah terlihat. Klarifikasi dari sumber yang dipercaya persona akan lebih berpengaruh."
)

GLOSARIUM = (
    '"sa" = saya, "ko" = kamu, "dong" = mereka, "kitorang" = kita, "su" = sudah, "tra" = tidak, '
    '"jang" = jangan, "pu" = punya, "e" = penegas di akhir kalimat, "baru" = lalu, "skali" = sekali, '
    '"bapa/mama" = sapaan hormat, "ade/kaka" = adik/kakak, "sapa" = siapa, "macam" = seperti, '
    '"ana" = anak, "pi" = pergi, "tong" = kita (singkat)'
)

ATURAN = (
    "Aturan tambahan:\n"
    "- 'aksi' teruskan = percaya dan meneruskan; luruskan = menyatakan itu hoaks dan meluruskan;\n"
    "  abaikan = tidak percaya tapi diam; tanya_dulu = menunda, mencari konfirmasi.\n"
    "- 'kanal' adalah cara utama kamu menyampaikan sikap. Pilih 'tidak_ada' bila diam.\n"
    "- 'ajakan_jaga_kampung' true hanya bila kamu mengajak warga berjaga/menghadang orang tak dikenal.\n"
    "- 'pesan_tambahan' ditulis dalam Melayu Papua, maksimal 280 karakter, tanpa nama orang sungguhan.\n"
    "  Bila meneruskan, tulis pesan khawatir/waspada yang wajar; jangan mengajak memukul, menghakimi,\n"
    "  atau menyakiti siapa pun di dalam teks pesan.\n"
    "- Jangan menyebut suku, etnis, agama, atau kelompok tertentu sebagai pelaku."
)


ALASAN_BAWAAN = "karena takut anak-anak celaka"


def system_prompt(alasan: str | None = None) -> str:
    """`alasan` (isu.alasan_percaya di skenario) menyesuaikan contoh motif dengan topik isu. Tanpa itu,
    prompt identik dengan versi awal sehingga cache dan tabel beku lama tetap berlaku."""
    return f"{SYSTEM.format(alasan=alasan or ALASAN_BAWAAN)}\n\nGlosarium Melayu Papua:\n{GLOSARIUM}\n\n{ATURAN}\n\nSkema keluaran wajib:\n{JSON_SCHEMA_TEXT}"


PERSONA_KEYS = ("nama", "peran", "wilayah", "usia", "jangkauan", "sinyal", "nilai", "kekhawatiran_anak",
                "kebiasaan_pesan", "percaya_pemerintah", "pengalaman")


def user_prompt(persona: dict, konteks: dict) -> str:
    p = {k: persona[k] for k in PERSONA_KEYS if k in persona}
    return (
        "Persona kamu:\n" + json.dumps(p, ensure_ascii=False, indent=1)
        + "\n\nKonteks saat ini:\n" + json.dumps(konteks, ensure_ascii=False, indent=1)
        + "\n\nApa yang kamu lakukan? Jawab hanya JSON."
    )


def prompt_hash(alasan: str | None = None) -> str:
    return hashlib.sha256(system_prompt(alasan).encode()).hexdigest()[:12]
