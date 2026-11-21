"""Aktor penyebar yang SENGAJA: buzzer daring dan provokator lapangan.

Berbeda dengan warga biasa yang menyebarkan hoaks karena percaya, aktor di sini tahu isunya tidak benar
dan menyebarkannya dengan sengaja. Karena itu mereka:
  - kebal klarifikasi (tidak bisa diluruskan selama kampanye aktif);
  - tidak kehilangan semangat (aktivitas tidak meluruh exp(−umur/τ) seperti warga);
  - bisa menyerang klarifikasi ("itu pemerintah tutup-tutupi"), sehingga klarifikasi kurang dipercaya
    oleh warga di grup yang mereka masuki;
  - bisa diturunkan atau ditindak lewat intervensi `penindakan`, dan buzzer bisa muncul lagi dengan akun baru.

Dua jenis, ditulis di blok `provokator:` berkas skenario:

  buzzer   Akun daring (boleh dari luar Papua, jadi bukan anggota populasi) yang mengunggah ke grup Facebook
           info atau grup WA. Satu akun dengan intensitas 1 setara satu warga yang baru percaya dan bersinyal
           penuh. Akun anonim kurang dipercaya (`kredibilitas_akun`), tetapi akun terkoordinasi yang mengunggah
           serentak di grup yang sama memberi kesan "banyak orang bilang begitu" (`bonus_konsensus`).
  lokal    Warga dewasa di wilayah sasaran yang menyebar lewat jaringannya sendiri (rumah tangga, tetangga,
           pangkalan, grup WA) dan, bila `ajak_jaga_kampung`, terus mengajak warga berjaga.

Contoh pesan (`contoh_pesan`, `contoh_serangan`) hanya ILUSTRASI untuk catatan peristiwa: ditampilkan
bertahap selama kampanye selama aktor masih aktif, tetapi tidak dipakai dalam perhitungan. Tekanan paparan
tetap ditentukan oleh parameter (akun, intensitas, kredibilitas), bukan oleh isi kalimat.

Pemilihan aktor hanya memakai wilayah dan usia dewasa, tidak memakai suku, agama, atau kelompok apa pun.
Tanpa blok `provokator:` mesin berjalan persis seperti sebelumnya (tidak ada bilangan acak tambahan).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from . import A, I, K_TANAM, TIPE_WILAYAH, Z
from .network import J

if TYPE_CHECKING:
    from .engine import Simulation

JENIS_GRUP_WA = {"sekolah": "wa_sekolah", "keluarga": "wa_keluarga", "jemaat": "wa_jemaat", "marga": "wa_marga"}
BAWAAN = {"kredibilitas_akun": 0.6, "bonus_konsensus": 0.3, "efek_serang_klarifikasi": 0.4, "jeda_serang_jam": 1.0}


@dataclass
class Buzzer:
    nama: str
    t0: int
    t1: int
    intensitas: float
    serang: bool
    kred: float                         # pengali kepercayaan paparan dari akun ini
    akun_grup: list[np.ndarray]         # grup sasaran per akun
    aktif: np.ndarray                   # (akun,) bool
    pulih_langkah: int = -1             # akun yang diturunkan diganti akun baru setelah sekian langkah (−1 = tidak)
    kembali: list[tuple[int, np.ndarray]] = field(default_factory=list)
    pesan: list[tuple[int, str]] = field(default_factory=list)     # (langkah tampil, teks) — ilustratif
    kanal: str = "FB"
    serang_teks: str = ""
    serang_tampil: bool = False


@dataclass
class Lokal:
    nama: str
    t0: int
    t1: int
    agen: np.ndarray
    ajak: bool
    serang: bool
    aktif: np.ndarray                   # (agen,) bool
    pesan: list[tuple[int, str]] = field(default_factory=list)
    serang_teks: str = ""
    serang_tampil: bool = False


class Provokator:
    def __init__(self, specs: list[dict], sim: "Simulation"):
        self.sim = sim
        p = {**BAWAAN, **(sim.p.get("provokator") or {})}
        self.efek_serang = float(p["efek_serang_klarifikasi"])
        self.jeda_serang = sim.step_of_hour(float(p["jeda_serang_jam"]))
        self.kred_dasar, self.bonus_konsensus = float(p["kredibilitas_akun"]), float(p["bonus_konsensus"])
        self.rng = np.random.default_rng([sim.seed, 601])
        self.buzzer: list[Buzzer] = []
        self.lokal: list[Lokal] = []
        self.t_klar = -1                                         # langkah klarifikasi pertama terlihat
        self.paparan = np.zeros(len(TIPE_WILAYAH), np.int64)                # paparan hoaks (S→E) yang berasal dari aktor ini
        self.terkunci = np.zeros(sim.n, bool)                    # provokator lokal yang sedang aktif
        urut = {"buzzer": 0, "lokal": 0}
        for s in specs:
            urut[s["jenis"]] += 1
            nama = s.get("nama") or (f"Buzzer {urut['buzzer']}" if s["jenis"] == "buzzer"
                                     else f"Provokator lapangan {urut['lokal']}")
            (self._buzzer if s["jenis"] == "buzzer" else self._lokal)(s, nama)
            aktor = (self.buzzer if s["jenis"] == "buzzer" else self.lokal)[-1]
            aktor.pesan = self._jadwal_pesan(s, aktor.t0, aktor.t1)
            aktor.serang_teks = str(s.get("contoh_serangan") or "").strip()

    # ------------------------------------------------------------------ penyusunan
    def _wilayah(self, s: dict) -> np.ndarray:
        geo, sim = self.sim.w.geo, self.sim
        m = np.ones(sim.D, bool)
        if s.get("kabupaten"):
            m = geo.distrik_kab == geo.kab_index(s["kabupaten"])
            if s.get("distrik"):
                m = np.zeros(sim.D, bool)
                m[geo.distrik_index(s["kabupaten"], s["distrik"])] = True
        return m

    def _waktu(self, s: dict) -> tuple[int, int]:
        sim = self.sim
        t0 = sim.step_of_hour(float(s.get("mulai_jam", 0)))
        t1 = sim.step_of_hour(float(s["selesai_jam"])) if s.get("selesai_jam") is not None else sim.T + 1
        return t0, t1

    def _jadwal_pesan(self, s: dict, t0: int, t1: int) -> list[tuple[int, str]]:
        """Sebar contoh pesan merata dari awal kampanye sampai sebelum berhenti (atau akhir simulasi)."""
        teks = [str(x).strip() for x in (s.get("contoh_pesan") or []) if str(x).strip()]
        akhir = min(t1, self.sim.T)
        if not teks or akhir <= t0:
            return []
        return [(t0 + int(j * (akhir - t0) / len(teks)), x) for j, x in enumerate(teks)]

    def _tampil_pesan(self, t: int, aktor, jenis: str, kanal: str) -> None:
        sim = self.sim
        if not aktor.aktif.any():                      # akun diturunkan / provokator ditindak: tidak ada unggahan
            return
        for tk, x in aktor.pesan:
            if tk == t:
                sim.event(t, "pesan_provokator", x, aktor=aktor.nama, jenis_aktor=jenis, kanal=kanal, ilustratif=True)
        if (aktor.serang and aktor.serang_teks and not aktor.serang_tampil and self.t_klar >= 0
                and t >= self.t_klar + self.jeda_serang and aktor.t0 <= t < aktor.t1):
            aktor.serang_tampil = True
            sim.event(t, "pesan_provokator", aktor.serang_teks, aktor=aktor.nama, jenis_aktor=jenis, kanal=kanal,
                      ilustratif=True, serangan=True)

    def _buzzer(self, s: dict, nama: str) -> None:
        net = self.sim.w.net
        dm = self._wilayah(s)
        kanal = s.get("kanal", "fb")
        jen = J["fb_info"] if kanal == "fb" else J[JENIS_GRUP_WA[s.get("jenis_grup", "keluarga")]]
        cand = np.flatnonzero((net.jenis == jen) & dm[net.distrik] & (net.ukuran >= 3))
        if len(cand) == 0:
            cand = np.flatnonzero((net.jenis == jen) & (net.ukuran >= 3))
        akun, per = int(s.get("akun", 10)), min(int(s.get("grup_per_akun", 3)), len(cand))
        koord = bool(s.get("terkoordinasi", True))
        if koord:   # semua akun mengunggah serentak ke himpunan grup sasaran yang sama
            sasaran = self.rng.choice(cand, per, replace=False)
            grup = [sasaran for _ in range(akun)]
        else:
            grup = [self.rng.choice(cand, per, replace=False) for _ in range(akun)]
        kred = self.kred_dasar * (1 + self.bonus_konsensus * koord)
        t0, t1 = self._waktu(s)
        pulih = s.get("pulih_jam")
        self.buzzer.append(Buzzer(nama, t0, t1, float(s.get("intensitas", 2.0)), bool(s.get("serang_klarifikasi", True)),
                                  min(1.0, kred), grup, np.ones(akun, bool),
                                  self.sim.step_of_hour(float(pulih)) if pulih is not None else -1))
        self.buzzer[-1].kanal = "FB" if kanal == "fb" else f"WA {s.get('jenis_grup', 'keluarga')}"

    def _lokal(self, s: dict, nama: str) -> None:
        sim, pop = self.sim, self.sim.w.pop
        dm = self._wilayah(s)
        cand = np.flatnonzero(dm[pop.distrik] & (pop.umur >= 18) & (pop.umur <= 55) & ~sim.is_key)
        k = min(int(s.get("jumlah", 3)), len(cand))
        agen = np.sort(self.rng.choice(cand, k, replace=False)) if k else np.empty(0, np.int64)
        t0, t1 = self._waktu(s)
        self.lokal.append(Lokal(nama, t0, t1, agen.astype(np.int64), bool(s.get("ajak_jaga_kampung", True)),
                                bool(s.get("serang_klarifikasi", True)), np.ones(len(agen), bool)))

    # ------------------------------------------------------------------ per langkah
    def on_step(self, t: int) -> None:
        """Dipanggil di awal langkah, sesudah tanam isu."""
        sim = self.sim
        for b in self.buzzer:
            if t == b.t0:
                sim.event(t, "provokator", f"{b.nama}: {int(b.aktif.sum())} akun mulai mengunggah ke "
                                           f"{len(np.unique(np.concatenate(b.akun_grup)))} grup")
            for tk, idx in [x for x in b.kembali if x[0] == t]:
                b.aktif[idx] = True
                b.kembali.remove((tk, idx))
                sim.event(t, "provokator", f"{b.nama}: {len(idx)} akun baru muncul menggantikan akun yang diturunkan")
            if b.t0 <= t < b.t1:
                self._tampil_pesan(t, b, "buzzer", b.kanal)
            if t == b.t1:
                sim.event(t, "provokator", f"{b.nama}: kampanye berhenti"
                                           + (f" ({int(b.aktif.sum())} dari {len(b.aktif)} akun masih aktif)"
                                              if not b.aktif.all() else ""))
        self.terkunci[:] = False
        for lk in self.lokal:
            a = lk.agen[lk.aktif]
            if t == lk.t0 and len(a):
                sim.event(t, "provokator", f"{lk.nama}: {len(a)} provokator lapangan mulai menyebar"
                                           + (" dan mengajak jaga kampung" if lk.ajak else ""))
            if lk.t0 <= t < lk.t1:
                self._tampil_pesan(t, lk, "lokal", "tatap muka")
            if not lk.t0 <= t < lk.t1 or len(a) == 0:
                continue
            baru = a[sim.status[a] != (A if lk.ajak else I)]
            if len(baru):
                sim._set(baru, A if lk.ajak else I, K_TANAM)
            sim.t_percaya[a] = t                                 # aktivitas tidak meluruh selama aktif
            sim.pernah_dengar[a] = True
            self.terkunci[a] = True
            if lk.ajak:
                net = sim.w.net
                gs = np.unique(np.concatenate([net.groups_of(int(x)) for x in a]))
                sim.ajakan_until[gs] = np.maximum(sim.ajakan_until[gs], t)

    def _buzzer_aktif(self, t: int) -> list[Buzzer]:
        return [b for b in self.buzzer if b.t0 <= t < b.t1 and b.aktif.any()]

    def pressure(self, t: int) -> np.ndarray | None:
        """Tekanan hoaks per kelompok dari akun buzzer aktif (skala sama dengan Simulation._group_pressure)."""
        bs = self._buzzer_aktif(t)
        if not bs:
            return None
        sim, net = self.sim, self.sim.w.net
        n_akun = np.zeros(net.G, np.float32)
        for b in bs:
            for g, on in zip(b.akun_grup, b.aktif):
                if on:
                    n_akun[g] += b.intensitas
        return (n_akun * net.baca * sim.w_hoaks * sim.open_g * sim.scale).astype(np.float32)

    def kredibilitas(self, t: int) -> float:
        bs = self._buzzer_aktif(t)
        if not bs:
            return 1.0
        w = np.array([b.aktif.sum() * b.intensitas for b in bs], float)
        return float(np.average([b.kred for b in bs], weights=w))

    def catat_paparan(self, agen: np.ndarray) -> None:
        if len(agen):
            self.paparan += np.bincount(self.sim.atipe[agen], minlength=len(self.paparan))

    def catat_klarifikasi(self, t: int) -> None:
        if self.t_klar < 0:
            self.t_klar = t

    def serangan(self, t: int) -> np.ndarray | None:
        """Porsi 0..1 per agen yang terpapar narasi tandingan ("klarifikasi itu bohong")."""
        if self.t_klar < 0 or t < self.t_klar + self.jeda_serang or self.efek_serang <= 0:
            return None
        sim, net = self.sim, self.sim.w.net
        g = np.zeros(net.G, np.float32)
        for b in self._buzzer_aktif(t):
            if b.serang:
                for gs, on in zip(b.akun_grup, b.aktif):
                    if on:
                        g[gs] = 1.0
        on = g.any()
        kena = np.minimum(1.0, net.M @ g) * sim.sig if on else np.zeros(sim.n, np.float32)
        for lk in self.lokal:
            if lk.serang and lk.t0 <= t < lk.t1 and lk.aktif.any():
                gl = np.zeros(net.G, np.float32)
                gl[np.unique(np.concatenate([net.groups_of(int(x)) for x in lk.agen[lk.aktif]]))] = 1.0
                gl[net.jenis == J["radio"]] = 0.0
                kena = np.maximum(kena, np.minimum(1.0, net.M @ gl))
                on = True
        return (self.efek_serang * kena).astype(np.float32) if on else None

    # ------------------------------------------------------------------ penindakan
    def tindak(self, t: int, porsi_akun: float, porsi_lokal: float, rng: np.random.Generator) -> tuple[int, int]:
        sim = self.sim
        n_akun = n_lokal = 0
        # Hanya aktor yang sudah beraksi yang bisa dikenali dan ditindak (tidak ada penindakan pre-emptif).
        for b in self.buzzer:
            if t < b.t0:
                continue
            on = np.flatnonzero(b.aktif)
            pilih = on[rng.random(len(on)) < porsi_akun]
            b.aktif[pilih] = False
            n_akun += len(pilih)
            if b.pulih_langkah >= 0 and len(pilih):
                b.kembali.append((t + b.pulih_langkah, pilih))
        for lk in self.lokal:
            if t < lk.t0:
                continue
            on = np.flatnonzero(lk.aktif)
            pilih = on[rng.random(len(on)) < porsi_lokal]
            lk.aktif[pilih] = False
            a = lk.agen[pilih]
            if len(a):                                           # berhenti menyebar; tahu isunya bohong (Z pasif)
                sim._set(a, Z, K_TANAM)
                sim.bobot_koreksi[a] = 0.0
                self.terkunci[a] = False
            n_lokal += len(pilih)
        return n_akun, n_lokal

    def ringkasan(self) -> dict:
        return {"buzzer": [{"nama": b.nama, "akun": len(b.aktif), "grup": len(np.unique(np.concatenate(b.akun_grup)))
                            if b.akun_grup else 0, "akun_aktif_akhir": int(b.aktif.sum())} for b in self.buzzer],
                "lokal": [{"nama": lk.nama, "jumlah": len(lk.agen), "aktif_akhir": int(lk.aktif.sum())} for lk in self.lokal],
                "paparan_dari_provokator": self.paparan.tolist()}


def build_provokator(scenario: dict, sim: "Simulation") -> Provokator | None:
    specs = scenario.get("provokator") or []
    return Provokator(specs, sim) if specs else None
