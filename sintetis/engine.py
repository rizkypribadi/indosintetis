"""FR-04 Mesin difusi SEIZA tervektorisasi dengan siklus harian (ibadah Minggu, hari pasar).

Per langkah (default 30 menit):
  1. Tanam isu & jalankan intervensi terjadwal.
  2. Paparan hoaks:  p(S→E) = 1 − exp(−λ),
       λ_i = Σ_g  β[kanal_g] · kepercayaan_g · baca_g · buka_g(t) · Σ_{j∈g} aktivitas_j · sinyal_j^online
             · sinyal_i^online · faktor_identitas_i · (1 − 0,45·literasi_i) · faktor_prebunking_i
       aktivitas_j = exp(−umur_sejak_percaya / τ). Sinyal hanya berlaku untuk kanal daring (WA/FB).
  3. Klarifikasi: tekanan dari agen Z yang meluruskan + suntikan intervensi; terlihat → bisa berbalik ke Z.
  4. Agen E bersikap (I atau Z); penyebar dewasa bisa menjadi Siaga aktif (A) bila ada pemicu.
  5. Agen kunci yang menerima informasi baru diputuskan oleh LLM / tabel beku / aturan ABM.
  6. Indeks risiko distrik = (A / populasi) × kehadiran pemicu.

Bila skenario punya blok `provokator:` (lihat provocateur.py), buzzer menambah tekanan hoaks ke grup
sasaran pada langkah 2, provokator lapangan kebal klarifikasi, dan narasi tandingan mereka menurunkan
kepercayaan pada klarifikasi (langkah 3) serta efek klarifikasi saat warga bersikap (langkah 4).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import scipy.sparse as sp

from . import A, E, I, K_BERSIKAP, K_F2F, K_FB, K_INTERVENSI, K_KUNCI, K_RADIO, K_TANAM, K_WA, KANAL, S, STATUS, Z, TIPE_WILAYAH
from .config import HARI, code_version, jeda_deteksi, stable_hash
from .interventions import KELAS, build_interventions
from .keyagents import PERAN
from .llm.orchestrator import Decider, DecisionRequest
from .llm.rules import rule_decision
from .llm.schema import Decision
from .network import JADWAL, JENIS_NAMA, J
from .provocateur import build_provokator
from .world import World

DARI_JENIS = {
    "rumah_tangga": "keluarga serumah", "tetangga": "tetangga", "ibadah": "obrolan di gereja",
    "pasar": "obrolan di pasar", "adat": "pertemuan adat", "pangkalan": "pangkalan ojek",
    "perjalanan": "orang yang baru datang dari kota/pulau lain", "wa_keluarga": "grup WA keluarga",
    "wa_marga": "grup WA keluarga besar", "wa_jemaat": "grup WA jemaat", "wa_sekolah": "grup WA orang tua murid",
    "fb_info": "grup Facebook info kota", "radio": "radio",
}
LEAD_NONE, LEAD_ON, LEAD_OFF, LEAD_ALL = 0, 1, 2, 3
KANAL_LEAD = {"wa": LEAD_ON, "mimbar_ibadah": LEAD_ALL, "tatap_muka": LEAD_OFF, "tidak_ada": LEAD_NONE}


@dataclass
class RunResult:
    timeseries: np.ndarray            # (T+1, D, 5) jumlah agen per status per distrik
    risk: np.ndarray                  # (T+1, D)
    eskalasi_step: np.ndarray         # (D,) langkah pertama indeks risiko > ambang, -1 bila tidak
    events: list[dict]
    transitions: pd.DataFrame | None
    manifest: dict
    llm_log: list[dict] = field(default_factory=list)
    kanal_masuk: np.ndarray | None = None   # (2, 4 tipe, 8 kanal): [0] paparan hoaks (→E), [1] koreksi (→Z)
    paparan_provokator: np.ndarray | None = None   # (4 tipe,) paparan hoaks yang berasal dari provokator/buzzer


class Simulation:
    def __init__(self, world: World, scenario: dict, intervensi: dict | None = None, seed: int = 1,
                 decider: Decider | None = None, log_events: bool = True, viz_sample: int = 0,
                 kode_intervensi: str = "?"):
        self.w, self.p, self.sc, self.seed = world, world.params, scenario, seed
        self.kode = kode_intervensi
        self.rng = np.random.default_rng([seed, 1000])
        self.decider = decider or Decider(scenario["isu"].get("intensitas_emosi", 0.9))
        pop, net, key = world.pop, world.net, world.key
        n, G = pop.n, net.G
        self.n = n

        self.dt_min = int(scenario.get("langkah_menit", 30))
        self.dt_h = self.dt_min / 60
        self.T = int(round(scenario.get("durasi_jam", 48) * 60 / self.dt_min))
        self.scale = self.dt_min / 30.0
        self.start_day = HARI.index(scenario["mulai"]["hari"])
        self.start_hour = int(scenario["mulai"].get("jam", 7))
        self.tau = float(self.p["tau_jam"])

        # Status agen
        self.status = np.zeros(n, np.int8)
        self.t_percaya = np.full(n, -1e9, np.float32)
        self.t_paparan = np.full(n, -1, np.int32)
        self.t_koreksi = np.full(n, -1e9, np.float32)
        self.bobot_koreksi = np.zeros(n, np.float32)
        self.lihat_klar = np.zeros(n, bool)
        self.prebunk = np.zeros(n, bool)
        self.trust_pap = np.full(n, 0.8, np.float32)
        self.pernah_dengar = np.zeros(n, bool)
        self.atipe = world.geo.distrik_tipe[pop.distrik].astype(np.int64)
        self.kanal_masuk = np.zeros((2, 4, 8), np.int64)

        isu = scenario["isu"]
        self.emosi = float(isu.get("intensitas_emosi", 0.9))
        self.f_id = np.ones(n, np.float32)
        kelompok = {"orang_tua_anak_sekolah": pop.ortu_sekolah, "perempuan": pop.perempuan,
                    "laki_laki": ~pop.perempuan, "pemuda": (pop.umur >= 16) & (pop.umur <= 30),
                    "lansia": pop.umur >= 55, "pengguna_hp": pop.hp}
        for k, v in (isu.get("faktor_identitas") or {}).items():
            self.f_id[kelompok[k]] *= float(v)
        self.lit_f = (1 - float(self.p["efek_literasi"]) * pop.literasi).astype(np.float32)
        # skala_sinyal hanya untuk analisis sensitivitas: menggeser kekuatan sinyal semua kampung sekaligus
        self.sig = np.clip(pop.sinyal * float(self.p.get("skala_sinyal", 1.0)), 0, 1).astype(np.float32)
        self.dewasa = (pop.umur >= 16) & (pop.umur <= 65)

        # Atribut kelompok
        self.online = np.isin(net.kanal, (K_WA, K_FB))
        self.on_f, self.off_f = self.online.astype(np.float32), (~self.online).astype(np.float32)
        beta = self.p["beta"]
        beta_k = np.array([beta["wa"], beta["fb"], beta["tatap_muka"], beta["radio"]], np.float32)
        self.beta_g = beta_k[net.kanal]
        self.beta_g[net.jenis == J["radio"]] = 0.0           # warga tidak bersiaran lewat radio
        self.w_hoaks = self.beta_g * net.kepercayaan * 1.0
        self.ch_mask = {c: (net.kanal == c).astype(np.float32) for c in (K_WA, K_FB, K_F2F)}
        self.pasar_g = np.flatnonzero(net.jenis == J["pasar"])
        self.adat_g = np.flatnonzero(net.jenis == J["adat"])
        self.adat_open_until = np.full(G, -1, np.int32)
        self.ajakan_until = np.full(G, -1, np.int32)
        self.jadwal_idx = {nm: i for i, nm in enumerate(JADWAL)}
        self.jadwal_factor = np.zeros(len(JADWAL), np.float32)
        self.hour_act = 0.0
        self.radio_on_air = False

        # Agen kunci
        self.key_of = np.full(n, -1, np.int32)
        self.key_of[key.agen] = np.arange(key.k)
        self.is_key = self.key_of >= 0
        L = key.L.tocsc()
        self.L_on = sp.csr_matrix(L.multiply(self.on_f[None, :]))
        self.L_off = sp.csr_matrix(L.multiply(self.off_f[None, :]))
        self.LT_on, self.LT_off = self.L_on.T.tocsr(), self.L_off.T.tocsr()
        self.L_any = key.L.T.tocsr()
        self.k_lead = np.zeros(key.k, np.int8)
        self.k_conf = np.zeros(key.k, np.float32)
        self.k_tanya = np.zeros(key.k, np.int8)
        self.k_retry = np.full(key.k, -1, np.int32)
        self.k_klar: list[list[str]] = [[] for _ in range(key.k)]
        self.k_klar_tepercaya = np.zeros(key.k, bool)
        self.k_dari = ["" for _ in range(key.k)]
        self.k_notify: dict[int, bool] = {}
        self.k_decided = np.zeros(key.k, bool)
        self.k_planted = np.zeros(key.k, bool)

        # Wilayah & risiko
        geo = world.geo
        self.D = len(geo.distrik_nama)
        self.dpop = np.maximum(np.bincount(pop.distrik, minlength=self.D), 1)
        tipe_cfg = world.region["tipe_wilayah"]
        self.pemicu_d = np.array([tipe_cfg[TIPE_WILAYAH[t]]["pemicu"] for t in geo.distrik_tipe], np.float32)
        self.ambang = float(self.p["ambang_eskalasi"])
        self.eskalasi_step = np.full(self.D, -1, np.int32)
        for x in scenario.get("tanpa_sinyal") or []:      # R4: distrik yang saat ini tanpa sinyal (mengganti peta sinyal)
            self.sig[self._dmask(x)[pop.distrik]] = 0.0

        # Keluaran
        self.events: list[dict] = []
        self.log_events = log_events
        self._tr: list[tuple[int, np.ndarray, np.ndarray, np.ndarray, int]] = []
        self.ts = np.zeros((self.T + 1, self.D, 5), np.int32)
        self.risk = np.zeros((self.T + 1, self.D), np.float32)
        self.t = 0

        # Sampel visual: tetap per seed dunia, selalu termasuk agen kunci
        self.viz_idx = None
        if viz_sample:
            vr = np.random.default_rng([world.seed, 9])
            m = min(n, viz_sample)
            self.viz_idx = np.unique(np.concatenate([vr.choice(n, m, replace=False), key.agen]))
            self.viz_pos = {int(a): i for i, a in enumerate(self.viz_idx)}

        self.plants = self._plan_tanam()
        # F5: isu baru diketahui petugas setelah jeda; intervensi bisa dijadwalkan relatif terhadap deteksi
        t_muncul = min(float(tn.get("t_jam", 0)) for tn in scenario["tanam"])
        self.jeda_deteksi_jam = jeda_deteksi(scenario, self.p, self._tipe_asal())
        self.t_deteksi_jam = t_muncul + self.jeda_deteksi_jam
        self.t_deteksi = self.step_of_hour(self.t_deteksi_jam)
        self.sisipan: list[dict] = []        # F4: keputusan yang disisipkan saat berjalan (untuk jalur pembanding)
        self._n_tanam_sisip = 0
        self.prov = build_provokator(scenario, self)
        self.interventions = build_interventions(intervensi, self)
        self._record(0)

    def _dmask(self, spec: dict) -> np.ndarray:
        """Distrik sasaran dari {kabupaten: id | [id..], distrik: nama | [nama..]}; semua bila kosong."""
        geo = self.w.geo
        kab = spec.get("kabupaten")
        if not kab:
            return np.ones(self.D, bool)
        kabs = [kab] if isinstance(kab, str) else list(kab)
        dis = spec.get("distrik")
        if dis:
            dis = [dis] if isinstance(dis, str) else list(dis)
            m = np.zeros(self.D, bool)
            for nm in dis:
                for k in kabs:
                    try:
                        m[geo.distrik_index(k, nm)] = True
                        break
                    except KeyError:
                        continue
                else:
                    raise KeyError(f"Distrik '{nm}' tidak ada di {kabs}")
            return m
        return np.isin(geo.distrik_kab, [geo.kab_index(k) for k in kabs])

    def _tipe_asal(self) -> str | None:
        """Tipe wilayah tempat isu pertama ditanam (untuk jeda deteksi bawaan per tipe)."""
        tn = min(self.sc["tanam"], key=lambda x: float(x.get("t_jam", 0)))
        try:
            m = self._dmask(tn)
        except KeyError:
            return None
        if m.all():
            return None
        tipe = np.bincount(self.w.geo.distrik_tipe[m], weights=self.dpop[m], minlength=len(TIPE_WILAYAH))
        return TIPE_WILAYAH[int(tipe.argmax())]

    # ------------------------------------------------------------------ F4 penyisipan saat berjalan
    def tambah_intervensi(self, spec: dict, catat: bool = True):
        """Sisipkan satu langkah intervensi; `t_jam` relatif terhadap SEKARANG (bawaan 0 = langkah berikutnya)."""
        s = dict(spec)
        if s.get("jenis") not in KELAS:
            raise ValueError(f"jenis intervensi tidak dikenal: {s.get('jenis')}")
        rel = float(s.pop("t_jam", 0) or 0)
        s.pop("t_sejak_deteksi", None)
        t0 = max(self.t + 1, self.t + self.step_of_hour(rel))
        s["t_jam"] = t0 * self.dt_h
        iv = KELAS[s["jenis"]](s, self)
        iv.prepare(self)
        self.interventions.append(iv)
        if catat:
            self.sisipan.append({"t": self.t, "aksi": "intervensi", "spec": dict(spec)})
        return iv

    def tambah_tanam(self, spec: dict, catat: bool = True) -> None:
        """Sisipkan titik tanam baru (mis. video lama diunggah ulang); `t_jam` relatif terhadap sekarang."""
        s = dict(spec)
        rel = float(s.pop("t_jam", 0) or 0)
        s["t_jam"] = 0
        tt = self.t + self.step_of_hour(rel)
        self._n_tanam_sisip += 1
        (_, agents, kanal), = self._plan_tanam([s], np.random.default_rng([self.seed, 502, self._n_tanam_sisip]))
        self.plants.append((tt, agents, kanal))
        if catat:
            self.sisipan.append({"t": self.t, "aksi": "tanam", "spec": dict(spec)})

    # ------------------------------------------------------------------ waktu
    def step_of_hour(self, jam: float) -> int:
        return int(round(jam * 60 / self.dt_min))

    def clock(self, t: int) -> tuple[int, int, int]:
        minutes = self.start_hour * 60 + t * self.dt_min
        day = (self.start_day + minutes // 1440) % 7
        return day, (minutes // 60) % 24, minutes % 60

    def label_waktu(self, t: int) -> str:
        d, h, m = self.clock(t)
        return f"{HARI[d]} {h:02d}.{m:02d} (T+{t * self.dt_h:g} jam)"

    def _jadwal(self, t: int) -> None:
        day, hour, _ = self.clock(t)
        jd = self.p["jadwal"]
        self.hour_act = float(self.p["aktivitas_jam"][hour])
        self.radio_on_air = False
        f = self.jadwal_factor
        f[self.jadwal_idx["selalu"]] = self.hour_act
        ib = (HARI[day] in jd["ibadah"]["hari"] and hour in jd["ibadah"]["jam"]) or \
             (HARI[day] in jd["ibadah_rabu"]["hari"] and hour in jd["ibadah_rabu"]["jam"])
        f[self.jadwal_idx["ibadah"]] = 1.0 if ib else 0.0
        f[self.jadwal_idx["pasar"]] = 1.0 if hour in jd["pasar"]["jam"] else 0.0
        f[self.jadwal_idx["adat"]] = 1.0 if 7 <= hour <= 21 else 0.0
        f[self.jadwal_idx["pangkalan"]] = 1.0 if hour in jd["pangkalan"]["jam"] else 0.0
        f[self.jadwal_idx["radio_dengar"]] = 1.0 if hour in jd["radio_dengar"]["jam"] else 0.0
        net = self.w.net
        open_g = f[net.jadwal].copy()
        open_g[self.pasar_g] *= net.hari_pasar[net.distrik[self.pasar_g], day]
        open_g[self.adat_g] *= self.adat_open_until[self.adat_g] >= t
        self.open_g = open_g
        self.day, self.hour = day, hour

    # ------------------------------------------------------------------ tanam isu
    def _plan_tanam(self, daftar: list[dict] | None = None, rng=None) -> list[tuple[int, np.ndarray, int]]:
        pop, net, geo, key = self.w.pop, self.w.net, self.w.geo, self.w.key
        rng = rng or np.random.default_rng([self.seed, 501])
        plan = []
        for tn in (self.sc["tanam"] if daftar is None else daftar):
            t = self.step_of_hour(tn.get("t_jam", 0))
            kab = geo.kab_index(tn["kabupaten"]) if "kabupaten" in tn else None
            dmask = np.ones(self.D, bool)
            if kab is not None:
                dmask = geo.distrik_kab == kab
            if "distrik" in tn:
                dmask = np.zeros(self.D, bool)
                dmask[geo.distrik_index(tn["kabupaten"], tn["distrik"])] = True
            if tn.get("akun_medsos"):
                roles = (key.peran == PERAN.index("akun_medsos"))
                cand_k = np.flatnonzero(roles & dmask[pop.distrik[key.agen]])
                k = min(int(tn["akun_medsos"]), len(cand_k))
                chosen = key.agen[rng.choice(cand_k, k, replace=False)] if k else np.empty(0, np.int64)
                if len(chosen) < tn["akun_medsos"]:
                    extra = np.flatnonzero(pop.fb & dmask[pop.distrik] & ~self.is_key)
                    chosen = np.r_[chosen, rng.choice(extra, int(tn["akun_medsos"]) - len(chosen), replace=False)]
                plan.append((t, chosen.astype(np.int64), K_FB))
                continue
            if tn["kanal"] in ("wa", "fb"):
                jen = {"sekolah": "wa_sekolah", "keluarga": "wa_keluarga", "jemaat": "wa_jemaat",
                       "marga": "wa_marga", "info": "fb_info"}[tn.get("jenis_grup", "keluarga" if tn["kanal"] == "wa" else "info")]
                gs = np.flatnonzero((net.jenis == J[jen]) & dmask[net.distrik] & (net.ukuran >= 3))
                gs = rng.choice(gs, min(int(tn.get("jumlah_grup", 1)), len(gs)), replace=False)
                seeds = [int(rng.choice(net.members_of(int(g)))) for g in gs]
                plan.append((t, np.asarray(seeds, np.int64), K_WA if tn["kanal"] == "wa" else K_FB))
            else:
                cand = np.flatnonzero(dmask[pop.distrik] & self.dewasa)
                plan.append((t, rng.choice(cand, min(int(tn.get("jumlah", 5)), len(cand)), replace=False), K_F2F))
        return plan

    # ------------------------------------------------------------------ utilitas status
    def _set(self, agents: np.ndarray, new: int, kanal: int | np.ndarray) -> None:
        if len(agents) == 0:
            return
        old = self.status[agents].copy()
        self.status[agents] = new
        if new in (E, Z):
            kan = np.broadcast_to(np.asarray(kanal, np.int64), agents.shape)
            self.kanal_masuk[int(new == Z)] += np.bincount(self.atipe[agents] * 8 + kan, minlength=32).reshape(4, 8)
        if self.log_events:
            kan = np.broadcast_to(np.asarray(kanal, np.int8), agents.shape).copy()
            self._tr.append((self.t, agents.astype(np.int32), old, kan, new))

    def event(self, t: int, jenis: str, teks: str, **extra) -> None:
        self.events.append({"t": t, "waktu": self.label_waktu(t), "jenis": jenis, "teks": teks, **extra})

    def key_notify_clarification(self, ki: int, label: str, sumber: str = "", tepercaya: bool = False) -> None:
        baru = label not in self.k_klar[ki]
        if baru:
            self.k_klar[ki].append(label)
        self.k_klar_tepercaya[ki] |= tepercaya
        self.lihat_klar[self.w.key.agen[ki]] = True
        # LLM hanya dipanggil bila ada informasi baru
        if baru and self.status[self.w.key.agen[ki]] != Z:
            self.k_notify[ki] = True

    # ------------------------------------------------------------------ satu langkah
    def step(self) -> dict | None:
        self.t += 1
        t = self.t
        self._jadwal(t)
        net, pop, key = self.w.net, self.w.pop, self.w.key
        frame_arcs: list[list[int]] = []

        for tt, agents, kanal in self.plants:
            if tt == t - 1:
                agents = agents[self.status[agents] != Z]
                self._set(agents, I, K_TANAM)
                self.t_percaya[agents] = t
                self.pernah_dengar[agents] = True
                ks = self.key_of[agents]
                for ki in ks[ks >= 0]:
                    self.k_planted[ki] = self.k_decided[ki] = True
                    self.k_lead[ki], self.k_conf[ki] = LEAD_ALL, 0.95
                self.event(t, "tanam", f"Isu ditanam di {len(agents)} titik ({KANAL[kanal]})")
        if t == self.t_deteksi:
            self.event(t, "deteksi", f"Isu diketahui petugas (jeda deteksi {self.jeda_deteksi_jam:g} jam)")

        prov = self.prov
        if prov is not None:
            prov.on_step(t)
        injections = [inj for iv in self.interventions if (inj := iv.on_step(self, t)) is not None]

        # --- 2. paparan hoaks
        st = self.status
        spreading = (st == I) | (st == A)
        act = np.zeros(self.n, np.float32)
        idx = np.flatnonzero(spreading)
        act[idx] = np.exp(-(t - self.t_percaya[idx]) * self.dt_h / self.tau)
        P = self._group_pressure(act, self.k_conf * ((st[key.agen] == I) | (st[key.agen] == A)), self.w_hoaks)
        Pp = prov.pressure(t) if prov is not None else None
        if Pp is not None:
            P = P + Pp
        lam = self._agent_pressure(P)
        lam *= self.f_id * self.lit_f
        lam[self.prebunk] *= float(self.p["prebunking"]["paparan"])
        sus = np.flatnonzero(st == S)
        new_e = sus[self.rng.random(len(sus)) < -np.expm1(-lam[sus])]
        if len(new_e):
            kanal_e, trust_e, src_g = self._attribute(new_e, P)
            if prov is not None and (Pp is not None or prov.terkunci.any()):
                # Atribusi ke provokator sebanding porsi tekanannya: buzzer (Pp) lalu provokator lapangan.
                lam_all = np.maximum(self._agent_pressure(P)[new_e], 1e-12)
                s_b = self._agent_pressure(Pp)[new_e] / lam_all if Pp is not None else np.zeros(len(new_e))
                if prov.terkunci.any():
                    Pl = self._group_pressure(act * prov.terkunci, np.zeros(key.k, np.float32), self.w_hoaks)
                    s_l = self._agent_pressure(Pl)[new_e] / lam_all
                else:
                    s_l = np.zeros(len(new_e))
                u = self.rng.random(len(new_e))
                dari_b = u < s_b
                prov.catat_paparan(new_e[dari_b | (u < s_b + s_l)])
                # akun anonim kurang dipercaya; akun terkoordinasi memberi kesan konsensus (lihat provocateur.py)
                trust_e[dari_b] = np.minimum(1.0, trust_e[dari_b] * prov.kredibilitas(t))
            self._set(new_e, E, kanal_e)
            self.t_paparan[new_e] = t
            self.pernah_dengar[new_e] = True
            self.trust_pap[new_e] = trust_e
            frame_arcs += self._arcs(new_e, src_g, act, 0)
            for a, g in zip(new_e[self.is_key[new_e]], src_g[self.is_key[new_e]]):
                self.k_dari[self.key_of[a]] = DARI_JENIS[JENIS_NAMA[net.jenis[g]]] if g >= 0 else "kabar dari orang"

        # --- 3. klarifikasi
        corr = np.zeros(self.n, np.float32)
        zi = np.flatnonzero((st == Z) & (self.bobot_koreksi > 0))
        corr[zi] = self.bobot_koreksi[zi] * np.exp(-(t - self.t_koreksi[zi]) * self.dt_h / (self.tau * 1.5))
        key_corr = (st[key.agen] == Z) * (self.bobot_koreksi[key.agen] > 0) * 1.0
        Pc = self._group_pressure(corr, key_corr, self.beta_g)
        seen_lam = self._agent_pressure(Pc)
        trust_lam = self._agent_pressure(Pc * net.kepercayaan)
        inj_seen = np.zeros(self.n, np.float32)
        for inj in injections:
            seen_lam += inj.seen * self.scale
            trust_lam += inj.trust * self.scale
            inj_seen += inj.seen * self.scale
        serang = None
        if prov is not None:
            if (seen_lam > 0).any():
                prov.catat_klarifikasi(t)
            serang = prov.serangan(t)
            if serang is not None:
                trust_lam *= 1 - serang                          # narasi tandingan: "klarifikasi itu bohong"
        cand = np.flatnonzero((st != Z) & (seen_lam > 0) & (~prov.terkunci if prov is not None else True))
        seen = cand[self.rng.random(len(cand)) < -np.expm1(-seen_lam[cand])]
        if len(seen):
            seen_key = seen[self.is_key[seen]]
            for a in seen_key:
                ki = int(self.key_of[a])
                if inj_seen[a] >= 0.5 * seen_lam[a] and injections:
                    best = max(injections, key=lambda x: x.seen[a])
                    self.key_notify_clarification(ki, best.label, tepercaya=best.jenis == "radio")
                else:
                    self.key_notify_clarification(ki, "Ada warga/tokoh yang meluruskan di grup: itu hoaks")
            seen = seen[~self.is_key[seen]]
            pers = self.p["persuasi_koreksi"]
            pers_v = np.array([pers["S"], pers["E"], pers["I"], 0.0, pers["A"]], np.float32)
            trust_avg = np.clip(trust_lam[seen] / np.maximum(seen_lam[seen], 1e-12), 0, 1)
            p_conv = pers_v[st[seen]] * trust_avg * np.where(self.prebunk[seen], 1.3, 1.0)
            conv = seen[self.rng.random(len(seen)) < np.clip(p_conv, 0, 1)]
            self.lihat_klar[seen] = True
            if len(conv):
                kan_p, _, src_c = self._attribute(conv, Pc)
                kan = np.where(inj_seen[conv] >= 0.5 * seen_lam[conv], K_INTERVENSI, kan_p)
                # yang belum pernah dengar hoaks hanya "tahu itu hoaks" (Z pasif), tidak ikut meluruskan
                fresh = st[conv] == S
                self._make_z(conv[fresh], kan[fresh], p_aktif=0.0)
                self._make_z(conv[~fresh], kan[~fresh])
                peer = kan != K_INTERVENSI
                frame_arcs += self._arcs(conv[peer], src_c[peer], corr, 1, limit=60)

        # --- 4. agen E bersikap (agen biasa)
        cand = np.flatnonzero((st == E) & (self.t_paparan < t) & ~self.is_key)
        res = cand[self.rng.random(len(cand)) < float(self.p["laju_bersikap"]) * self.scale]
        if len(res):
            p_bel = (float(self.p["kredibilitas_dasar"]) * (0.5 + 0.5 * self.emosi) * self.trust_pap[res]
                     * self.lit_f[res]
                     * np.where(self.prebunk[res], float(self.p["prebunking"]["percaya"]), 1.0)
                     * np.where(self.lihat_klar[res], 1 - float(self.p["efek_klarifikasi_bersikap"])
                                * (1 - serang[res] if serang is not None else 1.0), 1.0))
            bel = self.rng.random(len(res)) < p_bel
            self._set(res[bel], I, K_BERSIKAP)
            self.t_percaya[res[bel]] = t
            self._make_z(res[~bel], K_BERSIKAP)

        # --- 4b. I → A (siaga aktif)
        cand = np.flatnonzero((st == I) & self.dewasa & ~self.is_key)
        if len(cand):
            ajak = self.ajakan_until >= t
            mobil = (net.M @ ajak.astype(np.float32))[cand] > 0 if ajak.any() else np.zeros(len(cand), bool)
            bonus = 1.5 if self.sc["isu"].get("ajakan_tindakan") else 1.0
            p_a = (float(self.p["peluang_siaga"]) * self.scale * self.f_id[cand] * bonus
                   * self.pemicu_d[pop.distrik[cand]] * max(self.hour_act, 0.2) + 0.25 * self.scale * mobil)
            self._set(cand[self.rng.random(len(cand)) < p_a], A, K_BERSIKAP)

        # --- 5. agen kunci
        self._key_decisions(t)

        self._record(t)
        return self._frame(t, frame_arcs) if self.viz_idx is not None else None

    def _make_z(self, agents: np.ndarray, kanal, p_aktif: float | None = None) -> None:
        if len(agents) == 0:
            return
        self._set(agents, Z, kanal)
        p = float(self.p["peluang_korektor"]) if p_aktif is None else p_aktif
        active = agents[self.rng.random(len(agents)) < p]
        self.bobot_koreksi[active] = float(self.p["bobot_koreksi_aktif"])
        self.t_koreksi[active] = self.t

    def _group_pressure(self, act: np.ndarray, key_weight: np.ndarray, w_g: np.ndarray) -> np.ndarray:
        net, key = self.w.net, self.w.key
        g_on = net.MT @ (act * self.sig)
        g_off = net.MT @ act
        g = np.where(self.online, g_on, g_off) * net.baca
        ka = act[key.agen] * key_weight
        lead_on = np.isin(self.k_lead, (LEAD_ON, LEAD_ALL)) * ka
        lead_off = np.isin(self.k_lead, (LEAD_OFF, LEAD_ALL)) * ka
        g += self.LT_on @ (lead_on * self.sig[key.agen]) + self.LT_off @ lead_off
        return (g * w_g * self.open_g * self.scale).astype(np.float32)

    def _agent_pressure(self, P: np.ndarray) -> np.ndarray:
        M = self.w.net.M
        return (M @ (P * self.on_f)) * self.sig + M @ (P * self.off_f)

    def _attribute(self, rows: np.ndarray, P: np.ndarray):
        """Kanal paparan, kepercayaan sumber, dan satu kelompok sumber untuk tiap agen baru."""
        net = self.w.net
        R = net.M[rows]
        Rp = (R.multiply((P * self.on_f)[None, :]).multiply(self.sig[rows][:, None])
              + R.multiply((P * self.off_f)[None, :])).tocsr()
        Rp.eliminate_zeros()
        tot = np.asarray(Rp.sum(1)).ravel()
        tr = np.asarray(Rp @ net.kepercayaan).ravel() / np.maximum(tot, 1e-12)
        # sampel satu kelompok sumber per baris ∝ tekanan (vektorisasi lewat cumsum global)
        src = np.full(len(rows), -1, np.int64)
        has = np.diff(Rp.indptr) > 0
        if Rp.nnz:
            c = np.cumsum(Rp.data, dtype=np.float64)
            start = Rp.indptr[:-1]
            base = np.where(start > 0, c[np.maximum(start - 1, 0)], 0.0)
            pos = np.searchsorted(c, base + self.rng.random(len(rows)) * tot, side="right")
            pos = np.clip(pos, start, np.maximum(Rp.indptr[1:] - 1, start))
            src[has] = Rp.indices[pos[has]]
        kanal = np.where(src >= 0, net.kanal[np.maximum(src, 0)], K_F2F).astype(np.int8)
        return kanal, np.where(tot > 0, tr, 0.8).astype(np.float32), src

    def _arcs(self, rows, src_g, act, jenis: int, limit: int = 120) -> list[list[int]]:
        if self.viz_idx is None or len(rows) == 0:
            return []
        net, pop = self.w.net, self.w.pop
        out = []
        sel = np.arange(len(rows))
        if len(sel) > limit:
            sel = self.rng.choice(len(rows), limit, replace=False)
        for r in sel:
            g = src_g[r]
            if g < 0:
                continue
            mem = net.members_of(int(g))
            a = act[mem]
            leaders = self.L_any.indices[self.L_any.indptr[g]:self.L_any.indptr[g + 1]]
            if a.sum() > 0:
                srcs = mem[a > 0]
                src_agent = int(srcs[int(self.rng.integers(len(srcs)))])
            elif len(leaders):
                src_agent = int(self.w.key.agen[leaders[0]])
            else:
                continue
            out.append([int(pop.kampung[src_agent]), int(pop.kampung[rows[r]]), jenis, int(net.kanal[g])])
        return out

    # ------------------------------------------------------------------ agen kunci
    def _key_decisions(self, t: int) -> None:
        key, st = self.w.key, self.status
        kst = st[key.agen]
        todo = set(np.flatnonzero((kst == E) & ~self.k_decided).tolist())
        todo |= set(np.flatnonzero(self.k_retry == t).tolist())
        todo |= set(self.k_notify)
        self.k_notify = {}
        todo = sorted(ki for ki in todo if not self.k_planted[ki] or self.k_klar[ki])
        if not todo:
            return
        reqs = [self._key_request(ki, t) for ki in todo]
        decisions = self.decider.decide_batch(reqs, self.rng)
        for ki, d in zip(todo, decisions):
            self._apply_decision(ki, d, t)

    def _key_request(self, ki: int, t: int) -> DecisionRequest:
        persona = self.w.key.persona[ki]
        day, hour, _ = self.clock(t)
        hari = f"{HARI[day]} jam {hour:02d}"
        if HARI[day] == "Sabtu":
            hari += ", besok ibadah"
        elif HARI[day] == "Minggu" and hour < 12:
            hari += ", hari ini ibadah"
        a = self.w.key.agen[ki]
        dengar = self.pernah_dengar[a]
        percaya, meluruskan = self._sekitar(a)
        konteks = {
            "kabar_masuk": self.sc["isu"]["teks"] if dengar else "(belum dengar langsung; hanya dengar klarifikasinya)",
            "dari": self.k_dari[ki] or ("kabar dari orang" if dengar else "-"),
            "komentar": list(self.sc.get("komentar_contoh", []))[:2],
            "orang_sekitar_yang_percaya_dan_meneruskan": percaya,
            "orang_sekitar_yang_bilang_hoaks": meluruskan,
            "klarifikasi_terlihat": list(self.k_klar[ki]),
            "hari": hari,
        }
        disc = {"dari": konteks["dari"], "klar": sorted(self.k_klar[ki]), "dengar": bool(dengar),
                "percaya": percaya, "lurus": meluruskan,
                "hari": HARI[day] if HARI[day] in ("Sabtu", "Minggu") else "biasa",
                "tanya": int(self.k_tanya[ki]), "isu": stable_hash(self.sc["isu"]["teks"])}
        petunjuk = {"klarifikasi_tepercaya": bool(self.k_klar_tepercaya[ki]), "sudah_tanya": int(self.k_tanya[ki]),
                    "dekat_ibadah": HARI[day] in ("Sabtu", "Minggu")}
        return DecisionRequest(ki, persona, konteks, disc, petunjuk)

    def _sekitar(self, a: int) -> tuple[str, str]:
        """Tekanan sosial untuk konteks LLM: berapa kontak di kelompok agen yang percaya vs meluruskan (dibuat diskret)."""
        net = self.w.net
        gs = net.groups_of(a)
        gs = gs[net.kanal[gs] != K_RADIO]
        if len(gs) == 0:
            return "tidak ada", "tidak ada"
        mem = np.unique(np.concatenate([net.members_of(int(g)) for g in gs]))
        mem = mem[mem != a]
        st = self.status[mem]
        n_p = int(((st == I) | (st == A)).sum())
        n_z = int(((st == Z) & (self.bobot_koreksi[mem] > 0)).sum())

        def ember(n: int) -> str:
            return "tidak ada" if n == 0 else "1–2 orang" if n <= 2 else "beberapa orang (3–9)" if n < 10 else "banyak orang (10+)"
        return ember(n_p), ember(n_z)

    def _apply_decision(self, ki: int, d, t: int) -> None:
        key, net = self.w.key, self.w.net
        a = np.array([key.agen[ki]])
        k = d.keputusan
        self.k_decided[ki] = True
        self.k_retry[ki] = -1
        new = d.status()
        if new == E:
            self.k_tanya[ki] += 1
            if self.k_tanya[ki] >= 3:
                # masih ragu setelah 3 kali: serahkan ke aturan peluang ABM sesuai peran, bukan dipaksa skeptis
                req = self._key_request(ki, t)
                k = rule_decision(req.persona, req.konteks_aturan, self.rng, self.emosi)
                d = Decision(k, "abm", model="aturan-abm", error="ragu 3×, diputuskan aturan ABM")
                new = d.status()
            else:
                # tanya ulang 2 jam lagi (atau lebih cepat bila ada klarifikasi baru)
                self.k_retry[ki] = t + self.step_of_hour(2)
        if new == E and self.status[a[0]] == S:
            self._set(a, E, K_KUNCI)
        lead = KANAL_LEAD[k.kanal]
        groups = key.L.indices[key.L.indptr[ki]:key.L.indptr[ki + 1]]
        if new in (I, A):
            self._set(a, new, K_KUNCI)
            self.t_percaya[a] = t
            self.k_lead[ki], self.k_conf[ki] = lead, max(k.keyakinan, 0.3)
            if k.ajakan_jaga_kampung:
                self.ajakan_until[groups] = t + self.step_of_hour(8)
        elif new == Z:
            self._set(a, Z, K_KUNCI)
            self.t_koreksi[a] = t
            lurus = k.aksi == "luruskan"
            self.bobot_koreksi[a] = float(self.p["bobot_koreksi_tinggi"]) if lurus else 0.0
            self.k_lead[ki] = lead if lurus else LEAD_NONE
            self.k_conf[ki] = 1.0
            self.ajakan_until[groups] = -1
        if k.kanal == "tatap_muka" and new != E:
            adat = groups[net.jenis[groups] == J["adat"]]
            self.adat_open_until[adat] = t + self.step_of_hour(3)
        p = key.persona[ki]
        self.event(t, "agen_kunci", f"{p['nama']} ({p['peran']}, {p['wilayah']}): {k.aksi}"
                   + (" + ajak jaga kampung" if k.ajakan_jaga_kampung else ""),
                   ki=ki, agen=int(a[0]), sumber=d.sumber, keputusan=k.model_dump(), raw=d.raw, error=d.error,
                   kampung=int(self.w.pop.kampung[a[0]]))

    # ------------------------------------------------------------------ pencatatan
    def _record(self, t: int) -> None:
        pop = self.w.pop
        key = pop.distrik.astype(np.int64) * 5 + self.status
        self.ts[t] = np.bincount(key, minlength=self.D * 5).reshape(self.D, 5)
        r = self.ts[t, :, A] / self.dpop * self.pemicu_d
        self.risk[t] = r
        newly = (r > self.ambang) & (self.eskalasi_step < 0)
        self.eskalasi_step[newly] = t

    def _frame(self, t: int, arcs: list) -> dict:
        pop, geo = self.w.pop, self.w.geo
        V = len(geo.kampung_nama)
        kk = np.bincount(pop.kampung.astype(np.int64) * 5 + self.status, minlength=V * 5).reshape(V, 5)
        ev = [e for e in self.events if e["t"] == t]
        radio = []
        if self.radio_on_air and any(iv.jenis == "radio" and iv.t0 <= t < iv.t1 for iv in self.interventions):
            radio = [s["id"] for s in self.w.region.get("radio", [])]
        return {
            "t": t, "waktu": self.label_waktu(t), "jumlah": self.ts[t].sum(0).tolist(),
            "kampung": kk[:, 1:].T.tolist(),          # E, I, Z, A per kampung
            "risiko": np.round(self.risk[t], 5).tolist(),
            "arcs": arcs[:200], "radio": radio,
            "sampel": self.status[self.viz_idx].tolist(),
            "events": [{k: v for k, v in e.items()} for e in ev],
        }

    def frame0(self) -> dict:
        self._jadwal(0)
        return self._frame(0, [])

    # ------------------------------------------------------------------ jalankan
    def run(self, callback=None) -> RunResult:
        t0 = time.perf_counter()
        if callback and self.viz_idx is not None:
            callback(self.frame0())
        for _ in range(self.T):
            fr = self.step()
            if callback:
                if callback(fr) is False:
                    break
        return self.result(time.perf_counter() - t0)

    def result(self, seconds: float = 0.0) -> RunResult:
        tr = None
        if self.log_events and self._tr:
            tr = pd.DataFrame({
                "langkah": np.concatenate([np.full(len(a), t, np.int16) for t, a, *_ in self._tr]),
                "agen": np.concatenate([a for _, a, *_ in self._tr]),
                "dari": np.concatenate([o for _, _, o, *_ in self._tr]).astype(np.int8),
                "ke": np.concatenate([np.full(len(a), nw, np.int8) for _, a, _, _, nw in self._tr]),
                "kanal": np.concatenate([k for _, _, _, k, _ in self._tr]),
            })
        manifest = {
            "seed": self.seed, "seed_dunia": self.w.seed, "agen": self.n, "intervensi": self.kode,
            "skenario": self.sc.get("kode", self.sc["nama"]), "hash_skenario": stable_hash(self.sc),
            "langkah": self.t, "langkah_menit": self.dt_min, "versi_kode": code_version(),
            "llm": self.decider.stats(), "detik": round(seconds, 2),
            "jeda_deteksi_jam": self.jeda_deteksi_jam, "t_deteksi_jam": self.t_deteksi_jam,
            **({"sisipan": self.sisipan} if self.sisipan else {}),
            **({"provokator": self.prov.ringkasan()} if self.prov is not None else {}),
        }
        log = getattr(self.decider, "log", [])
        return RunResult(self.ts[: self.t + 1].copy(), self.risk[: self.t + 1].copy(), self.eskalasi_step.copy(),
                         self.events, tr, manifest, list(log), self.kanal_masuk.copy(),
                         self.prov.paparan.copy() if self.prov is not None else None)


STATUS_NAMES = STATUS
