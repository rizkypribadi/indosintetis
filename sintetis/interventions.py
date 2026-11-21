"""FR-06 Modul intervensi Papua: klarifikasi medsos resmi, gereja & tokoh adat, radio, prebunking,
penindakan provokator (patroli siber), dan tim keliling (F3).

Setiap intervensi menyuntikkan "tekanan klarifikasi" per agen (λ terlihat & bobot kepercayaan
sumber) atau mengubah keadaan agen kunci. Klarifikasi daring tunduk pada peta sinyal; radio,
mimbar, pertemuan adat, dan tim keliling tidak.

F2 (opsional di setiap langkah; tanpa kunci ini perilaku sama persis dengan versi sebelumnya):
  kabupaten / distrik     sasaran (id atau daftar); hanya warga & kelompok di distrik ini yang dijangkau
  t_sejak_deteksi         waktu mulai relatif terhadap saat isu diketahui petugas (F5), menggantikan t_jam
  pengali_kepercayaan     pengali kepercayaan pada pembawa pesan (R4: rendah/sedang/tinggi dari FGD)
  jumlah_tokoh            gereja_adat: jumlah pendeta/tokoh adat yang bisa dihubungi (kapasitas nyata)
  stasiun                 radio: daftar id stasiun di config/papua.yaml
  orang, kampung_per_jam  tim_keliling: kapasitas tim (orang × kampung per jam per orang)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from . import TIPE_WILAYAH
from .network import J

if TYPE_CHECKING:
    from .engine import Simulation

LABEL = {
    "medsos_resmi": "Klarifikasi resmi Polres/Diskominfo di Facebook & WA",
    "gereja_adat": "Imbauan Sinode gereja & lembaga adat: isu culik anak itu hoaks",
    "radio": "Siaran RRI/radio komunitas: tidak ada penculikan anak",
    "prebunking": "Pembekalan di gereja & sekolah tentang modus hoaks culik anak",
    "penindakan": "Patroli siber: akun provokator diturunkan dan provokator lapangan ditindak",
    "tim_keliling": "Tim keliling: klarifikasi tatap muka dari kampung ke kampung",
}


@dataclass
class Injection:
    seen: np.ndarray      # λ terlihat per agen
    trust: np.ndarray     # λ × kepercayaan per agen
    label: str
    jenis: str


class Intervention:
    def __init__(self, spec: dict, sim: "Simulation"):
        self.spec = spec
        self.jenis = spec["jenis"]
        if spec.get("t_sejak_deteksi") is not None:
            self.t_jam = sim.t_deteksi_jam + float(spec["t_sejak_deteksi"])
        else:
            self.t_jam = float(spec.get("t_jam", 0))
        self.t0 = sim.step_of_hour(self.t_jam)
        self.t1 = self.t0 + sim.step_of_hour(spec.get("durasi_jam", 0)) if "durasi_jam" in spec else self.t0
        self.label = LABEL[self.jenis]
        self.bersasaran = bool(spec.get("kabupaten"))
        self.dmask = sim._dmask(spec)                       # (D,) distrik sasaran
        self.amask = self.dmask[sim.w.pop.distrik]          # (n,) warga sasaran
        if self.bersasaran:
            nama = sim.w.geo.distrik_nama
            d = np.flatnonzero(self.dmask)
            self.label += " · " + (", ".join(nama[i] for i in d[:3]) + (f" +{len(d) - 3} distrik" if len(d) > 3 else "")
                                   if spec.get("distrik") else ", ".join(np.atleast_1d(spec["kabupaten"])))

    def pengali(self, sim: "Simulation", pembawa: str):
        """Pengali kepercayaan per warga: kepercayaan_pembawa[pembawa][tipe wilayah] × pengali_kepercayaan spec."""
        tab = (sim.p.get("kepercayaan_pembawa") or {}).get(pembawa) or {}
        v = np.array([float(tab.get(t, 1.0)) for t in TIPE_WILAYAH], np.float32)
        s = float(self.spec.get("pengali_kepercayaan", 1.0))
        if s == 1.0 and (v == 1.0).all():
            return 1.0
        return (v[sim.atipe] * s).astype(np.float32)

    def prepare(self, sim: "Simulation") -> None:
        pass

    def on_step(self, sim: "Simulation", t: int) -> Injection | None:
        return None


class MedsosResmi(Intervention):
    def prepare(self, sim):
        pop, p = sim.w.pop, sim.p
        dur = max(1, self.t1 - self.t0)
        mean_act = float(np.mean(p["aktivitas_jam"]))
        self.rate = -np.log(1 - self.spec.get("jangkauan", 0.35)) / (dur * mean_act)
        self.reach = ((pop.hp | pop.fb) * sim.sig).astype(np.float32)   # butuh internet
        if self.bersasaran:
            self.reach *= self.amask
        self.trust = float(self.spec.get("kepercayaan", p["kepercayaan"]["pemerintah"])) * self.pengali(sim, "pemerintah")

    def on_step(self, sim, t):
        if not self.t0 <= t < self.t1:
            return None
        seen = self.reach * (self.rate * sim.hour_act)
        if t == self.t0:
            sim.event(t, "intervensi", self.label)
        return Injection(seen, seen * self.trust, self.label, self.jenis)


class Radio(Intervention):
    def prepare(self, sim):
        net = sim.w.net
        is_radio = net.jenis == J["radio"]
        st = {s["nama"]: s for s in sim.w.region.get("radio", [])}
        pilih = self.spec.get("stasiun")
        if pilih:
            pilih = [pilih] if isinstance(pilih, str) else list(pilih)
            st = {k: v for k, v in st.items() if v["id"] in pilih or k in pilih}
            if not st:
                raise ValueError(f"stasiun radio tidak dikenal: {pilih}")
            is_radio = np.zeros(net.G, bool)
            for nama, g in zip(net.radio_stasiun, net.radio_group):
                if nama in st:
                    is_radio[g] = True
            self.label += " · " + ", ".join(st)
        self.listener = np.asarray(net.M @ is_radio.astype(np.float32)).ravel().astype(np.float32)
        if self.bersasaran:
            self.listener *= self.amask
        self.trust = ((float(np.mean([s.get("kepercayaan", 0.75) for s in st.values()])) if st else 0.75)
                      * self.pengali(sim, "radio"))

    def on_step(self, sim, t):
        if not self.t0 <= t < self.t1:
            return None
        if t == self.t0:
            sim.event(t, "intervensi", self.label)
        on_air = sim.jadwal_factor[sim.jadwal_idx["radio_dengar"]]
        sim.radio_on_air = on_air > 0
        if on_air <= 0:
            return None
        seen = self.listener * (sim.p["beta"]["radio"] * 0.8 * on_air)
        return Injection(seen, seen * self.trust, self.label, self.jenis)


class GerejaAdat(Intervention):
    """Sinode & lembaga adat meminta pendeta dan tokoh adat meluruskan. Keputusan tetap di tangan agen kunci."""

    def prepare(self, sim):
        from .keyagents import PERAN

        geo, pop, key, net = sim.w.geo, sim.w.pop, sim.w.key, sim.w.net
        porsi = self.spec.get("porsi_tokoh", 0.8)
        if self.spec.get("jumlah_tokoh") is not None:
            # kapasitas nyata → porsi kelompok kelembagaan yang terjangkau di wilayah sasaran
            nyata = float(geo.kampung_bobot[self.dmask[geo.kampung_distrik]].sum())
            per = float((sim.p.get("kapasitas") or {}).get("warga_per_tokoh", 400))
            porsi = min(1.0, float(self.spec["jumlah_tokoh"]) * per / max(nyata, 1.0))
        self.porsi = porsi
        roles = [PERAN.index("pendeta"), PERAN.index("tokoh_adat")]
        idx = np.flatnonzero(np.isin(key.peran, roles))
        if self.bersasaran:
            idx = idx[self.dmask[pop.distrik[key.agen[idx]]]]
        rng = np.random.default_rng([sim.seed, 77])
        k = int(round(len(idx) * porsi))
        self.targets = np.sort(rng.choice(idx, size=k, replace=False)) if k else idx[:0]
        # Pendeta & tokoh adat lain (agen ABM biasa) juga menerima imbauan lewat jalur kelembagaan:
        # mimbar ibadah, grup WA jemaat, dan pertemuan adat di kampung yang tercakup.
        jen = np.isin(net.jenis, (J["ibadah"], J["wa_jemaat"], J["adat"]))
        if self.bersasaran:
            jen &= self.dmask[net.distrik]
        self.covered = (jen & (rng.random(net.G) < porsi)).astype(np.float32)
        self.adat_cov = np.flatnonzero((net.jenis == J["adat"]) & (self.covered > 0))
        self.tau = sim.tau * 1.5
        # pengali kepercayaan per kelompok: adat untuk pertemuan adat, gereja untuk mimbar & grup jemaat
        tab = sim.p.get("kepercayaan_pembawa") or {}
        g_tipe = geo.distrik_tipe[net.distrik]
        m_g = np.array([float((tab.get("gereja") or {}).get(t, 1.0)) for t in TIPE_WILAYAH], np.float32)[g_tipe]
        m_a = np.array([float((tab.get("adat") or {}).get(t, 1.0)) for t in TIPE_WILAYAH], np.float32)[g_tipe]
        gm = np.where(net.jenis == J["adat"], m_a, m_g) * float(self.spec.get("pengali_kepercayaan", 1.0))
        self.kep_g = net.kepercayaan * gm if not np.allclose(gm, 1.0) else net.kepercayaan

    def on_step(self, sim, t):
        if t < self.t0:
            return None
        if t == self.t0:
            sim.event(t, "intervensi", f"{self.label} (diterima {len(self.targets)} agen kunci + "
                                       f"{int(self.covered.sum()):,} jemaat/kampung)".replace(",", "."))
            for ki in self.targets:
                sim.key_notify_clarification(int(ki), self.label, sumber="sinode/lembaga adat", tepercaya=True)
            sim.adat_open_until[self.adat_cov] = t + sim.step_of_hour(6)
            sim._jadwal(t)
        net = sim.w.net
        decay = np.exp(-(t - self.t0) * sim.dt_h / self.tau)
        # Mimbar & pertemuan adat tidak meluruh selama berlangsung; pesan WA jemaat meluruh.
        strength = np.where(net.jenis == J["wa_jemaat"], decay, 1.0).astype(np.float32)
        P = self.covered * strength * sim.open_g * 0.9 * sim.beta_g
        seen = (net.M @ (P * sim.on_f)) * sim.sig + net.M @ (P * sim.off_f)
        trust = (net.M @ (P * sim.on_f * self.kep_g)) * sim.sig + net.M @ (P * sim.off_f * self.kep_g)
        return Injection(seen.astype(np.float32), trust.astype(np.float32), self.label, self.jenis)


class Prebunking(Intervention):
    def prepare(self, sim):
        net, pop = sim.w.net, sim.w.pop
        kanal = self.spec.get("kanal", ["ibadah"])
        jen = ([J["ibadah"], J["wa_jemaat"]] if "ibadah" in kanal else []) + ([J["wa_sekolah"]] if "sekolah" in kanal else [])
        mask = np.isin(net.jenis, jen).astype(np.float32)
        member = np.asarray(net.M @ mask).ravel() > 0
        if "sekolah" in kanal:
            # anak dibekali di sekolah, orang tua ikut terpapar lewat anak
            rt_anak = np.bincount(pop.rumah_tangga, weights=pop.anak_sekolah).astype(bool)
            member |= pop.anak_sekolah | (rt_anak[pop.rumah_tangga] & (pop.umur >= 18))
        if self.bersasaran:
            member &= self.amask
        cakupan = float(self.spec.get("cakupan", 0.6))
        paruh = (sim.p.get("prebunking") or {}).get("paruh_hari")
        if paruh and self.t_jam < 0:          # efek prebunking memudar bila dilakukan jauh sebelum isu
            cakupan *= 0.5 ** ((-self.t_jam / 24) / float(paruh))
        rng = np.random.default_rng([sim.seed, 78])
        self.kena = member & (rng.random(pop.n) < cakupan)
        if self.t0 <= 0:
            self._terapkan(sim, 0)

    def _terapkan(self, sim, t):
        sim.prebunk |= self.kena
        sim.event(t, "intervensi", f"{self.label}: {int(sim.prebunk.sum()):,} warga".replace(",", "."))

    def on_step(self, sim, t):
        if self.t0 > 0 and t == self.t0:      # prebunking yang disisipkan di tengah putaran
            self._terapkan(sim, t)
        return None


class Penindakan(Intervention):
    """Patroli siber/platform menurunkan sebagian akun buzzer, aparat dan tokoh menghentikan provokator lapangan.

    Tidak berpengaruh bila skenario tidak punya provokator. Buzzer dengan `pulih_jam` kembali dengan akun baru.
    """

    def on_step(self, sim, t):
        if t != self.t0:
            return None
        if sim.prov is None:
            sim.event(t, "intervensi", f"{self.label}: tidak ada provokator di skenario ini")
            return None
        rng = np.random.default_rng([sim.seed, 79])
        n_akun, n_lokal = sim.prov.tindak(t, float(self.spec.get("porsi_akun", 0.7)),
                                          float(self.spec.get("porsi_lokal", 0.5)), rng)
        sim.event(t, "intervensi", f"{self.label}: {n_akun} akun diturunkan, {n_lokal} provokator lapangan dihentikan")
        return None


class TimKeliling(Intervention):
    """F3: tim lapangan mendatangi kampung satu per satu dan meluruskan isu secara tatap muka.

    Kapasitas = orang × kampung_per_jam (per orang) selama jam kerja. Kampung dipilih berurutan:
    paling banyak penyebar/siaga dulu, lalu yang tanpa sinyal, lalu yang berpenduduk terbanyak.
    Tidak butuh sinyal, jadi inilah jalur selain radio ke pedalaman tanpa sinyal.
    """

    def prepare(self, sim):
        geo, pop = sim.w.geo, sim.w.pop
        self.orang = int(self.spec.get("orang", 4))
        self.laju = float(self.spec.get("kampung_per_jam", 0.5)) * self.orang
        self.t1 = self.t0 + sim.step_of_hour(self.spec.get("durasi_jam", 24))
        kap = sim.p.get("kapasitas") or {}
        self.jam_kerja = kap.get("tim_jam_kerja", [7, 18])
        self.daya = float(self.spec.get("daya", kap.get("tim_daya", 1.2)))
        V = len(geo.kampung_nama)
        self.kandidat = self.dmask[geo.kampung_distrik]
        self.sudah = np.zeros(V, bool)
        urut = np.argsort(pop.kampung, kind="stable")
        self.anggota = urut
        self.batas = np.searchsorted(pop.kampung[urut], np.arange(V + 1))
        self.trust = float(sim.p["kepercayaan"].get("tim_lapangan", 0.8)) * self.pengali(sim, "tim_lapangan")
        self.kuota, self.total = 0.0, 0

    def on_step(self, sim, t):
        if not self.t0 <= t < self.t1:
            return None
        if t == self.t0:
            sim.event(t, "intervensi", f"{self.label} ({self.orang} orang, ±{self.laju:g} kampung/jam)")
        if not self.jam_kerja[0] <= sim.hour < self.jam_kerja[1]:
            return None
        self.kuota += self.laju * sim.dt_h
        k = int(self.kuota)
        cand = np.flatnonzero(self.kandidat & ~self.sudah)
        if k == 0 or len(cand) == 0:
            return None
        self.kuota -= k
        geo, pop, st = sim.w.geo, sim.w.pop, sim.status
        V = len(geo.kampung_nama)
        sebar = np.bincount(pop.kampung, weights=((st == 1) | (st == 2) | (st == 4)), minlength=V)[cand]
        order = np.lexsort((-geo.kampung_bobot[cand], geo.kampung_sinyal[cand], -sebar))
        pilih = cand[order[:k]]
        self.sudah[pilih] = True
        self.total += len(pilih)
        agents = np.concatenate([self.anggota[self.batas[v]:self.batas[v + 1]] for v in pilih])
        seen = np.zeros(sim.n, np.float32)
        seen[agents] = self.daya / sim.scale            # mesin mengalikan λ suntikan dengan skala langkah
        ks = sim.key_of[agents]
        for ki in ks[ks >= 0]:
            sim.key_notify_clarification(int(ki), self.label, sumber="tim lapangan", tepercaya=True)
        nama = sorted({geo.distrik_nama[geo.kampung_distrik[v]] for v in pilih})
        sim.event(t, "intervensi", f"Tim keliling di {len(pilih)} kampung ({', '.join(nama[:3])}); total {self.total}")
        return Injection(seen, seen * self.trust, self.label, self.jenis)


KELAS = {"medsos_resmi": MedsosResmi, "radio": Radio, "gereja_adat": GerejaAdat, "prebunking": Prebunking,
         "penindakan": Penindakan, "tim_keliling": TimKeliling}


def build_interventions(spec: dict | None, sim: "Simulation") -> list[Intervention]:
    out = []
    for step in (spec or {}).get("langkah", []):
        iv = KELAS[step["jenis"]](step, sim)
        iv.prepare(sim)
        out.append(iv)
    return out
