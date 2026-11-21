"""Uji kriteria penerimaan MVP (PRD §15) pada skala kecil."""
import copy

import numpy as np
import pytest

from sintetis import A, E, I, K_F2F, K_FB, K_WA, S, Z
from sintetis.config import load_interventions, load_region, load_scenario
from sintetis.engine import Simulation
from sintetis.llm.cache import DecisionCache
from sintetis.llm.orchestrator import LLMDecider, _extract_json
from sintetis.llm.schema import Decision, Keputusan
from sintetis.world import build_world

N = 20_000


@pytest.fixture(scope="module")
def world():
    return build_world(N, 42, use_cache=False)


@pytest.fixture(scope="module")
def sc():
    return load_scenario()


def run(world, sc, code="A", seed=1, **kw):
    sim = Simulation(world, sc, load_interventions()[code], seed=seed, **kw)
    return sim, sim.run()


def test_populasi_9_kabupaten_dan_seed_tetap(world):
    assert len(world.geo.kab_id) == 9
    assert world.pop.n == N
    w2 = build_world(N, 42, use_cache=False)
    assert np.array_equal(world.pop.kampung, w2.pop.kampung)
    assert (world.net.M != w2.net.M).nnz == 0
    assert np.array_equal(world.key.agen, w2.key.agen)


def test_hasil_identik_untuk_seed_sama(world, sc):
    _, r1 = run(world, sc, "D", seed=5)
    _, r2 = run(world, sc, "D", seed=5)
    assert np.array_equal(r1.timeseries, r2.timeseries)
    assert r1.transitions.equals(r2.transitions)
    _, r3 = run(world, sc, "D", seed=6)
    assert not np.array_equal(r1.timeseries, r3.timeseries)


def test_kanal_luring_tidak_bergantung_sinyal(sc):
    region = copy.deepcopy(load_region())
    for k in region["kabupaten"]:
        k["sinyal"] = 0.0
        k["sinyal_lemah"] = 0.0
    w = build_world(N, 42, region=region, use_cache=False)
    assert (w.geo.kampung_sinyal == 0).all()
    sim, res = run(w, sc, "A", seed=1)
    tr = res.transitions
    paparan = tr[(tr["dari"] == S) & (tr["ke"] == E)]
    assert len(paparan) > 0
    # tanpa sinyal, tidak ada paparan lewat WA/FB; semua lewat tatap muka
    assert not paparan["kanal"].isin([K_WA, K_FB]).any()
    assert (paparan["kanal"] == K_F2F).all()


def test_kanal_daring_mengikuti_sinyal(world, sc):
    sim, res = run(world, sc, "A", seed=2)
    tr = res.transitions
    on = tr[(tr["dari"] == S) & (tr["ke"] == E) & tr["kanal"].isin([K_WA, K_FB])]
    assert len(on) > 0
    assert (world.pop.sinyal[on["agen"].to_numpy()] > 0).all()


def test_intervensi_radio_mengurangi_siaga(world, sc):
    a = np.mean([run(world, sc, "A", s, log_events=False)[1].timeseries[:, :, A].sum() for s in range(4)])
    f = np.mean([run(world, sc, "F", s, log_events=False)[1].timeseries[:, :, A].sum() for s in range(4)])
    assert f < a


def test_skema_dan_pemetaan_llm():
    k = Keputusan(aksi="teruskan", kanal="wa", keyakinan=0.8, ajakan_jaga_kampung=True, pesan_tambahan="", alasan_singkat="")
    assert Decision(k, "llm").status() == A
    assert Decision(k.model_copy(update={"ajakan_jaga_kampung": False}), "llm").status() == I
    assert Decision(k.model_copy(update={"aksi": "luruskan"}), "llm").status() == Z
    assert Decision(k.model_copy(update={"aksi": "tanya_dulu"}), "llm").status() == E
    with pytest.raises(Exception):
        Keputusan(aksi="serang", kanal="wa", keyakinan=0.5, ajakan_jaga_kampung=False)
    with pytest.raises(Exception):
        Keputusan(aksi="abaikan", kanal="wa", keyakinan=0.5, ajakan_jaga_kampung=False, pesan_tambahan="x" * 281)
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_cache_dan_pembekuan(world, sc, tmp_path):
    cache = DecisionCache(tmp_path / "c.sqlite")
    dec = LLMDecider("mock", cache=cache)
    sim = Simulation(world, sc, load_interventions()["D"], seed=1, decider=dec)
    r1 = sim.run()
    assert dec.hitung["llm"] > 0 and dec.biaya.panggilan == dec.hitung["llm"]
    n = cache.freeze("uji")
    assert n == cache.stats()["entri"] > 0
    beku = LLMDecider("mock", cache=cache, beku=cache.load_frozen("uji"))
    r2 = Simulation(world, sc, load_interventions()["D"], seed=1, decider=beku).run()
    assert beku.hitung["llm"] == 0 and beku.hitung["beku_miss"] == 0
    assert np.array_equal(r1.timeseries, r2.timeseries)


def test_skenario_bebas_dirapikan_dan_faktor_identitas(world, sc):
    from sintetis.llm.asisten import rapikan_skenario

    raw = {"nama": "Hoaks beras plastik di Wamena", "mulai": {"hari": "Senin", "jam": 30},
           "isu": {"teks": "Jang beli beras bantuan e, su campur plastik!", "intensitas_emosi": 1.7,
                   "faktor_identitas": {"perempuan": 1.6, "suku_x": 9}},
           "tanam": [{"kanal": "wa", "jenis_grup": "jemaat", "kabupaten": world.geo.kab_nama[1], "distrik": "tidak ada"},
                     {"kanal": "fb", "akun_medsos": 3, "kabupaten": world.geo.kab_id[2], "t_jam": 8},
                     {"kanal": "radio"}],
           "catatan": "asumsi"}
    out = rapikan_skenario(raw, world.geo)
    assert out["mulai"]["jam"] == 23 and out["isu"]["intensitas_emosi"] == 1
    assert out["isu"]["faktor_identitas"] == {"perempuan": 1.6}
    assert out["tanam"][0]["kabupaten"] == world.geo.kab_id[1] and "distrik" not in out["tanam"][0]
    assert out["tanam"][1]["akun_medsos"] == 3 and out["tanam"][2]["kanal"] == "wa"
    sim, res = run(world, out, log_events=False)
    assert res.timeseries[-1, :, S].sum() < N
    bad = copy.deepcopy(sc)
    bad["isu"]["faktor_identitas"] = {"suku_x": 2}
    with pytest.raises(ValueError):
        from sintetis.config import validate_scenario
        validate_scenario(bad)


def test_laporan_ai_mock_dari_putaran(world, sc):
    from sintetis.llm.asisten import ringkas_putaran, tulis_laporan
    from sintetis.report import md_to_html

    sim, res = run(world, sc, "D", log_events=False)
    f = ringkas_putaran(res.timeseries, res.risk, res.eskalasi_step, res.events, res.manifest, world.geo,
                        sim.dpop, sim.dt_h, sc, "D", float(sim.pernah_dengar.mean()))
    assert set(f["per_tipe_wilayah"]) <= {"kota", "pesisir", "pedalaman", "kepulauan"}
    assert 0 < f["semua_wilayah"]["puncak_penyebar"] <= 1 and f["deret_proporsi_tiap_6_jam"][-1]["jam"] == 48
    md, info = tulis_laporan({"putaran": [f]}, "putaran", "mock")
    assert info["penyedia"] == "mock" and "Puncak penyebar" in md
    assert "<h2>" in md_to_html(md)


def test_kanal_masuk_dan_prompt_per_skenario(world, sc):
    from sintetis.llm.prompt import prompt_hash, system_prompt

    sim, res = run(world, sc, "F", log_events=False)
    papar, koreksi = res.kanal_masuk[0].sum(0), res.kanal_masuk[1].sum(0)
    assert papar[[K_WA, K_F2F]].min() > 0 and papar.sum() >= sim.pernah_dengar.sum() - 50
    assert koreksi.sum() > 0
    assert prompt_hash() == prompt_hash(None) == prompt_hash("")      # cache lama tetap berlaku
    assert prompt_hash("karena takut jatuh sakit") != prompt_hash()
    assert "karena takut jatuh sakit" in system_prompt("karena takut jatuh sakit")


def test_sensitivitas_kecil(tmp_path, monkeypatch, sc):
    import json

    from sintetis import sensitivity

    monkeypatch.setattr(sensitivity, "OUT_DIR", tmp_path)
    monkeypatch.setattr(sensitivity, "load_yaml", lambda p: {"rentang": {"peluang_siaga": [0.05, 0.2], "skala_sinyal": [0.6, 1.3]}})
    d = sensitivity.run_sensitivity(5000, 42, putaran=1, sampel=4, kode="AD", scenario=sc, workers=1, progress=None)
    h = json.loads((d / "sensitivitas.json").read_text(encoding="utf-8"))
    assert len(h["satu_per_satu"]) == 4 and h["global"]["sampel"] == 4
    rendah, tinggi = (r["baseline"] for r in h["satu_per_satu"][:2])
    assert tinggi > rendah                      # peluang siaga lebih besar → beban siaga lebih besar
    assert "Analisis sensitivitas" in (d / "sensitivitas.md").read_text(encoding="utf-8")


def test_kesiapan_dan_periksa_kampung(tmp_path, monkeypatch):
    from sintetis import readiness

    butir = {r["butir"]: r["status"] for r in readiness.check()}
    assert "Geografi kampung" in butir and set(butir.values()) <= {"siap", "perlu", "opsional"}
    monkeypatch.setattr(readiness, "DATA_DIR", tmp_path)
    assert readiness.periksa_kampung() == []
    (tmp_path / "kampung.csv").write_text("kabupaten_id,distrik,kampung,lon,lat,penduduk\nkota_jayapura,A,K1,140.7,-2.5,100\n", encoding="utf-8")
    pesan = readiness.periksa_kampung()
    assert "PERLU DIPERBAIKI" in pesan[0] and any("tidak ada kampung untuk kabupaten" in p for p in pesan)


def test_kalibrasi_abc_dan_montecarlo_posterior(tmp_path, monkeypatch, sc):
    import json

    import pandas as pd

    from sintetis import calibration, montecarlo
    from sintetis.analysis import paired_effect

    monkeypatch.setattr(calibration, "OUT_DIR", tmp_path)
    monkeypatch.setattr(montecarlo, "OUT_DIR", tmp_path)
    rentang = {"beta.wa": [0.2, 0.8], "peluang_siaga": [0.03, 0.25]}
    d = calibration.calibrate_abc(5000, 42, sampel=8, putaran=1, terima=0.25, rentang=rentang, scenario=sc,
                                  workers=1, nama="post_uji", progress=None)
    h = json.loads((d / "posterior.json").read_text(encoding="utf-8"))
    df = pd.read_parquet(d / "sampel.parquet")
    assert h["diterima"] == len(h["set_parameter"]) == 2 and df["diterima"].sum() == 2
    assert df.loc[df["diterima"], "galat"].max() <= df.loc[~df["diterima"], "galat"].min()
    for s in h["set_parameter"]:
        assert all(rentang[k][0] <= s[k] <= rentang[k][1] for k in rentang)
    assert "Kalibrasi ABC" in (d / "kalibrasi_abc.md").read_text(encoding="utf-8")
    assert set(h["cakupan_prior"]) == set(h["target"]) and 0 < h["ambang_derau_penyempitan"] < 1
    jauh = {"target": {"jam_ke_eskalasi": 500.0}, "bobot": {}, "kasus": "uji"}
    d2 = calibration.calibrate_abc(5000, 42, sampel=3, putaran=1, terima=0.5, rujukan=jauh, rentang=rentang,
                                   scenario=sc, workers=1, nama="post_jauh", progress=None)
    assert "target di luar jangkauan model" in (d2 / "kalibrasi_abc.md").read_text(encoding="utf-8")

    kw = dict(n=5000, seed_dunia=42, putaran=3, kode="AD", scenario=sc, workers=1, progress=None)
    m1 = montecarlo.run_montecarlo(**kw, posterior=d, nama="mc_post")
    m2 = montecarlo.run_montecarlo(**kw, posterior="post_uji", nama="mc_post2")
    m0 = montecarlo.run_montecarlo(**kw, nama="mc_nominal")
    r1, r2, r0 = (pd.read_parquet(m / "hasil.parquet") for m in (m1, m2, m0))
    assert r1.equals(r2)                                   # posterior + seed sama → hasil identik
    assert not r1["siaga_jam_per_1000"].equals(r0["siaga_jam_per_1000"])   # parameter posterior benar-benar dipakai
    man = json.loads((m1 / "manifest.json").read_text(encoding="utf-8"))
    assert len(man["posterior"]["indeks_per_seed"]) == 3 and man["posterior"]["set_tersedia"] == 2
    assert json.loads((m0 / "manifest.json").read_text(encoding="utf-8"))["posterior"] is None
    assert len(paired_effect(r1)) > 0


def test_penduga_sobol_fungsi_analitik():
    from sintetis.sensitivity import sobol_indeks

    rng = np.random.default_rng(1)
    N = 4096
    A_, B_ = rng.random((N, 3)), rng.random((N, 3))
    f = lambda X: 4 * X[:, 0] + X[:, 1] + 3 * X[:, 0] * X[:, 1] * 0   # noqa: E731  x3 tidak berpengaruh
    fAB = []
    for i in range(3):
        ab = A_.copy(); ab[:, i] = B_[:, i]; fAB.append(f(ab))
    ind = sobol_indeks(f(A_), f(B_), np.array(fAB), n_boot=50)
    assert abs(ind["S1"][0] - 16 / 17) < 0.05 and abs(ind["S1"][1] - 1 / 17) < 0.03
    assert abs(ind["ST"][2]) < 0.01 and ind["ST_lo"][0] <= ind["ST"][0] <= ind["ST_hi"][0]


def test_sobol_kecil(tmp_path, monkeypatch, sc):
    import json

    from sintetis import sensitivity

    monkeypatch.setattr(sensitivity, "OUT_DIR", tmp_path)
    rentang = {"peluang_siaga": [0.05, 0.2], "skala_sinyal": [0.6, 1.3]}
    d = sensitivity.run_sobol(5000, 42, n_dasar=4, kode="D", scenario=sc, workers=1, seed_per_konfig=2,
                              n_boot=20, rentang=rentang, progress=None)
    h = json.loads((d / "sobol.json").read_text(encoding="utf-8"))
    assert h["N"] == 4 and set(h["target"]) == {"beban_siaga_baseline_A", "efek_D_persen"}
    assert set(h["target"]["beban_siaga_baseline_A"]["parameter"]) == set(rentang)
    assert h["pengaturan"]["intervensi"] == "AD"
    t = h["target"]["beban_siaga_baseline_A"]
    assert t["lantai_derau"] >= 0 and all(0 <= q["ST_bersih"] <= max(q["ST"], 0) for q in t["parameter"].values())
    assert "Indeks Sobol" in (d / "sobol.md").read_text(encoding="utf-8")


def test_pdf_laporan_ai(world, sc, tmp_path):
    from sintetis.laporan_pdf import build_pdf
    from sintetis.llm.asisten import ringkas_putaran, tanpa_grafik, tulis_laporan

    runs = []
    for code in "AD":
        sim, res = run(world, sc, code, log_events=False)
        runs.append(ringkas_putaran(res.timeseries, res.risk, res.eskalasi_step, res.events, res.manifest, world.geo,
                                    sim.dpop, sim.dt_h, sc, code, float(sim.pernah_dengar.mean()), res.kanal_masuk))
    fakta = {"putaran": runs}
    assert "_grafik" in runs[0] and "_grafik" not in tanpa_grafik(fakta)["putaran"][0]
    md, info = tulis_laporan(fakta, "putaran", "mock")
    md += "\n## Dinamika penyebaran\n\n- teks **tebal** dan | tabel\n\n| a | b |\n|---|---|\n| 1 | 2,5% |\n\n> catatan"
    out = build_pdf(md, fakta, "putaran", info, tmp_path / "lap.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 50_000 and data.count(b"/Type /Page") >= 3


def test_alur_otomatis_sampai_pdf_lapangan(tmp_path, monkeypatch, sc):
    import json

    import pandas as pd

    from sintetis import alur, montecarlo
    from sintetis.llm import cache

    for mod in (alur, montecarlo, cache):
        monkeypatch.setattr(mod, "OUT_DIR", tmp_path)
    tahap = []
    h = alur.jalankan_alur(sc, 5000, 42, "mock", pilot=1, eksplorasi=1, putaran=4, kode="AD", penyedia_laporan="mock",
                           workers=1, nama="alur_uji", progress=None, tahap=lambda t, s, c="": tahap.append((t, s)))
    assert [t for t, s in tahap if s == "selesai"] == ["eksplorasi", "bekukan", "montecarlo", "peta", "laporan"]
    d = tmp_path / "alur_uji"
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["tabel_beku"] == "alur_uji_beku" and man["mode_llm"] == "mock"
    pdf = (tmp_path / h["laporan_pdf"]).read_bytes()
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page") >= 3
    assert set(h["log_peta"]) == {"A", "D"} and all((tmp_path / "runs" / n / "manifest.json").exists() for n in h["log_peta"].values())
    # putaran contoh identik dengan baris Monte Carlo pada seed yang sama (peta tidak bertentangan dengan statistik)
    df = pd.read_parquet(d / "hasil.parquet")
    lap = json.loads((d / h["laporan_md"].split("/")[-1].replace(".md", ".json")).read_text(encoding="utf-8"))
    for r in lap["fakta"]["putaran_contoh"]["putaran"]:
        mc = df[(df["seed"] == h["seed_peta"]) & (df["tipe"] == "semua") & (df["intervensi"] == r["intervensi"]["kode"])]
        assert abs(mc["siaga_jam_per_1000"].iat[0] - r["semua_wilayah"]["siaga_jam_per_1000"]) < 0.06
    md = (tmp_path / h["laporan_md"]).read_text(encoding="utf-8")
    assert "## Langkah paling ampuh per tipe wilayah" in md and "semua" in lap["fakta"]["kartu_tindakan_per_tipe_wilayah"]



# ------------------------------------------------------------------ provokator & buzzer
@pytest.fixture(scope="module")
def sc_prov():
    from sintetis.config import SCENARIO_DIR

    return load_scenario(SCENARIO_DIR / "isu_sentani_provokator.yaml")


def test_tanpa_provokator_penindakan_tidak_mengubah_apa_pun(world, sc):
    sim_a, ra = run(world, sc, "A", seed=3, log_events=False)
    sim_g, rg = run(world, sc, "G", seed=3, log_events=False)
    assert sim_a.prov is None and ra.paparan_provokator is None
    assert np.array_equal(ra.timeseries, rg.timeseries)        # tanpa provokator: G identik dengan A


def test_provokator_menambah_penyebaran_dan_tercatat(world, sc, sc_prov):
    dengar0 = np.mean([run(world, sc, "A", s, log_events=False)[0].pernah_dengar.mean() for s in range(3)])
    sims = [run(world, sc_prov, "A", s, log_events=False) for s in range(3)]
    dengar1 = np.mean([sim.pernah_dengar.mean() for sim, _ in sims])
    assert dengar1 > dengar0
    for sim, res in sims:
        assert res.paparan_provokator.sum() > 0
        assert res.paparan_provokator.sum() <= res.kanal_masuk[0].sum()
        assert {b["nama"] for b in res.manifest["provokator"]["buzzer"]} == {"Jaringan akun anonim", "Penerus di grup WA keluarga"}


def test_provokator_lokal_kebal_klarifikasi_lalu_bisa_ditindak(world, sc_prov):
    from sintetis.engine import Simulation

    iv = load_interventions()
    sim = Simulation(world, sc_prov, iv["D"], seed=1, log_events=False, kode_intervensi="D")
    lk = sim.prov.lokal[0]
    for _ in range(sim.step_of_hour(30)):                       # jauh sesudah klarifikasi gereja/adat T+12
        sim.step()
    assert len(lk.agen) == 4 and (sim.status[lk.agen] == A).all()
    assert (sim.ajakan_until[world.net.groups_of(int(lk.agen[0]))] >= sim.t).all()
    # penindakan T+6: provokator yang mulai T+8 belum bisa dikenali, jadi tidak ikut ditindak
    sim_g = Simulation(world, sc_prov, iv["G"], seed=1, log_events=False, kode_intervensi="G")
    for _ in range(sim_g.step_of_hour(10)):
        sim_g.step()
    assert sim_g.prov.lokal[0].aktif.all()
    # yang sudah aktif sejak T+2 bisa ditindak dan berhenti menyebar
    import copy

    awal = copy.deepcopy(sc_prov)
    awal["provokator"][2]["mulai_jam"] = 2
    sim_g = Simulation(world, awal, iv["G"], seed=1, log_events=False, kode_intervensi="G")
    for _ in range(sim_g.step_of_hour(10)):
        sim_g.step()
    lg = sim_g.prov.lokal[0]
    assert (~lg.aktif).sum() >= 1 and (sim_g.status[lg.agen[~lg.aktif]] == Z).all()
    assert any("akun diturunkan" in e["teks"] for e in sim_g.events)


def test_akun_buzzer_kembali_sesudah_diturunkan(world, sc_prov):
    from sintetis.engine import Simulation

    sim = Simulation(world, sc_prov, load_interventions()["G"], seed=2, log_events=False)
    b = sim.prov.buzzer[0]
    for _ in range(sim.step_of_hour(7)):
        sim.step()
    turun = int((~b.aktif).sum())
    assert turun > 0
    for _ in range(sim.step_of_hour(12)):                       # pulih_jam: 12
        sim.step()
    assert b.aktif.all()
    assert any("akun baru muncul" in e["teks"] for e in sim.events)


def test_serangan_klarifikasi_menahan_warga_di_grup_sasaran(world, sc_prov):
    import copy

    from sintetis.engine import Simulation

    tanpa = copy.deepcopy(sc_prov)
    for x in tanpa["provokator"]:
        x["serang_klarifikasi"] = False
    iv = load_interventions()

    def percaya_di_sasaran(s_, k, serang: bool):
        """Porsi yang tetap percaya (I/A) di akhir, di antara anggota grup sasaran buzzer yang mendengar isu."""
        sim = Simulation(world, s_, iv["C"], seed=k, log_events=False)
        g = np.zeros(world.net.G, np.float32)
        for b in sim.prov.buzzer:
            for gs in b.akun_grup:
                g[gs] = 1
        kena = (world.net.M @ g > 0) & (sim.sig > 0)
        for _ in range(sim.step_of_hour(12)):
            sim.step()
        sr = sim.prov.serangan(sim.t)                     # klarifikasi C muncul T+6, serangan sesudah jeda 1 jam
        if serang:
            assert sr is not None and sr[kena].max() > 0 and sr.max() <= 1
        else:
            assert sr is None
        for _ in range(sim.T - sim.t):
            sim.step()
        heard = kena & sim.pernah_dengar
        return np.isin(sim.status[heard], (I, A)).mean()

    dengan = np.mean([percaya_di_sasaran(sc_prov, k, True) for k in range(6)])
    tanpa_v = np.mean([percaya_di_sasaran(tanpa, k, False) for k in range(6)])
    assert dengan > tanpa_v


def test_validasi_dan_penyusun_skenario_provokator(world, sc_prov):
    import copy

    from sintetis.config import validate_scenario
    from sintetis.llm.asisten import rapikan_skenario

    for rusak in ({"jenis": "troll"}, {"jenis": "buzzer", "kanal": "radio"}, {"jenis": "lokal", "jumlah": 0},
                  {"jenis": "buzzer", "mulai_jam": 10, "selesai_jam": 5}, {"jenis": "lokal", "distrik": "Sentani"}):
        bad = copy.deepcopy(sc_prov)
        bad["provokator"] = [rusak]
        with pytest.raises(ValueError):
            validate_scenario(bad)
    raw = {"nama": "Isu x", "isu": {"teks": "Kabar x"}, "tanam": [{"kanal": "wa", "kabupaten": world.geo.kab_id[0]}],
           "provokator": [{"jenis": "buzzer", "kanal": "wa", "jenis_grup": "suku", "akun": 999,
                           "kabupaten": world.geo.kab_nama[0], "mulai_jam": 2},
                          {"jenis": "lokal", "jumlah": 3, "kabupaten": "tidak ada"}, {"jenis": "hacker"}]}
    out = rapikan_skenario(raw, world.geo)
    b, lk = out["provokator"]
    assert len(out["provokator"]) == 2 and b["akun"] == 100 and b["jenis_grup"] == "keluarga"
    assert b["kabupaten"] == world.geo.kab_id[0] and "kabupaten" not in lk
    run(world, out, "A", log_events=False)


def test_montecarlo_mencatat_paparan_provokator(tmp_path, monkeypatch, sc_prov):
    import pandas as pd

    from sintetis import montecarlo
    from sintetis.report import build_report

    monkeypatch.setattr(montecarlo, "OUT_DIR", tmp_path)
    d = montecarlo.run_montecarlo(5000, 42, putaran=2, kode="AG", scenario=sc_prov, workers=1, progress=None, nama="mc_prov")
    df = pd.read_parquet(d / "hasil.parquet")
    assert (df["paparan_provokator"] >= 0).all() and df.loc[df["tipe"] == "semua", "paparan_provokator"].max() > 0
    md = build_report(d)[0].read_text(encoding="utf-8")
    assert "Peran provokator dan buzzer" in md and "Aktor yang sengaja menyebar" in md


def test_contoh_pesan_provokator_ilustratif(world, sc_prov):
    """Contoh pesan tampil di peristiwa, tetapi tidak mengubah dinamika sama sekali."""
    import copy

    from sintetis.config import validate_scenario
    from sintetis.llm.asisten import buat_contoh_pesan, rapikan_skenario

    tanpa = copy.deepcopy(sc_prov)
    for pv in tanpa["provokator"]:
        pv.pop("contoh_pesan", None), pv.pop("contoh_serangan", None)
    s1, r1 = run(world, sc_prov, "D", seed=2)
    s0, r0 = run(world, tanpa, "D", seed=2)
    assert np.array_equal(r0.timeseries, r1.timeseries)
    pesan = [e for e in s1.events if e["jenis"] == "pesan_provokator"]
    assert not [e for e in s0.events if e["jenis"] == "pesan_provokator"]
    assert {e["aktor"] for e in pesan} == {"Jaringan akun anonim", "Penerus di grup WA keluarga", "Provokator lapangan"}
    assert all(e["ilustratif"] for e in pesan)
    ser = [e for e in pesan if e.get("serangan")]
    assert ser and all(e["t"] >= s1.prov.t_klar + s1.prov.jeda_serang for e in ser)
    b = s1.prov.buzzer[0]
    assert sorted(e["t"] for e in pesan if e["aktor"] == b.nama and not e.get("serangan")) == [t for t, _ in b.pesan]
    assert all(b.t0 <= t < b.t1 for t, _ in b.pesan)
    # tanpa nama: nama bawaan per jenis
    s2 = Simulation(world, tanpa | {"provokator": [{k: v for k, v in pv.items() if k != "nama"} for pv in tanpa["provokator"]]},
                    load_interventions()["A"], seed=1)
    assert [x.nama for x in s2.prov.buzzer] == ["Buzzer 1", "Buzzer 2"] and s2.prov.lokal[0].nama == "Provokator lapangan 1"
    # rapikan & validasi
    raw = {"nama": "x", "isu": {"teks": "x"}, "tanam": [{}], "provokator": [
        {"jenis": "buzzer", "contoh_pesan": "satu pesan", "contoh_serangan": "serang", "serang_klarifikasi": False},
        {"jenis": "lokal", "contoh_pesan": [{"teks": "a  b"}, "", "c" * 500], "contoh_serangan": "jang percaya"}]}
    out = rapikan_skenario(raw, world.geo)
    assert out["provokator"][0]["contoh_pesan"] == ["satu pesan"] and "contoh_serangan" not in out["provokator"][0]
    assert out["provokator"][1]["contoh_pesan"] == ["a b", "c" * 300]
    validate_scenario(out)
    with pytest.raises(ValueError):
        validate_scenario(out | {"provokator": [{"jenis": "lokal", "contoh_pesan": "bukan daftar"}]})
    isi, info = buat_contoh_pesan(tanpa, "mock")
    assert info["diisi"] == 3 and all(pv["contoh_pesan"] for pv in isi["provokator"])
    assert "contoh_pesan" not in tanpa["provokator"][0]            # masukan tidak diubah


REAL = __import__("pathlib").Path(__file__).resolve().parent.parent / "data"


@pytest.mark.skipif(not (REAL / "kampung.csv").exists(), reason="data/kampung.csv belum dibuat (jalankan 'geodata')")
def test_geografi_nyata(monkeypatch, sc):
    """Geografi dari 'geodata': kode resmi, poligon distrik, alias nama distrik lama, dan skenario tetap jalan."""
    import json

    from sintetis import population, server, world as world_mod

    for mod in (population, world_mod, server):
        monkeypatch.setattr(mod, "DATA_DIR", REAL)
    w = build_world(5_000, 42, use_cache=False)
    geo = w.geo
    assert geo.sumber.startswith("data/kampung.csv")
    assert len(geo.distrik_nama) >= 100 and len(geo.kampung_nama) >= 990
    lon, lat = geo.kampung_lonlat[:, 0], geo.kampung_lonlat[:, 1]
    assert ((lon > 134) & (lon < 141.1) & (lat > -4.6) & (lat < 1.2)).all()
    # Nama sintetis lama tetap bisa dipakai skenario lama.
    assert geo.distrik_nama[geo.distrik_index("kab_jayapura", "Sentani")] == "Sentani"
    assert geo.distrik_index("kota_jayapura", "Kota Jayapura 01") >= 0
    assert geo.distrik_nama[geo.distrik_index("kep_yapen", "Serui")] == "Yapen Selatan"
    # Setiap distrik punya poligon resmi.
    batas = server._batas_distrik()
    hilang = [n for d, n in enumerate(geo.distrik_nama) if (geo.kab_id[geo.distrik_kab[d]], n.lower()) not in batas]
    assert not hilang, hilang
    meta = json.loads((REAL / "geodata_sumber.json").read_text(encoding="utf-8"))
    assert meta["pemeriksaan"]["kampung_resmi_tanpa_poligon"] == []
    sim = Simulation(w, sc, load_interventions()["A"], seed=1, log_events=False)
    sim.run()
    assert sim.pernah_dengar.mean() > 0
