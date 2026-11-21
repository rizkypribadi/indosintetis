"""Uji revisi fitur lapangan (CATATAN-REVISI-FITUR-LAPANGAN.md): fondasi F1–F6 dan fitur R1–R6, skala kecil."""
import copy
import json
import re
import time

import numpy as np
import pandas as pd
import pytest

from sintetis.config import load_interventions, load_scenario, validate_scenario
from sintetis.engine import Simulation
from sintetis.world import build_world

N = 5000


@pytest.fixture(scope="module")
def world():
    return build_world(N, 42)


@pytest.fixture(scope="module")
def sc():
    return load_scenario()


@pytest.fixture(scope="module")
def pus(tmp_path_factory):
    """Pustaka kecil: satu sel prioritas berkurva (kota) dan satu sel biasa (kepulauan)."""
    from sintetis import pustaka

    root = tmp_path_factory.mktemp("pustaka") / "p"
    for kab in ("kota_jayapura", "kep_yapen"):
        pustaka.bangun(agen=N, putaran=4, kode="ACDEK", jenis="penculikan_anak", kabupaten=kab, kanal="wa", ajakan=True,
                       jam_kurva="0:8:4", putaran_kurva=4, workers=1, progress=None, root=root)
    return root


# ---------------------------------------------------------------------------------------------- fondasi
def test_f4_penyisipan_saat_berjalan_bisa_diputar_ulang(world, sc):
    spec = {"jenis": "gereja_adat", "porsi_tokoh": 0.8, "t_jam": 2}
    tanam = {"kanal": "fb", "kabupaten": "kota_jayapura", "akun_medsos": 2, "t_jam": 1}

    def jalan(sisip):
        s = Simulation(world, sc, load_interventions()["A"], seed=3, log_events=False)
        for _ in range(sim_t := 10):
            s.step()
        if sisip:
            iv = s.tambah_intervensi(spec)
            s.tambah_tanam(tanam)
            assert iv.t0 == sim_t + s.step_of_hour(2)          # t_jam relatif terhadap sekarang
        while s.t < s.T:
            s.step()
        return s

    s1, s2, s0 = jalan(True), jalan(True), jalan(False)
    assert np.array_equal(s1.ts, s2.ts) and not np.array_equal(s1.ts, s0.ts)
    assert [x["aksi"] for x in s1.sisipan] == ["intervensi", "tanam"] and s1.result().manifest["sisipan"]


def test_f5_jeda_deteksi_dan_jadwal_relatif_deteksi(world, sc):
    sc2 = {**copy.deepcopy(sc), "jeda_deteksi_jam": 3}
    a = Simulation(world, sc2, {"langkah": [{"jenis": "medsos_resmi", "t_sejak_deteksi": 2, "durasi_jam": 24}]}, seed=1, log_events=False)
    b = Simulation(world, sc2, {"langkah": [{"jenis": "medsos_resmi", "t_jam": 5, "durasi_jam": 24}]}, seed=1, log_events=False)
    assert a.t_deteksi_jam == 3 and a.interventions[0].t0 == b.interventions[0].t0
    ra, rb = a.run(), b.run()
    assert np.array_equal(ra.timeseries, rb.timeseries)
    assert any(e["jenis"] == "deteksi" for e in ra.events) and ra.manifest["jeda_deteksi_jam"] == 3
    # bawaan per tipe wilayah: Sentani (kota) memakai jeda kota di parameter.yaml
    assert Simulation(world, sc, None, seed=1, log_events=False).jeda_deteksi_jam == 1.0
    with pytest.raises(ValueError):
        validate_scenario({**copy.deepcopy(sc), "jeda_deteksi_jam": 100})


def test_f2_intervensi_bersasaran_dan_berkapasitas(world, sc):
    sim = Simulation(world, sc, {"langkah": [
        {"jenis": "medsos_resmi", "t_jam": 6, "durasi_jam": 24, "kabupaten": "keerom"},
        {"jenis": "gereja_adat", "t_jam": 6, "kabupaten": "kep_yapen", "jumlah_tokoh": 3},
        {"jenis": "gereja_adat", "t_jam": 6, "kabupaten": "kep_yapen", "jumlah_tokoh": 10_000},
        {"jenis": "radio", "t_jam": 6, "durasi_jam": 12, "stasiun": ["rri_serui"]}]}, seed=1, log_events=False)
    m, g3, gmax, r = sim.interventions
    geo, pop = world.geo, world.pop
    keerom = geo.distrik_kab[pop.distrik] == geo.kab_index("keerom")
    assert m.reach[~keerom].max() == 0 and m.reach[keerom].max() > 0
    yapen = geo.kab_index("kep_yapen")
    assert 0 < g3.porsi < gmax.porsi == 1.0 and len(g3.targets) <= len(gmax.targets)
    assert all(geo.distrik_kab[pop.distrik[world.key.agen[k]]] == yapen for k in gmax.targets)
    assert (geo.distrik_kab[world.net.distrik[g3.covered > 0]] == yapen).all()
    jangkau = {"kep_yapen", "waropen"}                        # RRI Serui (config/papua.yaml)
    kab_dengar = {geo.kab_id[k] for k in np.unique(pop.kab[r.listener > 0])}
    assert kab_dengar and kab_dengar <= jangkau
    # tanpa sinyal di distrik tertentu: kanal daring mati di sana
    sc3 = {**copy.deepcopy(sc), "tanpa_sinyal": [{"kabupaten": "kota_jayapura"}]}
    s3 = Simulation(world, sc3, None, seed=1, log_events=False)
    assert s3.sig[geo.distrik_kab[pop.distrik] == geo.kab_index("kota_jayapura")].max() == 0


def test_f3_tim_keliling_hanya_di_sasaran_dan_sesuai_kapasitas(world, sc):
    spec = {"langkah": [{"jenis": "tim_keliling", "t_jam": 2, "durasi_jam": 24, "orang": 2, "kampung_per_jam": 0.5,
                         "kabupaten": "kab_jayapura"}]}
    sim = Simulation(world, sc, spec, seed=1, log_events=False)
    sim.run()
    tk = sim.interventions[0]
    geo = world.geo
    v = np.flatnonzero(tk.sudah)
    assert tk.total == len(v) > 0
    assert (geo.distrik_kab[geo.kampung_distrik[v]] == geo.kab_index("kab_jayapura")).all()
    jam_kerja = sum(1 for t in range(tk.t0, tk.t1) if tk.jam_kerja[0] <= sim.clock(t)[1] < tk.jam_kerja[1]) * sim.dt_h
    assert tk.total <= tk.laju * jam_kerja + 1


def test_f1_metrik_per_kabupaten_distrik_dan_seed_eksplisit(tmp_path, sc):
    from sintetis.analysis import paired_effect, summarize
    from sintetis.montecarlo import run_montecarlo

    d = run_montecarlo(N, 42, 3, ["A", "D"], sc, workers=1, progress=None, out_dir=tmp_path / "mc")
    hk = pd.read_parquet(d / "hasil_kabupaten.parquet")
    dist = pd.read_parquet(d / "distrik.parquet")
    kp = pd.read_parquet(d / "kurva_persentil.parquet")
    assert set(hk["tipe"]) == set(build_world(N, 42).geo.kab_id) and "jam_puncak_penyebar" in hk
    assert {"peluang_memanas", "jam_median", "nama", "kabupaten"} <= set(dist) and dist["peluang_memanas"].between(0, 1).all()
    assert set(kp["status"]) == {"I", "A"} and (kp["p10"] <= kp["p90"] + 1e-12).all()
    s = summarize(hk)
    assert s["tipe"].notna().all() and len(paired_effect(hk)) == hk["tipe"].nunique()
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["versi_model"]["id"] and man["daftar_seed"] == [0, 1, 2]
    d2 = run_montecarlo(N, 42, 0, ["A", "D"], sc, workers=1, progress=None, out_dir=tmp_path / "mc2", seeds=[2])
    a = pd.read_parquet(d / "hasil.parquet")
    b = pd.read_parquet(d2 / "hasil.parquet")
    pd.testing.assert_frame_equal(a[a["seed"] == 2].reset_index(drop=True), b.reset_index(drop=True))


def test_f6_versi_model_berubah_saat_kalibrasi_aktif(tmp_path, monkeypatch):
    import shutil

    from sintetis import calibration, config

    v1 = config.versi_model()
    assert v1 == config.versi_model() and v1["id"].startswith("m") and v1["status_validasi"]
    for f in ("parameter.yaml", "papua.yaml", "kalibrasi_rujukan.yaml"):
        shutil.copy(config.CONFIG_DIR / f, tmp_path / f)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    assert config.versi_model()["id"] == v1["id"]
    calibration.aktifkan(tmp_path / "posterior_uji", [{"kasus": "k1"}], config_dir=tmp_path)
    v2 = config.versi_model()
    assert v2["id"] != v1["id"] and "dikalibrasi" in v2["status_validasi"][0] and config.kalibrasi_aktif() == "posterior_uji"


# ---------------------------------------------------------------------------------------------- R3
def test_r3_kurva_harga_keterlambatan(tmp_path, sc):
    from sintetis.keterlambatan import hitung_keterlambatan

    d = hitung_keterlambatan(sc, "DE", "0:8:4", putaran=4, n=N, workers=1, out_dir=tmp_path / "k", progress=None,
                             prebunking_hari=[-7, 0], prebunking_cakupan=[0.3, 0.6])
    h = json.loads((d / "ringkasan.json").read_text(encoding="utf-8"))
    r = h["ringkasan"]["semua"]["D"]
    assert r["kalimat"] and r["keyakinan"] in ("tinggi", "sedang", "rendah") and "jam_maks" in r
    assert "sejak isu diketahui" in r["kalimat"] or "belum terbukti" in r["kalimat"]
    assert h["ringkasan"]["semua"]["E"]["titik"] and "paruh_hari" in h["ringkasan"]["semua"]["E"]["kalimat"]
    k = pd.read_parquet(d / "kurva.parquet")
    assert sorted(k[(k["wilayah"] == "semua")]["jam"].unique()) == [0, 4, 8]
    assert (k["nilai_lo"] <= k["nilai"] + 1e-9).all() and (d / "keterlambatan_D.png").exists()
    assert h["relatif"] == "jam sejak isu diketahui petugas"


# ---------------------------------------------------------------------------------------------- R2a / R2b
def test_r2a_pustaka_dapat_dilanjutkan_dan_status(pus, monkeypatch):
    from sintetis import pustaka

    st = pustaka.status(pus)
    assert st["ada"] and st["selesai"] == 2 and st["total_desain"] == 270 and not st["usang"] and st["sel_berkurva"] == 1
    sel = pus / "sel" / "penculikan_anak__kota_jayapura__wa__ajak" / "selesai.json"
    t0 = sel.stat().st_mtime
    pustaka.bangun(agen=N, putaran=4, kode="ACDEK", jenis="penculikan_anak", kabupaten="kota_jayapura", kanal="wa",
                   ajakan=True, workers=1, progress=None, root=pus)
    assert sel.stat().st_mtime == t0                                      # tidak dihitung ulang
    P = pustaka.muat(pus)
    ind = P["indeks"]
    assert {"efek_persen", "efek_lo", "keyakinan", "siaga_jam_per_1000_p10", "jam_puncak_penyebar_p90"} <= set(ind)
    assert set(ind["tipe"]) >= {"semua", "kota", "kota_jayapura", "kep_yapen"} and len(P["keterlambatan"])
    monkeypatch.setattr(pustaka, "versi_model", lambda: {"id": "versi-lain"})
    assert pustaka.status(pus)["usang"]
    sc = pustaka.skenario_sel("antarkelompok__waropen__fb__tanpa", distrik="Distrik Fiktif")
    assert "distrik" not in sc["tanam"][0] and sc["isu"]["ajakan_tindakan"] is False


def test_r2b_ruang_situasi_dari_pustaka(pus, tmp_path, monkeypatch):
    from sintetis import situasi

    monkeypatch.setattr(situasi, "OUT_DIR", tmp_path)
    t0 = time.perf_counter()
    h = situasi.nilai({"jenis": "penculikan_anak", "kabupaten": "kota_jayapura", "kanal": "wa", "ajakan": "ya",
                       "jam_sejak_diketahui": 2, "deskripsi": "hubungi 0812-3456-7890 @akunpalsu"}, root=pus)
    assert time.perf_counter() - t0 < 3
    assert h["tanda"] == "SIMULASI — BUKAN PRAKIRAAN" and h["versi_model"]["id"]
    assert h["kecocokan"]["label"] == "tinggi" and h["posisi"]["fase"] in ("naik", "sekitar puncak", "menurun")
    st = {t["kode"]: t["status"] for t in h["tindakan"]}
    assert st["E"] == "sebelum_isu" and set(st.values()) <= {"bisa", "lewat", "sebelum_isu"}
    assert all(t["status"] == "bisa" for t in [h["tindakan_sekarang"]] if t)
    log = json.loads((tmp_path / "situasi" / f"{h['id']}.json").read_text(encoding="utf-8"))
    assert "0812" not in json.dumps(log) and "@akunpalsu" not in json.dumps(log)
    h2 = situasi.nilai({"jenis": "racun_bantuan", "kabupaten": "kota_jayapura", "kanal": "fb", "jam_sejak_diketahui": 40},
                       simpan=False, root=pus)
    assert h2["kecocokan"]["label"] == "rendah" and "jenis isu" in h2["kecocokan"]["tidak_cocok"]
    lewat = [t for t in h2["tindakan"] if t["status"] == "lewat"]
    assert all("lewat" in t["alasan"] or "jadwal" in t["alasan"] for t in lewat)
    with pytest.raises(ValueError):
        situasi.nilai({"kabupaten": "bukan_kab"}, simpan=False, root=pus)


# ---------------------------------------------------------------------------------------------- R1
def test_r1_kartu_respons_dua_halaman_tanpa_data_pribadi(pus, tmp_path):
    from sintetis import kartu, pustaka

    P = pustaka.muat(pus)
    d = kartu.data_kartu("kota_jayapura", P)
    assert d["tanda"] == "SIMULASI — BUKAN PRAKIRAAN" and d["versi_model"]["id"] and d["jumlah_skenario"] == 1
    for w in d["jendela"]:
        for l in w["langkah"]:
            assert not l["angka"] or "belum dihitung" in l["angka"] or ("keyakinan" in l["angka"] and "rentang" in l["angka"])
    for x in d["distrik"]:
        assert x["lo"] <= x["peluang"] <= x["hi"]
    teks = json.dumps(d["kontak"], ensure_ascii=False)
    assert not re.search(r"\d{6,}", teks) and "{radio}" not in teks
    for kab in ("kota_jayapura", "mamberamo_raya"):
        b = kartu.buat_kartu(kab, tmp_path / f"{kab}.pdf", P).read_bytes()
        assert b.startswith(b"%PDF") and b.count(b"/Type /Page") - b.count(b"/Type /Pages") == 2
    with pytest.raises(ValueError):
        kartu.data_kartu("kota_jayapura", P, bahasa="mp")              # Melayu Papua belum ditinjau


# ---------------------------------------------------------------------------------------------- R4
PROFIL = {"kabupaten": "kep_yapen", "batas_waktu_jam": 6, "tokoh": [{"jumlah": 6, "jam_paling_cepat": 1}],
          "tim_keliling": {"orang": 4, "kampung_per_jam": 0.5, "jam_paling_cepat": 2},
          "radio": [{"stasiun": "rri_serui", "jam_paling_cepat": 8}],          # melewati batas waktu → tidak dipakai
          "kepercayaan": {"gereja": "tinggi", "tim_lapangan": "sedang"}}


def test_r4_perencana_layak_dan_bisa_diulang(tmp_path, monkeypatch):
    from sintetis import perencana, pustaka

    monkeypatch.setattr(perencana, "OUT_DIR", tmp_path)
    ops = perencana.opsi(PROFIL)
    assert {o["id"] for o in ops} == {"tokoh1", "tim"}
    cand = perencana.rencana_layak(ops)
    assert len(cand) == 3 and all(not perencana.periksa_rencana(r, PROFIL) for r in cand.values())
    assert perencana.periksa_rencana({"langkah": [{"jenis": "gereja_adat", "jumlah_tokoh": 9, "t_sejak_deteksi": 1}]}, PROFIL)
    sc = pustaka.skenario_sel("penculikan_anak__kep_yapen__wa__ajak")
    kw = dict(agen=N, putaran_awal=3, sisa=2, sampel_ketahanan=1, seed_ketahanan=1, workers=1, progress=None)
    h1 = perencana.rencanakan(sc, PROFIL, nama="r1", **kw)
    h2 = perencana.rencanakan(sc, PROFIL, nama="r2", **kw)
    assert [r["kode"] for r in h1["rencana"]] == [r["kode"] for r in h2["rencana"]]
    assert [r["efek_persen"] for r in h1["rencana"]] == [r["efek_persen"] for r in h2["rencana"]]
    r = h1["rencana"][0]
    assert r["keyakinan"] in ("tinggi", "sedang", "rendah") and len(r["efek_rentang"]) == 2 and r["ketahanan"]
    assert h1["kalimat_sumber_daya"] and not h1["pelanggaran"]
    p = perencana.simpan_rencana(h1)
    assert p.exists() and perencana.rencana_tersimpan("kep_yapen")[0]["langkah"]


# ---------------------------------------------------------------------------------------------- R5
def test_r5_latihan_meja_kabut_keputusan_dan_debrief(tmp_path, monkeypatch):
    from sintetis import latihan, pustaka

    monkeypatch.setattr(latihan, "OUT_DIR", tmp_path)
    sc = pustaka.skenario_sel("penculikan_anak__kep_yapen__wa__ajak")
    sc["durasi_jam"] = 24
    prof = {**PROFIL, "batas_waktu_jam": 99}
    skrip = {"nama": "uji", "suntikan": [{"jam": 2, "teks": "Video lama diunggah ulang",
                                          "efek": {"tanam": {"kanal": "fb", "kabupaten": "kep_yapen", "akun_medsos": 2}}},
                                         {"jam": 3, "teks": "RRI mati", "efek": {"radio_mati": {"stasiun": "rri_serui", "jam": 30}}}]}
    s = latihan.SesiLatihan(sc, prof, skrip, agen=N, ronde=3)
    s.ronde_berikut()
    assert s.ronde_ke == 1 and len(s.suntikan) == 2 and s.suntikan[0]["otomatis"]
    peserta, fas = s.status("peserta"), s.status("fasilitator")
    assert "fasilitator" not in peserta and "distrik" in fas["fasilitator"]
    assert all({"distrik", "teks", "jam_diterima"} <= set(l) for l in peserta["laporan"])
    assert any(not p["tersedia"] for p in peserta["pilihan"] if p["id"].startswith("radio"))
    with pytest.raises(ValueError):
        s.putuskan("tokoh1", jumlah=99)                                 # melebihi kapasitas
    s.putuskan("tokoh1", jumlah=4, catatan="hubungi 0812 3456 7890")
    assert s.sisa["tokoh"] == 2 and "[dihapus]" in s.keputusan[0]["catatan"]
    s.putuskan("tim", jumlah=4)
    with pytest.raises(ValueError):
        s.putuskan("tim", jumlah=1)                                      # sumber daya habis
    s.nilai(1, "sebagian", "di kampung kami tidak begitu")
    while not s.selesai:
        s.ronde_berikut()
    d = s.debrief()
    assert d["replay_identik"] and set(d["jalur"]) == {"tanpa", "tim", "terbaik"}
    assert d["jalur"]["tim"]["kurva_siaga"] != d["jalur"]["tanpa"]["kurva_siaga"]    # keputusan mengubah simulasi
    assert d["penilaian_ringkas"]["sebagian"] == 1 and d.get("pdf")
    for f in ("keputusan.json", "suntikan.json", "penilaian.json", "debrief.pdf"):
        assert (tmp_path / "latihan" / s.id / f).exists()


# ---------------------------------------------------------------------------------------------- R6
def test_r6_kajian_validasi_analisis_dan_kalibrasi_banyak_kasus(tmp_path, monkeypatch):
    from sintetis import calibration, kajian

    monkeypatch.setattr(kajian, "KAJIAN_DIR", tmp_path / "kajian")
    monkeypatch.setattr(calibration, "OUT_DIR", tmp_path)
    k = copy.deepcopy(kajian.CONTOH)
    buruk = copy.deepcopy(k)
    buruk["linimasa"][1]["keterangan"] = "lapor ke 0812-3456-7890"
    buruk["linimasa"][2]["nama"] = "Pak X"
    err = kajian.validasi(buruk)
    assert any("nomor telepon" in e for e in err) and any("'nama'" in e for e in err)
    with pytest.raises(ValueError):
        kajian.simpan(buruk)
    kajian.simpan(k, pengguna="analis")
    nm = {**copy.deepcopy(k), "id": "kj-nearmiss", "near_miss": True, "hasil": {"eskalasi": False},
          "linimasa": [b for b in k["linimasa"] if b["jenis"] != "tanda_bahaya"]}
    kajian.simpan(nm)
    h = kajian.analisis(k["id"], agen=N, putaran=3, workers=1, progress=None)
    metrik = {t["metrik"] for t in h["seberapa_meleset"]}
    assert {"jam sampai memanas", "apakah memanas", "durasi memanas (jam)"} <= metrik
    assert h["bagaimana_jika"] and "4 jam lebih awal" in h["bagaimana_jika"][0]["kalimat"] and h.get("pdf")
    assert kajian.lencana_validasi("kep_yapen")["kasus"] == 1 and "belum ada" in kajian.lencana_validasi("sarmi")["teks"]
    audit = (tmp_path / "kajian" / "audit.log").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["aksi"] for x in audit] == ["buat", "buat", "analisis"]
    cases = kajian.kasus_kalibrasi(tmp_path / "kajian")
    assert len(cases) == 2 and any(c["near_miss"] for c in cases) and all(0 < c["bobot_kasus"] <= 1 for c in cases)
    nmc = next(c for c in cases if c["near_miss"])
    assert nmc["target"]["jam_ke_eskalasi"] == nmc["skenario"]["durasi_jam"] * 1.5
    d = calibration.calibrate_abc(N, 42, sampel=3, putaran=1, terima=0.34, rujukan=cases,
                                  rentang={"peluang_siaga": [0.05, 0.2]}, workers=1, nama="post_kasus", progress=None)
    post = json.loads((d / "posterior.json").read_text(encoding="utf-8"))
    assert post["jumlah_kasus"] == 2 and set(post["target"]) == {f"{c['kasus']}:jam_ke_eskalasi" for c in cases}
    assert set(post["bobot_kasus"]) == {"kj-2027-001", "kj-nearmiss"}


# ---------------------------------------------------------------------------------------------- server
def test_server_endpoint_lapangan(pus, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sintetis import kartu, pustaka, server, situasi

    monkeypatch.setattr(pustaka, "akar", lambda: pus.parent)
    real_status = pustaka.status
    monkeypatch.setattr(pustaka, "status", lambda root=None: real_status(root or pus))
    monkeypatch.setattr(situasi, "OUT_DIR", tmp_path)
    monkeypatch.setattr(kartu, "OUT_DIR", tmp_path)
    c = TestClient(server.app)
    m = c.get("/api/lapangan/meta").json()
    assert len(m["kabupaten"]) == 9 and m["pustaka"]["ada"] and "kep_yapen" in m["profil"]
    r = c.post("/api/situasi", json={"jenis": "penculikan_anak", "kabupaten": "kep_yapen", "kanal": "wa", "ajakan": "ya",
                                     "jam_sejak_diketahui": 1})
    assert r.status_code == 200 and r.json()["kecocokan"]["label"] == "tinggi"
    assert c.post("/api/situasi", json={"kabupaten": "x"}).status_code == 422
    p = c.get("/api/kartu/kep_yapen.pdf")
    assert p.status_code == 200 and p.content.startswith(b"%PDF")
    assert c.get("/api/kartu/bukan.pdf").status_code == 404
    assert c.get("/lapangan").status_code == 200 and c.get("/latihan").status_code == 200
    assert c.get("/static/vendor/echarts-5.5.1.min.js").status_code == 200
    assert c.get("/api/pustaka").json()["selesai"] == 2


def test_server_websocket_latihan(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sintetis import latihan, server

    monkeypatch.setattr(latihan, "OUT_DIR", tmp_path)
    c = TestClient(server.app)
    with c.websocket_connect("/ws/latihan") as f:
        f.send_json({"buat": {"skenario": "penculikan_anak__kep_yapen__wa__ajak", "profil": "kep_yapen",
                              "skrip": "skrip_contoh.yaml", "agen": N, "ronde": 2, "durasi_jam": 24}})
        sid = f.receive_json()["sesi"]
        assert "fasilitator" in f.receive_json()["status"]
        f.send_json({"aksi": "ronde"})
        assert f.receive_json()["status"]["ronde"] == 1
        with c.websocket_connect("/ws/latihan") as p:
            p.send_json({"gabung": sid, "peran": "peserta"})
            assert p.receive_json()["peran"] == "peserta"
            assert "fasilitator" not in p.receive_json()["status"]          # peserta tidak melihat keadaan sebenarnya
            f.receive_json()
            p.send_json({"aksi": "ronde"})
            assert "fasilitator" in p.receive_json()["error"]
            p.receive_json(), f.receive_json()
            p.send_json({"aksi": "putuskan", "pilihan": "tim", "jumlah": 2})
            assert len(p.receive_json()["status"]["keputusan"]) == 1
            f.receive_json()
        f.send_json({"aksi": "debrief"})
        d = f.receive_json()["debrief"]
        assert d["replay_identik"] and set(d["jalur"]) == {"tanpa", "tim", "terbaik"}
