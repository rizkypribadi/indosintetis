"""R5 Mode Latihan Meja (tabletop): melatih koordinasi antarlembaga dalam situasi hoaks yang realistis tanpa
risiko nyata, sekaligus mengumpulkan penilaian kewajaran dari peserta sebagai data validasi.

  - Simulasi 48–72 jam dipadatkan menjadi 6–8 ronde. Fasilitator memajukan ronde; tim mengambil keputusan.
  - Peserta hanya melihat KABUT INFORMASI (`laporan_untuk_peserta`): laporan dari distrik yang siaga aktifnya
    melewati ambang, terlambat 1–3 jam, sebagian keliru atau tidak lengkap, dan tidak ada laporan dari distrik
    tanpa sinyal kecuali lewat tim keliling.
  - Keputusan tim langsung disisipkan ke simulasi yang sedang berjalan (F4) dengan batas sumber daya (R4).
  - Debrief: tiga jalur pada seed yang sama (tanpa tindakan, keputusan tim, rencana terbaik), keputusan
    paling menentukan, ringkasan penilaian kewajaran, dan PDF.

Log disimpan di out/latihan/<sesi>/: keputusan.json, suntikan.json, penilaian.json, debrief.pdf.
Penilaian dan catatan peserta disimpan tanpa nama dan hanya dipakai sebagai data penelitian bila peserta setuju.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from . import A, I
from .config import OUT_DIR, ROOT, TANDA_SIMULASI, load_interventions, load_scenario, load_yaml, versi_model
from .engine import Simulation
from .perencana import muat_profil, opsi
from .world import build_world

SKRIP_DIR = ROOT / "latihan"
NILAI = ("ya", "sebagian", "tidak")


def daftar_skrip() -> list[dict]:
    return [{"berkas": p.name, "nama": (load_yaml(p) or {}).get("nama", p.stem)} for p in sorted(SKRIP_DIR.glob("skrip_*.yaml"))]


def _anonim(teks: str) -> str:
    """Catatan peserta tanpa nomor telepon, akun, atau tautan."""
    return re.sub(r"https?://\S+|@\w+|\+?\d[\d\s\-]{7,}\d", "[dihapus]", str(teks or ""))[:500]


def laporan_untuk_peserta(sim: Simulation, t: int, terakhir: dict, dikunjungi: set, rng: np.random.Generator,
                          porsi_keliru: float = 0.15) -> list[dict]:
    """Laporan yang wajar sampai ke tim pada langkah t: bertunda, sebagian keliru, tanpa distrik tak bersinyal
    kecuali yang dikunjungi tim keliling. `terakhir` (distrik → level) diperbarui di tempat."""
    geo, pop = sim.w.geo, sim.w.pop
    amb = sim.ambang
    sig_d = np.bincount(pop.distrik, weights=sim.sig > 0, minlength=sim.D) / sim.dpop
    out = []
    for d in range(sim.D):
        tunda = int(rng.integers(2, 7))                    # 1–3 jam (langkah 30 menit)
        tt = t - tunda
        if tt < 1:
            continue
        r = float(sim.risk[tt, d]) / amb
        level = 0 if r < 0.5 else 1 if r < 1.5 else 2 if r < 4 else 3
        if level == 0 or level <= terakhir.get(d, 0):
            continue
        lewat_tim = d in dikunjungi
        if sig_d[d] < 0.3 and not lewat_tim:
            continue                                        # tidak ada sinyal, tidak ada tim: kabar tidak sampai
        terakhir[d] = level
        dd = d
        if rng.random() < porsi_keliru:                     # salah distrik (masih satu kabupaten)
            sama = np.flatnonzero(geo.distrik_kab == geo.distrik_kab[d])
            dd = int(rng.choice(sama))
        lv = level
        if rng.random() < porsi_keliru:                     # dilebih-lebihkan atau dikecilkan
            lv = int(np.clip(level + rng.choice([-1, 1]), 1, 3))
        teks = {1: "warga mulai ramai membicarakan kabar itu; sebagian mengaku akan berjaga",
                2: "ada kelompok warga berjaga di jalan masuk kampung dan memeriksa orang baru",
                3: "kerumunan besar; ada ajakan menghadang orang yang tidak dikenal"}[lv]
        sumber = "tim keliling" if lewat_tim else str(rng.choice(["pesan WA warga", "Bhabinkamtibmas", "kepala kampung"]))
        out.append({"jam_kejadian": round(tt * sim.dt_h, 1), "jam_diterima": round(t * sim.dt_h, 1),
                    "distrik": geo.distrik_nama[dd], "kabupaten": geo.kab_nama[geo.distrik_kab[dd]], "teks": teks,
                    "sumber": sumber, "level": lv})
    return out


class SesiLatihan:
    def __init__(self, skenario: dict | str | Path, profil: dict | str | Path, skrip: dict | str | Path | None = None,
                 agen: int = 20_000, seed: int = 1, ronde: int = 6, jam_ronde: float | None = None, nama: str | None = None,
                 rencana_terbaik: dict | None = None, seed_dunia: int = 42):
        self.id = nama or f"latihan_{datetime.now():%Y%m%d-%H%M%S}"
        self.sc = copy.deepcopy(skenario if isinstance(skenario, dict) else load_scenario(skenario))
        self.profil = muat_profil(profil)
        if skrip is None:
            self.skrip = {"nama": "tanpa skrip", "suntikan": []}
        elif isinstance(skrip, dict):
            self.skrip = skrip
        else:
            p = Path(skrip)
            self.skrip = load_yaml(p if p.exists() else SKRIP_DIR / str(skrip))
        self.agen, self.seed, self.seed_dunia = agen, seed, seed_dunia
        self.n_ronde = int(ronde)
        self.jam_ronde = float(jam_ronde or self.sc.get("durasi_jam", 48) / self.n_ronde)
        self.w = build_world(agen, seed_dunia)
        self.sim = Simulation(self.w, self.sc, None, seed=seed, log_events=False, kode_intervensi="latihan")
        self.ops = {o["id"]: o for o in opsi({**self.profil, "batas_waktu_jam": 1e9})}
        self.sisa = {"tokoh": sum(int(t["jumlah"]) for t in self.profil.get("tokoh") or []),
                     "tim": int((self.profil.get("tim_keliling") or {}).get("orang", 0)),
                     "radio": {r["stasiun"] for r in self.profil.get("radio") or []},
                     "medsos": bool((self.profil.get("medsos") or {}).get("ada"))}
        self.gangguan: dict = {"radio_mati": {}}
        self.ronde_ke = 0
        self.keputusan: list[dict] = []
        self.suntikan: list[dict] = []
        self.penilaian: list[dict] = []
        self.laporan: list[dict] = []
        self._terakhir: dict = {}
        self._skrip_sudah: set = set()
        self.rencana_terbaik = rencana_terbaik
        self.persetujuan = False
        self.dir = OUT_DIR / "latihan" / self.id
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ waktu & pilihan
    @property
    def jam(self) -> float:
        return self.sim.t * self.sim.dt_h

    @property
    def selesai(self) -> bool:
        return self.ronde_ke >= self.n_ronde or self.sim.t >= self.sim.T

    def pilihan(self) -> list[dict]:
        out = []
        for oid, o in self.ops.items():
            j = o["jenis"]
            if j == "tokoh":
                maks = self.sisa["tokoh"]
            elif j == "tim":
                maks = self.sisa["tim"]
            elif j == "radio":
                st = o["spec"]["stasiun"][0]
                if st not in self.sisa["radio"]:
                    continue
                mati = self.gangguan["radio_mati"].get(st)
                if mati is not None and self.jam < mati:
                    out.append({"id": oid, "label": o["label"], "tersedia": False, "alasan": f"gangguan sampai jam {mati:g}"})
                    continue
                maks = 1
            else:
                maks = int(self.sisa["medsos"])
            if maks > 0:
                out.append({"id": oid, "label": o["label"], "jenis": j, "maks": maks, "tersedia": True,
                            "perlu_jumlah": j in ("tokoh", "tim")})
        return out

    # ------------------------------------------------------------------ aksi
    def putuskan(self, aksi: str, jumlah: int | None = None, distrik: list[str] | None = None, catatan: str = "") -> dict:
        if self.selesai:
            raise ValueError("Latihan sudah selesai")
        p = next((x for x in self.pilihan() if x["id"] == aksi), None)
        if p is None or not p.get("tersedia"):
            raise ValueError(f"Aksi '{aksi}' tidak tersedia (sumber daya habis atau gangguan)")
        o = self.ops[aksi]
        spec = dict(o["spec"])
        spec.pop("t_sejak_deteksi", None)
        j = o["jenis"]
        n = None
        if j in ("tokoh", "tim"):
            n = int(jumlah or p["maks"])
            if not 1 <= n <= p["maks"]:
                raise ValueError(f"Jumlah harus 1..{p['maks']}")
            spec["jumlah_tokoh" if j == "tokoh" else "orang"] = n
        if distrik and spec.get("kabupaten"):
            spec["distrik"] = list(distrik)
        spec["t_jam"] = 0
        try:
            self.sim.tambah_intervensi(spec)       # sumber daya baru dikurangi bila penyisipan berhasil
        except KeyError as e:
            raise ValueError(f"Distrik tidak dikenal untuk langkah ini: {e}") from None
        if n is not None:
            self.sisa[j] -= n
        elif j == "radio":
            self.sisa["radio"].discard(spec["stasiun"][0])
        else:
            self.sisa["medsos"] = False
        k = {"ronde": self.ronde_ke + 1, "jam": round(self.jam, 1), "aksi": aksi, "jenis": j, "label": o["label"],
             "jumlah": spec.get("jumlah_tokoh") or spec.get("orang"), "distrik": distrik, "catatan": _anonim(catatan),
             "langkah_sim": self.sim.t}
        self.keputusan.append(k)
        self._simpan()
        return k

    def suntik(self, s: dict, otomatis: bool = False) -> dict:
        ef = s.get("efek") or {}
        if "tanam" in ef:
            self.sim.tambah_tanam(dict(ef["tanam"]))
        if "radio_mati" in ef:
            r = ef["radio_mati"]
            self.gangguan["radio_mati"][r["stasiun"]] = self.jam + float(r.get("jam", 6))
        if "tokoh_tidak_bisa" in ef:
            self.sisa["tokoh"] = max(0, self.sisa["tokoh"] - int(ef["tokoh_tidak_bisa"]))
        rec = {"jam": round(self.jam, 1), "teks": str(s.get("teks", "")), "efek": ef, "otomatis": otomatis,
               "langkah_sim": self.sim.t, "ronde": self.ronde_ke + 1}
        self.suntikan.append(rec)
        self._simpan()
        return rec

    def ronde_berikut(self) -> dict:
        if self.selesai:
            raise ValueError("Latihan sudah selesai")
        akhir = min(self.sim.T, self.sim.t + self.sim.step_of_hour(self.jam_ronde))
        rng = np.random.default_rng([self.seed, 900, self.ronde_ke])
        baru = []
        while self.sim.t < akhir:
            for i, s in enumerate(self.skrip.get("suntikan") or []):
                if i not in self._skrip_sudah and float(s.get("jam", 1e9)) <= self.jam:
                    self._skrip_sudah.add(i)
                    self.suntik(s, otomatis=True)
            self.sim.step()
            if self.sim.t % 2 == 0:                         # kabar dikumpulkan tiap jam
                baru += laporan_untuk_peserta(self.sim, self.sim.t, self._terakhir, self._dikunjungi(), rng)
        self.ronde_ke += 1
        for b in baru:
            b["ronde"] = self.ronde_ke
        self.laporan += baru
        self._simpan()
        return {"ronde": self.ronde_ke, "laporan_baru": len(baru)}

    def nilai(self, ronde: int, nilai: str, catatan: str = "", peran: str = "peserta") -> dict:
        if nilai not in NILAI:
            raise ValueError(f"nilai harus {NILAI}")
        r = {"ronde": int(ronde), "nilai": nilai, "catatan": _anonim(catatan), "peran": re.sub(r"[^\w /]", "", peran)[:40],
             "versi_model": versi_model()["id"], "persetujuan": self.persetujuan}
        self.penilaian.append(r)
        self._simpan()
        return r

    def _dikunjungi(self) -> set:
        geo = self.w.geo
        out = set()
        for iv in self.sim.interventions:
            if iv.jenis == "tim_keliling":
                out |= set(geo.kampung_distrik[np.flatnonzero(iv.sudah)].tolist())
        return out

    # ------------------------------------------------------------------ status per peran
    def status(self, peran: str = "peserta") -> dict:
        base = {"id": self.id, "skenario": self.sc.get("nama"), "kabupaten": self.profil["kabupaten"],
                "tanda": TANDA_SIMULASI, "ronde": self.ronde_ke,
                "n_ronde": self.n_ronde, "jam": round(self.jam, 1), "jam_ronde": self.jam_ronde, "selesai": self.selesai,
                "laporan": self.laporan[-40:], "kabar": [{"jam": s["jam"], "teks": s["teks"]} for s in self.suntikan],
                "pilihan": self.pilihan(), "sisa": {**self.sisa, "radio": sorted(self.sisa["radio"])},
                "keputusan": self.keputusan, "penilaian_ronde": [p["ronde"] for p in self.penilaian],
                "persetujuan": self.persetujuan, "versi_model": versi_model()["teks"]}
        if peran != "fasilitator":
            return base
        sim, geo = self.sim, self.w.geo
        tot = sim.ts[: sim.t + 1].sum(1)
        N = float(sim.dpop.sum())
        dist = sorted(({"distrik": geo.distrik_nama[d], "kabupaten": geo.kab_nama[geo.distrik_kab[d]],
                        "siaga_permil": round(float(sim.ts[sim.t, d, A] / sim.dpop[d] * 1000), 1),
                        "risiko_x_ambang": round(float(sim.risk[sim.t, d] / sim.ambang), 2),
                        "memanas": bool(sim.eskalasi_step[d] >= 0)} for d in range(sim.D)),
                      key=lambda x: -x["risiko_x_ambang"])[:12]
        return {**base, "fasilitator": {
            "kurva": {"jam": (np.arange(sim.t + 1) * sim.dt_h).round(1).tolist(),
                      "penyebar": (tot[:, I] / N).round(5).tolist(), "siaga": (tot[:, A] / N).round(5).tolist()},
            "distrik": dist, "skrip": self.skrip.get("suntikan") or [], "skrip_sudah": sorted(self._skrip_sudah),
            "penilaian": self.penilaian, "gangguan": self.gangguan}}

    # ------------------------------------------------------------------ debrief
    def _jalur(self, sisipan: list[dict], awal: dict | None = None) -> Simulation:
        sim = Simulation(self.w, self.sc, awal, seed=self.seed, log_events=False, kode_intervensi="jalur")
        by_t: dict[int, list] = {}
        for s in sisipan:
            by_t.setdefault(int(s["t"]), []).append(s)
        for _ in range(self.sim.T):
            for s in by_t.get(sim.t, []):
                (sim.tambah_intervensi if s["aksi"] == "intervensi" else sim.tambah_tanam)(dict(s["spec"]))
            sim.step()
        return sim

    def _rencana_pembanding(self) -> tuple[str, dict]:
        if self.rencana_terbaik:
            return self.rencana_terbaik.get("nama", "rencana tersimpan"), {"langkah": self.rencana_terbaik["langkah"]}
        from .perencana import rencana_tersimpan

        rs = rencana_tersimpan(self.profil["kabupaten"])
        if rs:
            return f"rencana tersimpan: {rs[0]['nama']}", {"langkah": rs[0]["langkah"]}
        # tanpa rencana tersimpan: semua sumber daya profil pada jam paling cepatnya (rencana 'pakai semua')
        lk = []
        for o in self.ops.values():
            s = dict(o["spec"])
            s["t_sejak_deteksi"] = o["jam"]
            lk.append(s)
        return "semua sumber daya pada jam paling cepat", {"langkah": lk}

    def debrief(self) -> dict:
        tanam = [s for s in self.sim.sisipan if s["aksi"] == "tanam"]
        jalur = {"tanpa": ("Tanpa tindakan", self._jalur(tanam)),
                 "tim": ("Keputusan tim", self._jalur(self.sim.sisipan))}
        nm, rspec = self._rencana_pembanding()
        jalur["terbaik"] = (f"Rencana terbaik ({nm})", self._jalur(tanam, rspec))

        def ringkas(sim):
            tot = sim.ts.sum(1)
            N = float(sim.dpop.sum())
            return {"beban_siaga_per_1000": round(float(tot[:, A].sum() * sim.dt_h / N * 1000), 1),
                    "puncak_siaga_permil": round(float(tot[:, A].max() / N * 1000), 2),
                    "distrik_memanas": int((sim.eskalasi_step >= 0).sum()),
                    "kurva_siaga": (tot[:, A] / N).round(5).tolist()}

        hasil = {k: {"nama": v[0], **ringkas(v[1])} for k, v in jalur.items()}
        identik = bool(np.array_equal(jalur["tim"][1].ts[: self.sim.t + 1], self.sim.ts[: self.sim.t + 1]))
        # keputusan paling menentukan: jalur tim tanpa satu keputusan
        nilai_k = []
        ints = [i for i, s in enumerate(self.sim.sisipan) if s["aksi"] == "intervensi"]
        for n, i in enumerate(ints[:10]):        # keputusan ke-n ↔ sisipan intervensi ke-n (urutan sama)
            sim = self._jalur([s for j, s in enumerate(self.sim.sisipan) if j != i])
            naik = ringkas(sim)["beban_siaga_per_1000"] - hasil["tim"]["beban_siaga_per_1000"]
            k = self.keputusan[n] if n < len(self.keputusan) else {}
            nilai_k.append({"jam": round(self.sim.sisipan[i]["t"] * self.sim.dt_h, 1), "label": k.get("label", "?"),
                            "beban_naik_bila_tidak_dilakukan": round(naik, 1)})
        nilai_k.sort(key=lambda x: -x["beban_naik_bila_tidak_dilakukan"])
        rek = {n: sum(p["nilai"] == n for p in self.penilaian) for n in NILAI}
        out = {"id": self.id, "tanda": TANDA_SIMULASI, "versi_model": versi_model(), "skenario": self.sc.get("nama"),
               "seed": self.seed, "jalur": hasil, "replay_identik": identik, "keputusan": self.keputusan,
               "suntikan": self.suntikan, "keputusan_menentukan": nilai_k, "penilaian_ringkas": rek,
               "penilaian": self.penilaian, "dt_jam": self.sim.dt_h,
               "catatan": ("Perbandingan memakai satu seed (kejadian yang sama persis untuk ketiga jalur). Selisih kecil "
                           "(±10%) bisa kebetulan; pakai Perencana atau Monte Carlo untuk kesimpulan.")}
        (self.dir / "debrief.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        try:
            out["pdf"] = str(pdf_debrief(out, self.dir / "debrief.pdf"))
        except Exception as e:         # PDF gagal tidak boleh menghapus debrief
            out["pdf_error"] = f"{type(e).__name__}: {e}"
        return out

    def _simpan(self) -> None:
        for nama, isi in (("keputusan", self.keputusan), ("suntikan", self.suntikan), ("penilaian", self.penilaian)):
            (self.dir / f"{nama}.json").write_text(json.dumps(isi, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def pdf_debrief(d: dict, path: Path) -> Path:
    import io

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib import colors

    from .laporan_pdf import _setup_fonts

    _setup_fonts()
    st = {"h1": ParagraphStyle("h1", fontName="Sans-Bold", fontSize=16, leading=20),
          "h2": ParagraphStyle("h2", fontName="Sans-Bold", fontSize=12, leading=15, spaceBefore=8, spaceAfter=3),
          "b": ParagraphStyle("b", fontName="Sans", fontSize=10, leading=13)}
    fig, ax = plt.subplots(figsize=(7, 3))
    gaya = {"tanpa": ("#888888", ":"), "tim": ("#000000", "-"), "terbaik": ("#444444", "--")}
    for k, v in d["jalur"].items():
        x = np.arange(len(v["kurva_siaga"])) * d["dt_jam"]
        ax.plot(x, np.array(v["kurva_siaga"]) * 1000, color=gaya[k][0], ls=gaya[k][1], lw=1.6, label=v["nama"])
    for k in d["keputusan"]:
        ax.axvline(k["jam"], color="#000000", lw=0.6, alpha=0.5)
    ax.set_xlabel("jam sejak isu beredar (garis tegak = keputusan tim)")
    ax.set_ylabel("siaga aktif per 1.000")
    ax.legend(frameon=False, fontsize=8)
    ax.grid(alpha=0.3)
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=170)
    plt.close(fig)
    buf.seek(0)
    img = Image(buf, width=175 * mm, height=75 * mm)
    rows = [["Jalur", "Beban siaga /1.000", "Puncak siaga ‰", "Distrik memanas"]]
    rows += [[v["nama"], f"{v['beban_siaga_per_1000']:.0f}", f"{v['puncak_siaga_permil']:.1f}", str(v["distrik_memanas"])]
             for v in d["jalur"].values()]
    t = Table(rows, colWidths=[80 * mm, 32 * mm, 30 * mm, 30 * mm])
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "Sans"), ("FONTSIZE", (0, 0), (-1, -1), 10),
                           ("FONTNAME", (0, 0), (-1, 0), "Sans-Bold"), ("GRID", (0, 0), (-1, -1), 0.6, colors.black)]))
    kp = [["Jam", "Keputusan", "Jumlah", "Catatan"]] + [[f"{k['jam']:g}", Paragraph(k["label"], st["b"]), str(k.get("jumlah") or ""),
                                                        Paragraph(k.get("catatan") or "", st["b"])] for k in d["keputusan"]]
    tk = Table(kp, colWidths=[15 * mm, 85 * mm, 18 * mm, 55 * mm])
    tk.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "Sans"), ("FONTSIZE", (0, 0), (-1, -1), 10),
                            ("FONTNAME", (0, 0), (-1, 0), "Sans-Bold"), ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story = [Paragraph(f"Debrief latihan meja · {d['skenario']}", st["h1"]),
             Paragraph(f"<b>{TANDA_SIMULASI}</b> · skenario fiktif · {d['versi_model']['teks']}", st["b"]), Spacer(1, 6),
             Paragraph("Tiga jalur pada seed yang sama", st["h2"]), t, Paragraph(d.get("catatan", ""), st["b"]),
             Spacer(1, 4), img,
             Paragraph("Linimasa keputusan tim", st["h2"]), tk]
    if d["keputusan_menentukan"]:
        k = d["keputusan_menentukan"][0]
        story += [Paragraph("Keputusan paling menentukan", st["h2"]),
                  Paragraph(f"Jam {k['jam']:g}: {k['label']}. Tanpa keputusan ini, beban siaga naik ±{k['beban_naik_bila_tidak_dilakukan']:.0f} "
                            "orang-jam per 1.000 (pada seed yang sama).", st["b"])]
    pr = d["penilaian_ringkas"]
    story += [Paragraph("Penilaian kewajaran oleh peserta", st["h2"]),
              Paragraph(f"\"Apakah reaksi warga masuk akal?\" ya {pr['ya']}, sebagian {pr['sebagian']}, tidak {pr['tidak']}.", st["b"])]
    story += [Paragraph(f"Ronde {p['ronde']} ({p['nilai']}): {p['catatan']}", st["b"]) for p in d["penilaian"] if p["catatan"]]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                      bottomMargin=16 * mm, title="Debrief latihan meja").build(story)
    return path


def buat_sesi(body: dict) -> SesiLatihan:
    """Dari formulir fasilitator: skenario (berkas di scenarios/ atau sel pustaka), profil sumber daya, skrip."""
    from . import pustaka
    from .config import SCENARIO_DIR

    sk = str(body.get("skenario") or "")
    if "__" in sk:
        sc = pustaka.skenario_sel(sk)
        sc["durasi_jam"] = int(body.get("durasi_jam", 72))
    else:
        if not re.fullmatch(r"[\w.\-]+\.yaml", sk or "x"):
            raise ValueError("skenario tidak valid")
        sc = load_scenario(SCENARIO_DIR / sk)
    prof = str(body.get("profil") or "")
    if not re.fullmatch(r"[\w\-]+", prof):
        raise ValueError("profil tidak valid")
    skrip = body.get("skrip") or None
    if skrip and not re.fullmatch(r"skrip_[\w\-]+\.yaml", skrip):
        raise ValueError("skrip tidak valid")
    return SesiLatihan(sc, prof, skrip, agen=int(body.get("agen", 20_000)), seed=int(body.get("seed", 1)),
                       ronde=max(2, min(int(body.get("ronde", 6)), 12)))


__all__ = ["SesiLatihan", "laporan_untuk_peserta", "buat_sesi", "daftar_skrip", "load_interventions"]
