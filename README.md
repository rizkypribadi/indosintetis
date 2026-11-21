# Indonesia Sintetis — Fase 1: Provinsi Papua

Implementasi MVP dari `PRD Indonesia Sintetis.html`: simulasi hibrida penyebaran hoaks (ABM SEIZA + agen kunci LLM) di 9 kabupaten/kota Provinsi Papua, dengan Monte Carlo, analisis per tipe wilayah, dan dashboard peta beranimasi.

> Semua agen fiktif. Geografi (999 kampung, 105 distrik, batas resmi) dirakit dari data terbuka dengan `python -m sintetis geodata`. Sinyal masih **proksi** (uji kecepatan Ookla) dan sebaran penduduk antardistrik masih **model** (WorldPop): ganti dengan Podes dan angka BPS per distrik bila tersedia. Parameter perilaku belum terkalibrasi.

## Mulai cepat

Klik dua kali **`jalankan.bat`**. Berkas ini menyiapkan lingkungan Python bila belum ada, menampilkan daftar kesiapan, lalu membuka dashboard di http://127.0.0.1:8000. Daftar kesiapan yang sama ada di tab *Dunia* pada dashboard dan lewat `python -m sintetis status`.

## Menjalankan

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Dashboard (peta, graf, kurva, banding, replay, editor skenario, hasil Monte Carlo)
.\.venv\Scripts\python.exe -m sintetis serve          # buka http://127.0.0.1:8000

# Baris perintah
python -m sintetis dunia --agen 100000                 # ringkasan populasi & jaringan
python -m sintetis jalankan --intervensi D --seed 1    # satu putaran + log Parquet di out/runs/
python -m sintetis bekukan --nama beku_v1 --llm deepseek --pilot 3   # isi cache LLM lalu bekukan
python -m sintetis montecarlo --putaran 200            # 6 skenario × 200 putaran + laporan
python -m sintetis montecarlo --putaran 200 --llm deepseek --beku beku_v1
python -m sintetis sensitivitas --ke-mc mc_sentani_100k_200x   # uji ketahanan peringkat, tempel ke laporan MC
python -m sintetis status                              # apa yang siap, apa yang masih perkiraan
python -m sintetis geodata                             # geografi nyata dari data terbuka → data/kampung.csv + batas wilayah
python -m sintetis data                                # templat data/kampung_contoh.csv + periksa data/kampung.csv
python -m sintetis kalibrasi                           # grid terhadap config/kalibrasi_rujukan.yaml
python -m sintetis kalibrasi --metode abc --sampel 500 # ABC: sebaran posterior semua parameter → out/posterior_abc/
python -m sintetis montecarlo --posterior posterior_abc   # Monte Carlo dengan parameter diundi dari posterior
python -m sintetis sensitivitas --metode sobol --seed-per-konfig 4   # indeks Sobol: parameter mana yang paling menentukan
python -m sintetis montecarlo --skenario scenarios/isu_sentani_provokator.yaml --intervensi ABCDEFGH   # dengan buzzer & provokator
python -m sintetis persona                             # ekspor persona untuk tinjauan penutur asli
python -m sintetis bench --agen 1000000                # uji kinerja
python -m pytest tests
```

Penyedia LLM: `sumopod` (proxy SumoPod ke `deepseek-v4-flash`, kunci dari `SUMOPOD_API_KEY` atau `LLM API Key.txt`), `deepseek` (API DeepSeek langsung, `DEEPSEEK_API_KEY`), `ollama` (lokal). `deepseek-v4-flash` adalah model penalaran, jadi batas token keluarannya dinaikkan ke 2.500 di `config/llm.yaml`.

**Temuan percontohan (bias LLM)** — 20 ribu agen, intervensi D, seed 1:

| Versi | teruskan | ajak jaga | luruskan | tanya_dulu | Distrik eskalasi |
|---|---|---|---|---|---|
| v1: prompt awal | 0 | 0 | 177 | 66 | 29 |
| v2: persona bersifat (kekhawatiran, kebiasaan meneruskan, kepercayaan pada aparat, pengalaman) + prompt "perankan reaksi realistis" + tekanan sosial di konteks | 20 | 4 | 154 | 132 | 30 |
| v3: v2 + `tanya_dulu` ditanya ulang tiap 2 jam; setelah 3× ragu diserahkan ke aturan ABM (sebelumnya dipaksa Z) | 25 | 11 | 173 | 105 | 33 |
| Aturan ABM saja (pembanding) | 43 | 13 | 119 | 19 | 55 |

Agen LLM masih jauh lebih cepat meluruskan dibanding aturan ABM. Belum diketahui mana yang lebih dekat dengan kenyataan, karena kedua parameter sama-sama belum divalidasi. Prompt sengaja **tidak** disetel lebih jauh agar cocok dengan angka ABM, karena itu sama saja menyesuaikan satu tebakan dengan tebakan lain. Penentunya adalah FGD dan tinjauan penutur asli, serta perbandingan dengan model lain (mis. Ollama). Pada uji terisolasi tanpa klarifikasi, 7 dari 12 agen meneruskan, sehingga peluruhan di simulasi penuh terutama datang dari koreksi warga yang cepat menyebar. Mode `mock` menjalankan seluruh jalur LLM (prompt, validasi JSON, cache, biaya) tanpa jaringan; mode `abm` memakai aturan bawaan saja.

## Asisten AI: skenario bebas & laporan

- **Skenario bebas** — di *Editor skenario*, tulis skenario apa saja dengan bahasa sehari-hari (topik, tempat, kanal, waktu, kelompok yang paling cemas), lalu klik **Susun dengan AI**. LLM mengisi formulir (teks isu dalam Melayu Papua, titik tanam, pengali kerentanan, durasi) dan menulis asumsinya di kotak *Asumsi AI*. Periksa, ubah bila perlu, lalu simpan. Keluaran LLM selalu dirapikan dan divalidasi di Python (`rapikan_skenario`): kabupaten/distrik dicocokkan dengan geografi, angka dipotong ke rentang sah. *Mulai dari kosong* membuat skenario tanpa AI.
- **Pengali kerentanan** (`isu.faktor_identitas`) sekarang mendukung `orang_tua_anak_sekolah`, `perempuan`, `laki_laki`, `pemuda` (16–30), `lansia` (55+), `pengguna_hp`.
- **Laporan AI** — tombol *Laporan AI* (atau dari dialog Monte Carlo). Sumber: putaran dashboard terakhir (termasuk mode banding), log tersimpan, atau hasil Monte Carlo. Angka dihitung dulu di Python (`ringkas_putaran`, `ringkas_mc`); LLM hanya menafsirkan dan diminta tidak mengarang angka. Laporan disimpan sebagai `laporan_ai_*.md/.html` di folder hasil (putaran dashboard: `out/laporan_ai/`). Satu laporan Monte Carlo ±1–2 menit, ±0,02 USD lewat SumoPod.
- **PDF** — setiap laporan AI otomatis juga dibuat sebagai PDF A4 (tombol **Buka PDF** di dialog). Isinya: sampul dengan angka kunci dan pengaturan simulasi, narasi AI yang ditata ulang (tabel, poin, kotak catatan), serta grafik yang disisipkan di bagian yang relevan. Grafik Monte Carlo: kurva siaga aktif per tipe wilayah, kanal paparan, efek tiap intervensi dengan IK 95%, peta panas beban siaga, rentang efek dan parameter paling berpengaruh dari analisis sensitivitas. Grafik putaran tunggal: komposisi status warga, penyebar dan siaga per tipe wilayah, kanal, beban per tipe wilayah, distrik tertinggi, dan keputusan agen kunci. Grafik dibuat dari angka simulasi, bukan dari teks AI. Bangun ulang PDF tanpa memanggil AI lagi: `python -m sintetis pdf out/.../laporan_ai_*.md`.
- Kode: `sintetis/llm/asisten.py`, `sintetis/laporan_pdf.py`, endpoint `POST /api/ai/skenario`, `POST /api/ai/laporan`, `GET /unduh/...`.

## Alur otomatis untuk tim lapangan

Tombol **Alur otomatis** di bilah atas menjalankan seluruh alur yang disarankan di server, tanpa perlu diawasi:

1. **Eksplorasi**: beberapa putaran tanpa intervensi, untuk melihat seberapa bervariasi kejadiannya.
2. **Bekukan**: putaran percontohan dengan LLM, lalu keputusan tokoh kunci dibekukan. Tahap ini dilewati bila tokoh memakai aturan ABM.
3. **Monte Carlo**: semua intervensi diuji dengan tabel beku tersebut.
4. **Putaran contoh**: intervensi terbaik dan tanpa intervensi dijalankan pada seed yang selisihnya paling dekat dengan median Monte Carlo. Keduanya disimpan sebagai log yang bisa diputar di peta (tombol *Putar di peta*, lalu *Video* untuk bahan briefing). Seed, dunia, dan tabel bekunya sama dengan Monte Carlo, sehingga angkanya identik dengan baris Monte Carlo pada seed itu.
5. **Laporan lapangan**: PDF berbahasa sederhana untuk Diskominfo, aparat, tokoh, dan relawan. Isinya angka kunci di sampul, **kartu tindakan per tipe wilayah** yang dihitung langsung di Python (bukan oleh AI; IK 95% dan stabilitas diterjemahkan menjadi keyakinan tinggi/sedang/rendah), lalu bagian *Bila isu dibiarkan*, *Langkah paling ampuh*, *Jadwal tindakan*, *Distrik yang perlu diawasi*, *Contoh jalannya kejadian*, dan *Peran tokoh masyarakat*, masing-masing dengan grafiknya.

Hasil tersimpan di `out/alur_<skenario>_<waktu>/`. Folder ini juga muncul di dialog Monte Carlo, dan `alur.json` mencatat semua tahap. Lewat baris perintah: `python -m sintetis alur --putaran 50 --llm sumopod --penyedia sumopod`. Kode: `sintetis/alur.py`, endpoint `POST /api/alur` dan `GET /api/alur-status`.

## Alat kerja tim lapangan (revisi R1–R6)

Rincian dan alasannya ada di `CATATAN-REVISI-FITUR-LAPANGAN.md`. Semua alat ini bisa dibuka dari bilah atas dashboard (**Ruang Situasi**, **Kartu respons**, **Alat lapangan**) atau langsung di http://127.0.0.1:8000/lapangan. Halaman lapangan dan latihan dibuat besar dan sederhana agar nyaman di tablet, dan tidak memakai peta.

Prinsip yang berlaku di semua keluaran: angka selalu disertai rentang (IK 95% atau P10–P90) dan label keyakinan; tanda **SIMULASI — BUKAN PRAKIRAAN** dan versi model tercetak di setiap kartu, PDF, layar, dan log; tidak ada nama orang, nama akun, atau nomor telepon (kontak ditulis sebagai peran); semua angka dihitung di Python, bukan oleh LLM.

**Urutan pertama kali:**

```powershell
python -m sintetis pustaka bangun --prioritas          # sel prioritas (kota/pesisir + ajakan bertindak) + kurva keterlambatan
python -m sintetis pustaka bangun                      # sisa 270 sel (±1 malam di laptop; bisa dihentikan & dilanjutkan)
python -m sintetis pustaka status                      # cakupan, versi, sel yang belum dihitung, tanda USANG
python -m sintetis kartu --semua                       # 9 kartu → out/kartu/<versi>/kartu_<kab>.pdf
```

| Fitur | Perintah | Di dashboard | Kode |
|---|---|---|---|
| R1 Kartu Respons Cetak | `kartu --kabupaten kep_yapen` / `--semua` | Kartu respons · `GET /api/kartu/{kab}.pdf` | `kartu.py`, `config/templat_kartu.yaml`, `config/kontak_peran.yaml` |
| R2a Pustaka skenario | `pustaka bangun [--prioritas] [--agen 20000 --putaran 30]`, `pustaka status` | peringatan "pustaka usang" | `pustaka.py`, `scenarios/templat/*.yaml` |
| R2b Ruang Situasi | `situasi --kabupaten kep_yapen --kanal wa --ajakan --jam-sejak-diketahui 3` | Ruang Situasi (+ Hitung khusus) | `situasi.py` |
| R3 Harga keterlambatan | `keterlambatan --skenario <berkas> --intervensi CDFK --jam 0:24:2 --putaran 50` | tab di dialog Monte Carlo; opsi di Alur otomatis | `keterlambatan.py` |
| R4 Perencana | `rencanakan --sumber-daya kep_yapen --skenario <berkas> [--simpan]` | Alat lapangan → Perencana | `perencana.py`, `config/sumber_daya/*.yaml` |
| R5 Latihan meja | (server) `serve --host 0.0.0.0` agar HP peserta bisa bergabung | Alat lapangan → Latihan meja (`/latihan`) | `latihan.py`, `latihan/skrip_*.yaml` |
| R6 Kajian pasca-kejadian | `kajian buat --dari-situasi <log>`, `kajian analisis <id>`, `kalibrasi --kasus kajian/` | Alat lapangan → Kajian | `kajian.py`, `calibration.py` |

**Fondasi (F1–F6).**

- **F1** Monte Carlo menulis `hasil_kabupaten.parquet` (metrik per kabupaten), `distrik.parquet` (peluang memanas dan jam sampai memanas per distrik, P10/P50/P90), dan `kurva_persentil.parquet`. `hasil.parquet` per tipe wilayah tidak berubah.
- **F2** Setiap langkah intervensi boleh diberi `kabupaten`/`distrik` sasaran, `t_sejak_deteksi`, `pengali_kepercayaan`, dan kapasitas (`jumlah_tokoh`, `stasiun`, `orang`). Tanpa kunci ini hasil sama persis dengan versi sebelumnya.
- **F3** Jenis intervensi baru `tim_keliling` (kode K di `scenarios/intervensi.yaml`): klarifikasi tatap muka dari kampung ke kampung, dengan kapasitas orang × kampung per jam, pada jam kerja. Tidak butuh sinyal.
- **F4** `Simulation.tambah_intervensi(spec)` dan `tambah_tanam(spec)` menyisipkan keputusan di tengah putaran (waktu relatif terhadap sekarang). Semua sisipan dicatat, sehingga jalur bisa diputar ulang dengan seed yang sama.
- **F5** `jeda_deteksi_jam` (skenario, atau bawaan per tipe wilayah di `parameter.yaml`) membedakan jam isu muncul dan jam isu diketahui petugas. Kurva dan rekomendasi dinyatakan relatif terhadap deteksi. Skenario juga bisa mengisi `tanpa_sinyal` untuk mematikan sinyal di distrik tertentu.
- **F6** `versi_model()` menghasilkan hash dari `parameter.yaml`, `papua.yaml`, `kalibrasi_rujukan.yaml`, kalibrasi aktif, dan versi paket. Hash ini tercetak di semua keluaran. Pustaka dari versi lain ditandai **usang**. Hash isi kode dicatat terpisah agar perbaikan satu baris kode tidak membuat pustaka usang.

**Catatan penting.**

- Kartu tercetak sebagai **DRAF** sampai `config/templat_kartu.yaml` ditinjau penutur asli dan praktisi lapangan (`ditinjau.id: true`). Versi Melayu Papua (`--bahasa mp`) baru aktif setelah terjemahannya ditinjau.
- Prebunking tidak memudar di model (`prebunking.paruh_hari: null`), jadi kurva keterlambatan E hanya bermakna untuk cakupan, tidak untuk waktu. Isi paruh waktunya bila ada data.
- Perencana menilai sumber daya pada **seed baru** yang tidak dipakai saat seleksi, agar rencana pemenang tidak tampak terlalu bagus. Rencana dengan stabilitas peringkat rendah diberi peringatan.
- Latihan meja: peserta hanya melihat laporan yang terlambat 1–3 jam, sebagian keliru, dan tidak ada kabar dari distrik tanpa sinyal kecuali lewat tim keliling. Debrief membandingkan tiga jalur (tanpa tindakan, keputusan tim, rencana terbaik) pada seed yang sama.
- Kajian: validasi menolak kolom nama/akun/telepon dan teks yang memuat nomor telepon atau @akun. Setiap perubahan dicatat di `kajian/audit.log`. Folder `kajian/` tidak ikut git. `kalibrasi --kasus kajian/` mengaktifkan posterior baru (`config/kalibrasi_aktif.yaml`): versi model naik, pustaka menjadi usang, dan Monte Carlo tanpa `--posterior` memakai posterior itu. Hapus berkas tersebut untuk kembali ke parameter nominal.
- Pustaka JS dashboard (MapLibre, deck.gl, ECharts, js-yaml) kini disimpan di `web/vendor/`, sehingga dashboard, Ruang Situasi, dan latihan tetap jalan tanpa internet. Peta dasar satelit/topografi tetap butuh internet; pilih *Kanvas polos* saat luring. Peta dasar luring (PMTiles) belum dibuat.

## Hasil yang sudah ada (skenario Sentani, parameter belum terkalibrasi)

| Folder di `out/` | Isi |
|---|---|
| `mc_sentani_100k_200x` | Hasil utama: 100 ribu agen × 200 putaran × 6 intervensi, aturan ABM. Laporan memuat kanal paparan dan analisis sensitivitas |
| `mc_sentani_20k_100x_abm`, `mc_sentani_20k_100x_llm_beku` | Pembanding aturan ABM vs keputusan LLM beku (`beku_sumopod_v1`, 1.244 keputusan DeepSeek) pada dunia yang sama |
| `sensitivitas_sentani_2026_20000` | 13 parameter digeser satu per satu + 40 sampel Latin hypercube (1.206 putaran) |

Temuan sementara:

- **Peringkat E > D > F > C > B ≈ A sama** untuk aturan ABM dan LLM beku; efek terhadap baseline juga hampir sama (E −61% vs −64%, D −44% vs −46%, F −27% vs −27%). Catatan: tabel beku baru menutup **24%** keputusan agen kunci, sisanya jatuh ke aturan ABM, jadi ini belum uji LLM penuh. Menaikkan cakupan butuh putaran percontohan berbayar (`bekukan --pilot N`, ±0,3 USD dan ±11 menit per putaran 20 ribu agen).
- **Sensitivitas**: prebunking (E) tetap terbaik di 85% sampel parameter; tiga besar E > D > F bertahan di 82% sampel. Satu-satunya parameter yang membalik juara adalah `prebunking.percaya`: bila prebunking hanya sedikit menurunkan peluang percaya (0,7), klarifikasi gereja & adat (D) menjadi terbaik. Angka ini layak diprioritaskan saat FGD.
- Beban siaga baseline paling peka terhadap `peluang_korektor` (seberapa banyak warga skeptis yang ikut meluruskan).
- Warga pedalaman dan pesisir pertama kali mendengar hoaks terutama lewat **tatap muka** (±70%), warga kota lewat **WhatsApp** (±54%).

## Struktur

| Berkas | Isi |
|---|---|
| `config/papua.yaml` | 9 kabupaten/kota: penduduk SP2020, tipe wilayah, sinyal, ikatan antarwilayah, stasiun radio |
| `config/parameter.yaml` | β per kanal, τ, literasi, kepercayaan relasi, jadwal ibadah/pasar, komposisi agen kunci |
| `config/llm.yaml` | penyedia (DeepSeek/Ollama/mock), tarif, batas biaya per skenario |
| `scenarios/*.yaml` | skenario isu (editor di dashboard menulis ke sini) dan intervensi A–F |
| `sintetis/population.py` | FR-01 populasi, FR-02 peta sinyal (atau `data/kampung.csv` bila ada) |
| `sintetis/geodata.py` | Perakit geografi nyata: kode & batas Kepmendagri, penduduk Dukcapil + WorldPop, sinyal Ookla, pantai dari DEM |
| `sintetis/network.py` | FR-03 jaringan bipartit agen × kelompok: WA, FB, tatap muka, radio |
| `sintetis/engine.py` | FR-04 mesin SEIZA tervektorisasi dengan siklus harian |
| `sintetis/interventions.py` | FR-06 medsos resmi, gereja & adat, radio, prebunking, penindakan provokator |
| `sintetis/provocateur.py` | Aktor yang sengaja menyebar: buzzer daring dan provokator lapangan |
| `sintetis/llm/` | FR-07 orkestrator, FR-08 cache SQLite & tabel beku, FR-14 pelacak biaya |
| `sintetis/montecarlo.py`, `analysis.py` | FR-09 Monte Carlo paralel, FR-10 analisis per tipe wilayah (IK 95%, efek berpasangan, stabilitas peringkat) |
| `sintetis/calibration.py` | FR-11 kalibrasi pola: grid, atau ABC (posterior atas rentang di `config/sensitivitas.yaml`) |
| `sintetis/sensitivity.py`, `config/sensitivitas.yaml` | Analisis sensitivitas (OAT + Latin hypercube, dan indeks Sobol) serta rentang parameternya |
| `sintetis/readiness.py` | Pemeriksa kesiapan dan validasi `data/kampung.csv` |
| `sintetis/llm/asisten.py` | Penyusun skenario dari teks bebas dan penulis laporan AI |
| `sintetis/eventlog.py` | FR-12 log Parquet + manifes replikasi (seed, versi kode, model LLM, hash prompt) |
| `sintetis/report.py` | FR-14 laporan Markdown/HTML |
| `sintetis/server.py`, `web/` | FastAPI + WebSocket, dashboard V-01–V-11, halaman `/lapangan` dan `/latihan` |
| `sintetis/keterlambatan.py`, `pustaka.py`, `situasi.py`, `kartu.py`, `perencana.py`, `latihan.py`, `kajian.py` | Alat tim lapangan R1–R6 (lihat bagian *Alat kerja tim lapangan*) |

## Status terhadap kriteria penerimaan MVP (PRD §15)

| Kriteria | Status |
|---|---|
| 100 ribu agen, 9 kab/kota, 48 jam, hasil identik untuk seed sama | ✅ ±2 dtk per putaran; diuji di `tests/` |
| Tatap muka & radio tanpa sinyal, kanal daring mengikuti peta sinyal | ✅ diuji (dunia tanpa sinyal → nol paparan WA/FB) |
| Enam skenario dibandingkan per tipe wilayah dengan IK | ✅ `montecarlo` + laporan |
| ≥ 50 agen kunci Melayu Papua di DeepSeek, lolos validasi JSON, ditinjau penutur asli | ⚠️ Percontohan `deepseek-v4-flash` via SumoPod: 243/243 keputusan lolos validasi (8 lewat coba ulang), 0,23 USD per putaran 20 ribu agen. **Belum ditinjau penutur asli** (kolom `ditinjau`, alur `persona` → `config/persona_ditinjau.yaml`) |
| Peta, graf, kurva tersinkron, replay, ekspor video | ✅ (video WebM lewat MediaRecorder) |
| Kinerja 1 juta agen × 96 langkah < 5 menit | ✅ ±23 dtk di laptop ini |

## Penyimpangan dari PRD

- **Frontend tanpa build step**: HTML + JS biasa dengan MapLibre, deck.gl, ECharts (disimpan lokal di `web/vendor/`), bukan React + Vite + Sigma.js. Graf jaringan digambar deck.gl di atas peta. Bisa dipindah ke React nanti tanpa mengubah API.
- **Jaringan sebagai kelompok**, bukan graf antaragen: grup WA/FB, rumah ibadah, pasar, pertemuan adat, dll. Ini yang membuat 1 juta agen tetap cepat; python-igraph dan GeoPandas tidak diperlukan.
- **Poligon distrik** adalah batas resmi dari `data/batas_distrik.geojson` (perintah `geodata`). Tanpa berkas itu, dashboard kembali memakai selubung cembung kampung.
- **Peta dasar** bisa dipilih di panel lapisan: *Satelit + relief* (citra Esri World Imagery + bayangan bukit dari DEM Terrarium + label tempat), *Topografi* (Esri World Topo), atau *Kanvas polos*. Semua butuh internet; tanpa internet peta tetap jalan di atas latar polos.

## Geografi nyata

`python -m sintetis geodata` (±2–5 menit pertama kali, butuh internet, mengunduh ±70 MB ke `data/mentah/`) menulis:

| Berkas | Isi |
|---|---|
| `data/kampung.csv` | 999 kampung/kelurahan resmi: kode, nama, titik permukiman, penduduk, sinyal, tipe wilayah, elevasi, jarak ke laut |
| `data/batas_distrik.geojson`, `data/batas_kabupaten.geojson` | Poligon resmi untuk peta |
| `data/geodata_sumber.json` | Sumber, lisensi, aturan turunan, dan ringkasan pemeriksaan per kabupaten |
| `data/penduduk_distrik_contoh.csv` | Templat untuk mengisi penduduk distrik dari BPS |

| Butir | Sumber | Mutu |
|---|---|---|
| Kode, nama, batas kab/distrik/kampung | Kepmendagri No 300.2.2-2430/2025 (repo `cahyadsn/wilayah`, `wilayah_boundaries`) | Resmi |
| Penduduk kabupaten | Dukcapil, Kepmendagri No 300.2.2-2138/2025 | Resmi (data registrasi) |
| Penduduk per kampung & titik permukiman | WorldPop 2020 constrained 100 m; bila kosong, titik `place` OpenStreetMap | **Model**. ±470 kampung kecil tidak terlihat WorldPop dan diberi bobot kecil |
| Sinyal | Ookla Open Data, ubin seluler 4 kuartal terakhir: `4g` bila ada ubin ≥ 5 Mbps dalam 2 km, `lemah` bila ada uji lebih lambat, `tidak` bila tak ada uji | **Proksi**: tidak ada uji ≠ pasti tanpa sinyal |
| Tipe wilayah | kelurahan → kota; Biak/Supiori/Yapen → kepulauan; ≤ 3 km dari laut → pesisir; sisanya pedalaman. Tipe distrik = tipe kampung berpenduduk terbanyak | Aturan, bisa diubah lewat opsi |

Kalibrasi geografi dengan data resmi bila sudah ada:

1. **Penduduk distrik (BPS *Kabupaten/Distrik Dalam Angka*)**: salin `data/penduduk_distrik_contoh.csv` ke `data/penduduk_distrik.csv`, isi kolom `penduduk` (baris kosong tetap memakai WorldPop), lalu jalankan `geodata --timpa`. Contoh yang perlu dicek: WorldPop memberi Muara Tami ±86 ribu jiwa, yang tampak terlalu tinggi.
2. **Sinyal Podes**: timpa kolom `sinyal` di `data/kampung.csv` (`4g | lemah | tidak`). Kolom `ookla_uji` dan `ookla_mbps_maks` membantu memeriksa kampung yang meragukan.
3. Opsi: `--ambang-4g 5`, `--radius-sinyal 2`, `--pantai-km 3`, `--kuartal 4`.

Skenario lama dengan nama distrik sintetis tetap jalan: "Kota Jayapura 01" dipetakan ke distrik ke-1 menurut penduduk (Abepura), "Keerom 01" ke Arso, dan `alias_distrik` di `config/papua.yaml` memetakan Serui → Yapen Selatan. Skenario baru dan penyusun skenario AI memakai nama resmi.

Dunia dan hasil yang dibangun dengan geografi sintetis tidak sebanding dengan yang baru; jalankan ulang Monte Carlo, sensitivitas, dan kalibrasi.

Lisensi: Ookla CC BY-NC-SA 4.0 (nonkomersial), WorldPop CC BY 4.0, OSM ODbL, kode wilayah MIT. Cantumkan atribusinya bila hasil dipublikasikan.

## Kalibrasi ABC dan Monte Carlo berposterior

`kalibrasi --metode abc` mengambil set parameter dari prior seragam (rentang di `config/sensitivitas.yaml`), mensimulasikan baseline, dan menyimpan porsi `--terima` yang paling dekat dengan target di `config/kalibrasi_rujukan.yaml`. Keluaran di `out/posterior_abc/`: `posterior.json`, `sampel.parquet`, dan `kalibrasi_abc.md`, yang memuat parameter mana yang benar-benar terbatasi rujukan dan peringatan bila target tidak terjangkau model di seluruh prior.

`montecarlo --posterior NAMA` mengundi satu set parameter posterior per seed dinamika, sama untuk semua intervensi pada seed itu. Uji berpasangan tetap sah, dan IK 95% memuat ketidakpastian parameter, bukan hanya keacakan dinamika. Tanpa `--posterior`, perilaku Monte Carlo tidak berubah.

Dengan target contoh yang sekarang, `jam_ke_eskalasi` = 8 jam tidak terjangkau (model paling lambat ±6,7 jam di seluruh prior), jadi posterior yang ada belum layak dipakai untuk kesimpulan; isi dulu linimasa rujukan yang sebenarnya.

## Indeks Sobol

`sensitivitas --metode sobol` memecah varians beban siaga baseline (A), dan efek % tiap intervensi lain terhadap A (default E), menjadi porsi per parameter: S1 (pengaruh sendiri) dan ST (termasuk interaksi). Rancangannya Saltelli dengan N baris (`--n-sobol`, default 64), sehingga biayanya N × (d + 3) konfigurasi × `--seed-per-konfig` × jumlah intervensi.

Keacakan simulasi ikut menggelembungkan setiap ST. Karena itu blok B dijalankan dua kali dengan seed berbeda untuk mengukur lantai derau di seluruh ruang parameter. Laporan menampilkan ST bersih (ST dikurangi lantai) dan menandai parameter berpengaruh hanya bila batas bawah IK ST-nya di atas lantai. Untuk target efek %, derau dengan 1 seed per konfigurasi cukup besar, jadi pakai `--seed-per-konfig 4` atau lebih. `--ke-mc NAMA` menempelkan hasilnya ke laporan Monte Carlo.

## Provokator dan buzzer

Warga biasa di model menyebarkan hoaks karena percaya. Blok opsional `provokator:` di berkas skenario menambah aktor yang **sengaja** menyebar (contoh lengkap: `scenarios/isu_sentani_provokator.yaml`; bisa juga diisi lewat editor dashboard atau penyusun skenario AI).

| Jenis | Siapa | Perilaku |
|---|---|---|
| `buzzer` | Akun anonim (boleh dari luar Papua, bukan anggota populasi) di grup FB info atau grup WA | Mengunggah terus selama `mulai_jam`–`selesai_jam` dengan `intensitas` (1 = satu warga yang baru percaya). Akun anonim kurang dipercaya, tetapi akun `terkoordinasi` di grup yang sama memberi kesan konsensus. `pulih_jam`: akun yang diturunkan diganti akun baru. |
| `lokal` | Warga dewasa di wilayah sasaran (dipilih hanya berdasarkan wilayah dan usia) | Menyebar lewat jaringannya sendiri dan, bila `ajak_jaga_kampung`, terus mengajak warga berjaga. |

Keduanya kebal klarifikasi selama aktif, aktivitasnya tidak meluruh, dan bila `serang_klarifikasi`, sejak klarifikasi pertama muncul mereka menurunkan kepercayaan warga di grupnya pada klarifikasi (`provokator.efek_serang_klarifikasi` di `config/parameter.yaml`). Intervensi G (penurunan akun T+6) dan H (klarifikasi resmi + penurunan akun) memakai jenis `penindakan`. Monte Carlo mencatat `paparan_provokator`, yaitu porsi paparan hoaks yang datang langsung dari aktor ini, dan laporan menampilkan bagian "Peran provokator dan buzzer". Tanpa blok `provokator:`, hasil simulasi sama persis dengan sebelumnya, dan G/H sama dengan A/C.

**Contoh pesan (ilustratif).** Tiap aktor boleh punya `contoh_pesan` (daftar unggahan/ucapan) dan `contoh_serangan` (satu pesan yang menyerang klarifikasi). Contoh pesan ditampilkan merata selama kampanye di tab Peristiwa, selama aktornya masih aktif. Akun yang diturunkan tidak mengunggah lagi. Contoh serangan muncul sekali setelah klarifikasi pertama. Penyusun skenario AI mengisinya otomatis, dan tombol "Buat contoh pesan (AI)" di editor mengisi aktor yang masih kosong. Isi kalimat **tidak** dipakai dalam perhitungan, jadi hasil simulasi sama persis dengan atau tanpa contoh pesan.

Semua angka provokator adalah perkiraan skenario. Untuk uji ketahanan, tambahkan `provokator.kredibilitas_akun` dan `provokator.efek_serang_klarifikasi` ke `config/sensitivitas.yaml` (sudah ada contoh yang dikomentari).

## Skenario dengan topik lain dan agen kunci LLM

Prompt agen kunci memakai `isu.alasan_percaya` dari skenario (mis. "karena takut keluarga jatuh sakit") sebagai contoh motif warga. Bila kosong, prompt identik dengan versi awal ("karena takut anak-anak celaka"), sehingga cache dan tabel beku lama tetap berlaku. Penyusun skenario AI mengisi kolom ini otomatis.

## Yang masih butuh data/orang

Jalankan `python -m sintetis status` untuk melihat mana yang sudah terpenuhi.

1. Sinyal per kampung dari Podes (kolom `sinyal` di `data/kampung.csv`) dan penduduk per distrik dari BPS (`data/penduduk_distrik.csv`), menggantikan proksi Ookla dan pembagian WorldPop. Lihat bagian *Geografi nyata*.
2. Linimasa kasus Wamena 2023 untuk `config/kalibrasi_rujukan.yaml` (nilai sekarang hanya contoh).
3. Tinjauan persona & glosarium oleh penutur asli, dan FGD untuk bobot kepercayaan.
4. Menaikkan cakupan tabel beku LLM (sekarang 24%) dengan `bekukan --llm sumopod --pilot N`, dan pembanding Ollama (belum terpasang di laptop ini).
5. Setelah butir 1–3 masuk: jalankan ulang `kalibrasi --metode abc`, `montecarlo --posterior`, dan `sensitivitas`, lalu persempit rentang di `config/sensitivitas.yaml`.
