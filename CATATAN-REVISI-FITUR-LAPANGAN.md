# Catatan Revisi — Indonesia Sintetis untuk Tim Lapangan

**Status:** draf untuk dibahas tim · **implementasi kode F1–F6 dan R1–R6 selesai (21 November 2026)**, lihat bagian berikut
**Tanggal:** 21 November 2026
**Cakupan:** enam fitur pilihan yang mengubah Indonesia Sintetis dari alat riset "bagaimana jika" menjadi alat kerja pencegahan konflik di lapangan.

| Kode | Fitur |
|---|---|
| R1 | Kartu Respons Cetak per kabupaten |
| R2 | Pustaka skenario yang sudah dihitung dan Ruang Situasi |
| R3 | Kurva "harga keterlambatan" |
| R4 | Perencana intervensi dengan keterbatasan |
| R5 | Mode Latihan Meja (tabletop) |
| R6 | Kajian pasca-kejadian |

## Status implementasi (21 November 2026)

Semua fondasi dan keenam fitur sudah ada di kode, beserta CLI, endpoint, antarmuka, dan tes otomatis
(`tests/test_lapangan.py`, 15 tes; seluruh 41 tes lulus). Cara pakai ada di README, bagian *Alat kerja tim lapangan*.
Yang belum selesai bukan pekerjaan kode, melainkan pekerjaan orang dan komputasi:

| Butir | Status kode | Yang masih perlu |
|---|---|---|
| F1–F6 fondasi | selesai | nilai `jeda_deteksi_jam` per tipe wilayah dan pengali `kepercayaan_pembawa` dari FGD |
| R1 Kartu | selesai (`kartu.py`, 2 halaman A4, hitam-putih) | tinjauan templat oleh penutur asli & praktisi; uji cetak fisik; pengesahan mitra |
| R2a Pustaka | selesai, bisa dilanjutkan | menjalankan `pustaka bangun --prioritas` lalu `pustaka bangun` (±1 malam) |
| R2b Ruang Situasi | selesai (±0,5 dtk per jawaban) | pustaka di atas; peninjauan templat isu antarkelompok oleh peneliti Papua |
| R3 Keterlambatan | selesai | menjalankan kurva C, D, F, E, K untuk Sentani dan Serui dengan putaran penuh; isi `prebunking.paruh_hari` bila ada data |
| R4 Perencana | selesai | profil sumber daya nyata per kabupaten di `config/sumber_daya/` |
| R5 Latihan meja | selesai (`/latihan`, WebSocket) | uji coba sesi nyata, panduan fasilitator dua halaman, persetujuan peserta |
| R6 Kajian | selesai (validasi data pribadi, audit, kalibrasi banyak kasus) | kasus nyata dan near miss; keputusan siapa yang boleh mengisi dan mengakses |

Keputusan teknis yang perlu diketahui tim:

- **Versi model (F6)** dihitung dari berkas konfigurasi, kalibrasi aktif, dan versi paket, bukan dari isi setiap berkas kode. Dengan begitu perbaikan kecil di kode tidak membuat pustaka dan kartu usang. Hash kode tetap dicatat terpisah di setiap keluaran.
- **Tim keliling (F3)** memakai kode **K** di `scenarios/intervensi.yaml`. Di pustaka, sasarannya adalah kabupaten asal isu.
- **Prebunking** tidak memudar di model (`prebunking.paruh_hari: null`), jadi kurva E hanya bermakna untuk cakupan. Waktu prebunking baru berpengaruh setelah paruh waktunya diisi. Nilai bawaan sengaja tidak diubah agar hasil lama tetap sebanding.
- **Perencana** menilai "sumber daya paling berharga" pada seed baru yang tidak dipakai saat seleksi, untuk menghindari *winner's curse*.
- **Kalibrasi aktif (R6)**: `kalibrasi --kasus kajian/` menulis `config/kalibrasi_aktif.yaml`. Monte Carlo tanpa `--posterior` lalu memakai posterior itu, versi model naik, dan pustaka menjadi usang. Hapus berkas itu untuk kembali ke parameter nominal.
- **Luring**: pustaka JS disimpan di `web/vendor/`. Peta dasar luring (PMTiles/MBTiles) belum dibuat; saat tanpa internet pakai peta *Kanvas polos*. Halaman lapangan dan latihan tidak memakai peta.

---

## 0. Latar belakang

### Masalah yang dijawab

1. **Respons hampir selalu terlambat.** Di laporan uji Serui (C vs D), penyebar memuncak pada jam ke-13,5, sebelum intervensi D (T+12) sempat berdampak. Sebagian besar warga sudah terpapar sebelum jam ke-12. Sementara itu, Monte Carlo dan laporan AI butuh hitungan menit sampai jam. Jadi aplikasi harus menyiapkan jawaban **sebelum** krisis, bukan menghitung **saat** krisis.
2. **Prebunking adalah temuan terkuat.** E menang di 85% sampel parameter. Ini menegaskan nilai persiapan.
3. **Keluaran sekarang berbentuk riset.** Isinya peringkat A–H per tipe wilayah, sedangkan tim lapangan butuh jawaban praktis: *apa yang dilakukan, oleh siapa, kapan, di kabupaten mana, dengan sumber daya yang ada.*
4. **Belum ada jembatan ke kenyataan.** Skenario selalu dimulai dari teks peneliti, dan kejadian nyata tidak pernah kembali menjadi data kalibrasi.

### Prinsip yang berlaku di semua fitur

- **Angka = rentang + keyakinan.** Tidak ada angka dua desimal yang berdiri sendiri. Pakai rentang (mis. persentil 10–90 atau IK 95%) dan label keyakinan tinggi/sedang/rendah, memakai logika `keyakinan()` yang sudah ada di `sintetis/alur.py`.
- **Tanda "SIMULASI — BUKAN PRAKIRAAN"** wajib ada di setiap PDF, kartu, layar Ruang Situasi, dan video.
- **Versi model tercetak di setiap keluaran.** Hash parameter + versi kode + versi pustaka + status validasi (mis. "parameter belum dikalibrasi, persona belum ditinjau"). Dengan begitu kartu lama bisa dikenali dan ditarik.
- **Tanpa data orang sungguhan.** Kontak ditulis sebagai *peran* (pendeta klasis, kepala suku, kepala kampung, Bhabinkamtibmas, penyiar RRI), bukan nama. Lokasi paling rinci tingkat distrik. Tidak ada nama akun, nama orang, atau asal etnis sebagai faktor kecurigaan (PRD §4 dan §14).
- **Bahasa sederhana.** Istilah riset (SEIZA, seed, tabel beku, Monte Carlo) tidak muncul di tampilan untuk tim lapangan.
- **Bisa tanpa internet dan bisa dicetak.** Pustaka JS dan font disimpan lokal, peta dasar luring (PMTiles/MBTiles), dan semua keluaran lapangan tersedia sebagai PDF A4 hitam-putih.
- **Angka dihitung di Python, bukan oleh LLM.** LLM boleh membantu merapikan kalimat, tetapi semua angka dan rekomendasi berasal dari hasil simulasi, seperti pola `kartu_tindakan()` sekarang.

### Prasyarat teknis bersama (fondasi)

Fitur-fitur di bawah bergantung pada beberapa perubahan dasar. Kerjakan ini lebih dulu.

| ID | Perubahan | Berkas | Dipakai oleh |
|---|---|---|---|
| F1 | **Metrik per kabupaten dan per distrik**, tidak hanya per tipe wilayah. `run_metrics()` sekarang hanya mengelompokkan per `TIPE_WILAYAH` + "semua". Tambahkan kelompok per kabupaten, plus keluaran per distrik: peluang memanas dan median jam sampai memanas. | `montecarlo.py`, `analysis.py` | R1, R2, R4, R6 |
| F2 | **Intervensi parametrik dan bersasaran.** Setiap langkah intervensi bisa diberi `t_jam` bebas, `kabupaten`/`distrik` sasaran, dan kapasitas (mis. jumlah tokoh). Daftar `langkah` sudah mendukung gabungan; yang ditambah adalah filter sasaran dan kapasitas di `GerejaAdat`, `Radio`, `MedsosResmi`. | `interventions.py`, `scenarios/intervensi.yaml` | R3, R4, R5 |
| F3 | **Jenis intervensi baru `tim_keliling`.** Klarifikasi tatap muka oleh tim lapangan dengan kapasitas terbatas (orang × kampung per jam). Ini satu-satunya jalur ke pedalaman tanpa sinyal selain radio. | `interventions.py` | R1, R4, R5 |
| F4 | **Mesin yang bisa disisipi saat berjalan.** `Simulation.tambah_intervensi(spec)` dan `Simulation.tambah_tanam(spec)` relatif terhadap `self.t`, agar keputusan bisa masuk di tengah putaran. `step()` sudah per langkah, jadi yang ditambah adalah API penyisipan. | `engine.py` | R5, R6 |
| F5 | **Jeda deteksi.** Bedakan *jam sejak isu muncul* dan *jam sejak isu diketahui petugas*. Tambahkan parameter `jeda_deteksi_jam` di skenario. Semua kurva dan rekomendasi dinyatakan relatif terhadap waktu deteksi, karena hanya itu yang diketahui tim di lapangan. | `config.py`, skenario, `engine.py` | R2, R3, R5, R6 |
| F6 | **Versi model.** Fungsi `versi_model()` menghasilkan hash `parameter.yaml` + `papua.yaml` + versi kode + nama posterior, lalu dicetak di setiap keluaran. `eventlog.py` sudah mencatat manifes; perluas agar dipakai bersama. | `eventlog.py`, `config.py` | semua |

---

## Urutan pengerjaan yang disarankan

```
F1–F6 (fondasi)
   │
   ├── R2a Pustaka skenario ──┬── R3 Kurva harga keterlambatan
   │                          ├── R1 Kartu Respons Cetak   (butuh R2a + R3)
   │                          └── R2b Ruang Situasi        (butuh R2a + R3)
   │
   ├── R4 Perencana intervensi            (butuh F2, F3)
   ├── R5 Mode Latihan Meja               (butuh F4, R2a; lebih baik setelah R4)
   └── R6 Kajian pasca-kejadian           (butuh F4, F5; memakai log R2b)
```

| Fase | Isi | Perkiraan usaha* |
|---|---|---|
| 1 | Fondasi F1–F6 | 6–8 hari |
| 2 | R2a Pustaka + R3 Kurva | 6–8 hari (+ 1 malam komputasi) |
| 3 | R1 Kartu + R2b Ruang Situasi | 7–9 hari |
| 4 | R4 Perencana | 6–8 hari |
| 5 | R5 Latihan Meja | 10–14 hari |
| 6 | R6 Kajian pasca-kejadian | 6–8 hari |

\*Perkiraan kasar untuk satu pengembang yang sudah mengenal kode ini. Belum termasuk FGD, tinjauan penutur asli, dan uji coba lapangan.

---

## R1. Kartu Respons Cetak per kabupaten

### Tujuan
Menyediakan pegangan satu halaman per kabupaten yang dibawa tim ke lapangan dan bisa dipakai tanpa listrik, sinyal, atau laptop.

### Pengguna
Diskominfo kabupaten, Polres/Bhabinkamtibmas, klasis gereja, lembaga adat, RRI dan radio komunitas, relawan.

### Isi kartu (A4, satu halaman, bisa bolak-balik)

**Sisi depan: "Bila isu muncul"**
1. **Kepala kartu.** Nama kabupaten, komposisi tipe wilayah (mis. "6 distrik pesisir, 4 pedalaman"), tanggal cetak, versi model, label keyakinan keseluruhan.
2. **Jendela waktu** (dari R3), dihitung sejak isu diketahui:
   - **0–2 jam:** langkah pertama, mis. hubungi pendeta klasis dan kepala kampung di distrik asal isu.
   - **2–6 jam:** mis. siaran RRI dan tim keliling ke distrik tanpa sinyal.
   - **6–24 jam:** mis. klarifikasi resmi di medsos, pemantauan titik kumpul.
   - Satu kalimat harga keterlambatan, mis. "Setiap jam menunda menambah beban siaga ±X% (rentang a–b)."
3. **Kanal paling ampuh per tipe wilayah** di kabupaten ini, mis. tatap muka di pedalaman dan WhatsApp di kota. Sumbernya kolom `paparan_*`.
4. **Distrik yang perlu diawasi.** Lima distrik dengan peluang memanas tertinggi di pustaka, beserta rentangnya.
5. **Tanda bahaya.** Indikator yang menunjukkan isu mulai berubah menjadi tindakan: isu pindah dari WA ke pasar atau tempat ibadah, muncul ajakan "jaga kampung", orang berkumpul di titik tertentu, akun baru menyebar ulang setelah klarifikasi. Daftar ini disusun dari mekanisme model (siaga aktif, provokator, pemulihan akun) lalu ditinjau FGD.

**Sisi belakang: "Siapa menghubungi siapa"**
6. **Pohon kontak per peran** dengan **baris kosong untuk ditulis tangan** (nama dan nomor). Data pribadi tidak pernah tersimpan di sistem. Daftar peran berasal dari `config/kontak_peran.yaml`.
7. **Yang jangan dilakukan.** Contoh: jangan menyebar ulang hoaks dengan maksud membantah; jangan memakai pembawa pesan yang kurang dipercaya di wilayah itu; jangan menyebut kelompok tertentu sebagai pelaku.
8. **Pengingat prebunking.** Kapan dan lewat siapa prebunking paling efektif di kabupaten ini (hasil E per kabupaten).
9. **Catatan kaki.** Tanda "SIMULASI — BUKAN PRAKIRAAN", versi model, status validasi, dan alamat kontak tim peneliti.

### Spesifikasi
- Teks langkah memakai **templat tetap** yang ditinjau tim dan penutur asli, bukan tulisan bebas LLM. Angka dan urutan prioritas diisi dari hasil pustaka (R2a) dan kurva (R3).
- Versi Melayu Papua bersifat opsional, aktif bila templat terjemahannya sudah ditinjau.
- Ramah cetak hitam-putih: tanpa warna sebagai satu-satunya pembeda, huruf minimal 10 pt, kontras tinggi.

### Perubahan teknis
- Modul baru `sintetis/kartu.py` memakai ulang gaya, font, dan tabel dari `laporan_pdf.py` (`_styles`, `_table`, `_callout`).
- Konfigurasi baru:
  - `config/kontak_peran.yaml` berisi daftar peran per kabupaten, tanpa nama.
  - `config/templat_kartu.yaml` berisi kalimat langkah, tanda bahaya, dan larangan.
- CLI: `python -m sintetis kartu --kabupaten kep_yapen` atau `--semua` menghasilkan `out/kartu/<versi>/kartu_<kab>.pdf`.
- Endpoint: `GET /api/kartu/{kabupaten}.pdf`.
- UI: tombol **Kartu respons** di bilah atas, berisi daftar 9 kabupaten dan tombol unduh.

### Kriteria penerimaan
- [x] Sembilan PDF terbentuk, masing-masing pas di A4 (depan dan belakang). *`kartu --semua`; tes memeriksa tepat 2 halaman.*
- [x] Setiap angka disertai rentang atau label keyakinan.
- [x] Tidak ada nama orang atau nomor telepon yang dihasilkan sistem.
- [x] Versi model dan tanggal tercetak; kartu dari versi berbeda bisa dibedakan. *Disimpan per versi di `out/kartu/<versi>/`.*
- [ ] Tetap terbaca saat dicetak hitam-putih (diuji fisik). *Desain hitam-putih, huruf ≥ 10 pt; uji cetak fisik belum.*
- [ ] Templat kalimat sudah ditinjau minimal satu penutur asli dan satu praktisi lapangan. *Kartu tercetak "DRAF" sampai `ditinjau.id: true`.*

### Risiko dan catatan
- Kartu bisa dianggap SOP resmi padahal model belum tervalidasi. Mitigasinya: label status validasi yang jelas dan proses pengesahan bersama mitra (Diskominfo, sinode, lembaga adat) sebelum dibagikan.
- Kartu cepat usang bila parameter diperbarui. Versi model di kartu dan R6 memicu pencetakan ulang.

---

## R2. Pustaka skenario yang sudah dihitung dan Ruang Situasi

### R2a. Pustaka skenario

#### Tujuan
Semua kombinasi skenario penting dihitung **jauh sebelum krisis**, sehingga saat isu nyata muncul jawabannya tersedia dalam hitungan detik.

#### Dimensi pustaka (rancangan awal, dibahas tim)

| Dimensi | Nilai |
|---|---|
| Jenis isu (templat) | penculikan anak · penyerangan/pembunuhan warga · racun atau kontaminasi bantuan/makanan · penembakan · isu antarkelompok yang memicu balas dendam |
| Kabupaten asal isu | 9 kabupaten di `config/papua.yaml` |
| Kanal awal | WhatsApp · Facebook · tatap muka |
| Ajakan bertindak | ada · tidak ada |
| Waktu mulai | pagi/siang · malam (opsional, tahap 2) |

Total desain penuh: 5 × 9 × 3 × 2 = **270 skenario**. Setiap skenario diuji dengan semua intervensi A–H, ditambah varian waktu dari R3 untuk sebagian skenario.

**Perkiraan komputasi:** 20 ribu agen, 30 putaran, dan 8 intervensi berarti 270 × 8 × 30 ≈ 65 ribu putaran. Dengan paralelisme Monte Carlo yang ada, ini kira-kira satu malam di laptop. Skenario prioritas (mis. kota dan pesisir dengan ajakan bertindak) dijalankan ulang dengan 100 ribu agen dan 100 putaran.

#### Spesifikasi
- Templat jenis isu di `scenarios/templat/<jenis>.yaml` berisi intensitas emosi, `faktor_identitas`, dan ajakan bertindak. Teks isu bersifat generik, tanpa nama orang atau kelompok etnis.
- Disimpan per versi model:
  - `out/pustaka/<versi>/indeks.parquet`: satu baris per skenario × intervensi × wilayah (kabupaten, tipe, semua), memuat metrik dari `run_metrics()` plus persentil 10/50/90.
  - `out/pustaka/<versi>/distrik.parquet`: peluang memanas dan median jam per distrik.
  - `out/pustaka/<versi>/kurva.parquet`: kurva status E/I/A per jam (persentil) per skenario × intervensi.
  - `manifest.json`: versi model, jumlah putaran, tanggal, cakupan.
- Saat parameter berubah, pustaka ditandai **usang** dan dashboard menampilkan peringatan.

#### Perubahan teknis
- Modul baru `sintetis/pustaka.py` yang membangun desain skenario dan memanggil `run_montecarlo()` per sel.
- CLI:
  - `python -m sintetis pustaka bangun [--prioritas] [--agen 20000 --putaran 30]`
  - `python -m sintetis pustaka status` menampilkan cakupan, versi, dan sel yang belum dihitung.
- Bisa dilanjutkan bila terputus: sel yang sudah selesai tidak dihitung ulang.

### R2b. Ruang Situasi

#### Tujuan
Satu layar sederhana untuk saat ada laporan isu nyata: masukkan apa yang diketahui, lalu dapatkan perkiraan dan tindakan dalam kurang dari 3 detik.

#### Alur pengguna
1. Petugas membuka **Ruang Situasi** dari bilah atas. Tampilannya besar dan sederhana, nyaman di tablet.
2. Mengisi formulir singkat:
   - Jenis isu (pilih dari templat, atau "lainnya" dengan deskripsi singkat)
   - Kabupaten dan distrik tempat isu pertama terdengar
   - Kanal tempat isu pertama terdengar
   - Jam perkiraan isu mulai beredar dan jam diketahui petugas
   - Apakah sudah ada ajakan bertindak?
   - Apakah sudah ada tanda bahaya (kerumunan, blokade)?
3. Sistem mencocokkan masukan ke sel pustaka terdekat dan menampilkan:
   - **Kecocokan:** tinggi, sedang, atau rendah, beserta dimensi mana yang tidak cocok.
   - **Posisi di kurva:** "Isu kemungkinan sedang di fase naik. Pada skenario serupa, puncak biasanya T+a sampai T+b jam."
   - **Distrik yang kemungkinan terdampak berikutnya:** lima teratas beserta rentangnya.
   - **Tindakan sekarang:** intervensi terbaik yang **masih bisa dilakukan** dari titik waktu ini. Intervensi yang jendelanya sudah lewat disaring, memakai R3.
   - **Harga keterlambatan:** satu kalimat dari R3.
   - Tautan ke Kartu Respons kabupaten (R1) dan ke Perencana (R4).
4. Tombol opsional **Hitung khusus**: menjalankan simulasi baru yang persis sesuai masukan di latar belakang, untuk menyempurnakan perkiraan. Hasil awal dari pustaka tetap tampil selama perhitungan berjalan.
5. Setiap sesi tersimpan sebagai log lokal `out/situasi/<waktu>.json` dan nantinya menjadi draf Kajian pasca-kejadian (R6).

#### Spesifikasi pencocokan
- Dimensi kategoris (jenis isu, kanal, ajakan) harus cocok persis. Bila tidak ada sel yang cocok, cari sel terdekat dan turunkan label kecocokan.
- Kabupaten cocok persis. Distrik dipakai untuk menyaring tampilan distrik terdampak.
- Waktu berjalan = jam sekarang dikurangi jam isu beredar (bila tidak diketahui, pakai jam diketahui ditambah `jeda_deteksi_jam` default) untuk menentukan posisi di kurva.

#### Perubahan teknis
- Endpoint:
  - `POST /api/situasi` menerima masukan dan mengembalikan hasil (tanpa LLM, murni baca pustaka).
  - `POST /api/situasi/{id}/hitung` dan `GET /api/situasi/{id}/status` untuk perhitungan khusus.
- UI: halaman atau mode baru di `web/` dengan tata letak dua kolom (formulir | hasil) dan huruf besar.
- Berjalan sepenuhnya luring setelah pustaka terbangun.

#### Kriteria penerimaan
- [x] Jawaban dari pustaka muncul dalam kurang dari 3 detik tanpa internet. *±0,5 dtk.*
- [x] Setiap keluaran menampilkan label kecocokan, rentang, dan versi model.
- [x] Intervensi yang jendela waktunya sudah lewat tidak direkomendasikan.
- [x] Log sesi tersimpan tanpa nama orang atau akun. *Nomor telepon, @akun, dan tautan di deskripsi dihapus.*
- [x] `pustaka status` melaporkan cakupan dan sel yang usang.

#### Risiko dan catatan
- **Kesan "ramalan".** Layar wajib menampilkan "SIMULASI — BUKAN PRAKIRAAN" dan status validasi. Kata yang dipakai adalah "pada skenario serupa", bukan "akan terjadi".
- **Templat isu antarkelompok** harus ditinjau peneliti Papua agar tidak memasukkan asal etnis sebagai faktor kecurigaan. Model hanya memodelkan *kerentanan kelompok yang disasar isu*, bukan kecurigaan terhadap kelompok.
- Formulir tidak menyediakan kolom nama orang, nama akun, atau tautan unggahan.

---

## R3. Kurva "harga keterlambatan"

### Tujuan
Menunjukkan dengan jelas berapa kerugian dari setiap jam menunda tindakan, dan kapan sebuah intervensi praktis sudah tidak berguna. Kalimat seperti "menunda 4 jam menambah beban siaga ±30%" lebih menggerakkan pengambil keputusan daripada peringkat A–H.

### Spesifikasi
- Untuk setiap intervensi berwaktu (medsos resmi B/C, gereja dan adat D, radio F, penindakan G, `tim_keliling`), geser `t_jam` dari 0 sampai 24 jam setelah **deteksi** dengan langkah 1–2 jam (F5).
- Untuk prebunking (E), geser dari −14 hari sampai 0, sekaligus cakupan 0,2–0,8.
- Setiap titik dijalankan sebagai Monte Carlo dengan **seed berpasangan** terhadap baseline A, sehingga selisihnya bersih dari keacakan.
- Metrik:
  - `siaga_jam_per_1000` (utama)
  - peluang ada distrik memanas
  - jam sampai memanas
- Keluaran turunan:
  - **Harga per jam:** kemiringan kurva di jendela 0–6 jam, dalam persen beban siaga per jam keterlambatan.
  - **Batas waktu efektif:** jam saat manfaat intervensi turun di bawah 25% dari manfaat bila dilakukan di jam ke-0.
  - **Kalimat otomatis** (dari Python), mis. "Klarifikasi lewat gereja dan adat paling berguna bila dilakukan sebelum jam ke-6 sejak isu diketahui. Setiap jam menunda menambah beban siaga ±X% (rentang a–b, keyakinan sedang)."
- Dihitung per tipe wilayah dan per kabupaten (F1).

### Perubahan teknis
- Modul baru `sintetis/keterlambatan.py` yang membangkitkan varian spesifikasi intervensi (mis. `D@t3`, `D@t6`) lalu memakai `run_montecarlo()`.
- CLI: `python -m sintetis keterlambatan --skenario <berkas> --intervensi C,D,F --jam 0:24:2 --putaran 50`
- Keluaran `out/keterlambatan_<skenario>_<waktu>/`: `kurva.parquet`, PNG per intervensi, `ringkasan.json` (harga per jam, batas waktu).
- Untuk skenario prioritas, pustaka (R2a) menyimpan hasil ini juga agar Ruang Situasi dan Kartu bisa membacanya.
- Dashboard: tab baru **Harga keterlambatan** di dialog Monte Carlo, berupa grafik garis dengan pita IK 95% dan penanda batas waktu.
- PDF laporan lapangan (`laporan_pdf.py`): grafik baru `fig_keterlambatan()` dan satu bagian "Kapan harus bertindak".

### Kriteria penerimaan
- [ ] Kurva untuk minimal C, D, F, dan E tersedia untuk skenario Sentani dan Serui. *Alat siap (`keterlambatan --intervensi CDEFK`); belum dijalankan dengan putaran penuh.*
- [x] Setiap titik menampilkan pita IK 95%, dan selisih dihitung berpasangan.
- [x] Harga per jam dan batas waktu efektif dihitung otomatis dan muncul sebagai kalimat sederhana.
- [x] Hasil masuk ke Kartu (R1) dan Ruang Situasi (R2b). *Juga ke PDF laporan lapangan, bagian "Kapan harus bertindak".*
- [x] Uji kewajaran: bila kurva naik-turun tidak wajar karena derau, tampilkan peringatan "putaran kurang".

### Risiko dan catatan
- Hasil sangat bergantung pada asumsi jeda deteksi dan kecepatan penyebaran yang belum terkalibrasi. Uji sensitivitas (`sensitivity.py`) perlu dijalankan terhadap kurva ini, tidak hanya terhadap peringkat.
- Bentuk kurva lebih bisa dipercaya daripada angka absolutnya. Pesan utamanya sebaiknya "lebih cepat jauh lebih baik, terutama sebelum jam ke-X", bukan angka persen yang tepat.

---

## R4. Perencana intervensi dengan keterbatasan

### Tujuan
Menjawab pertanyaan yang benar-benar diajukan tim lapangan: *"Dengan orang, waktu, dan sarana yang kami punya sekarang, apa gabungan langkah terbaik?"*

### Masukan: "Apa yang kita punya"
| Sumber daya | Contoh isian |
|---|---|
| Tokoh gereja/adat yang bisa dihubungi | jumlah per kabupaten/distrik, jam paling cepat bisa dihubungi |
| Radio | stasiun (dari `papua.yaml`: RRI Jayapura, RRI Biak, RRI Serui, radio komunitas Mamberamo), jam paling cepat siaran, durasi |
| Akun resmi medsos | ada/tidak, jangkauan perkiraan |
| Tim keliling (F3) | jumlah orang, kampung per jam, distrik yang bisa dijangkau |
| Kondisi sinyal | distrik yang saat ini tanpa sinyal (mengganti peta sinyal) |
| Kepercayaan warga terhadap pembawa pesan | rendah/sedang/tinggi per wilayah untuk aparat, gereja, adat, pemerintah; nilai awal dari FGD |
| Batas waktu | jam paling lambat semua langkah harus dimulai |

Isian bisa disimpan sebagai profil `config/sumber_daya/<kabupaten>.yaml` agar tidak perlu diisi ulang.

### Cara kerja
1. **Bangkitkan rencana yang layak.** Semua gabungan langkah yang mungkin dari sumber daya yang ada, dengan waktu paling cepat masing-masing. Rencana yang melanggar kapasitas tidak pernah dibuat.
2. **Penyaringan cepat.** Setiap kandidat dijalankan dengan 20 ribu agen dan sekitar 10 putaran berseed berpasangan.
3. **Penyempurnaan bertahap (successive halving).** Separuh terbaik dijalankan lagi dengan lebih banyak putaran, sampai tersisa 3 rencana.
4. **Uji ketahanan.** Tiga rencana teratas diuji pada sampel parameter posterior atau Latin hypercube (`sensitivity.py`) untuk melihat apakah peringkatnya bertahan.

### Keluaran
- **Tiga rencana teratas**, masing-masing berisi urutan langkah dan jam, efek terhadap tanpa intervensi (rentang + keyakinan), dan stabilitas peringkat.
- **Sumber daya paling berharga**, mis. "Menambah 2 tokoh adat di Waropen lebih berguna daripada slot radio kedua." Dihitung dari perbandingan rencana yang berbeda satu sumber daya.
- Tombol **Simpan rencana** agar rencana bisa dipakai di Kartu (R1) dan Latihan Meja (R5).

### Perubahan teknis
- Butuh F2 (sasaran dan kapasitas) dan F3 (`tim_keliling`).
- Kepercayaan per pembawa pesan per wilayah: tambahkan pengali di `parameter.yaml` yang dibaca `GerejaAdat`, `MedsosResmi`, `Radio`.
- Modul baru `sintetis/perencana.py`.
- Endpoint `POST /api/perencana` dan `GET /api/perencana-status` (pola sama seperti `/api/mc` dan `/api/mc-status`).
- CLI: `python -m sintetis rencanakan --skenario <berkas> --sumber-daya config/sumber_daya/kep_yapen.yaml`
- UI: dialog **Perencana** dengan formulir sumber daya di kiri dan kartu tiga rencana di kanan.

### Kriteria penerimaan
- [x] Tidak ada rencana yang melebihi kapasitas atau batas waktu masukan.
- [x] Hasil bisa diulang (seed sama memberi hasil sama).
- [x] Rencana bawaan selesai dalam 15 menit atau kurang di laptop uji. *±2 menit untuk profil contoh Kepulauan Yapen (15 rencana, 20 ribu agen, 6 seed awal, 2 sampel ketahanan); bawaan 10 seed + 8 sampel diperkirakan ±5–8 menit.*
- [x] Setiap rencana menampilkan rentang efek, keyakinan, dan hasil uji ketahanan.
- [x] Kalimat "sumber daya paling berharga" dihitung di Python.

### Risiko dan catatan
- **Optimasi bisa mengeksploitasi kelemahan model.** Rencana yang menang hanya karena celah parameter akan terlihat dari uji ketahanan. Tampilkan peringatan bila stabilitas rendah.
- Intervensi `penindakan` (G/H) tetap dibatasi pada pelaporan dan penurunan akun lewat jalur hukum dan platform. Perencana tidak menyarankan tindakan terhadap orang atau kelompok tertentu.
- Nilai kepercayaan per pembawa pesan sensitif. Nilainya berasal dari FGD, didokumentasikan sumbernya, dan tidak berdasar asal etnis.

---

## R5. Mode Latihan Meja (tabletop)

### Tujuan
1. **Melatih koordinasi** antarlembaga (Diskominfo, Polres, gereja, adat, RRI, relawan) dalam situasi hoaks yang realistis, tanpa risiko nyata.
2. **Mengumpulkan validasi tatap muka.** Setiap komentar peserta seperti "di kampung kami tidak begitu" menjadi data kalibrasi. Ini cara paling murah untuk mengisi kekosongan validasi yang sekarang menahan semua klaim model.

### Peran
- **Fasilitator.** Menjalankan aplikasi di laptop dan proyektor, memilih skenario, menyuntikkan kejadian, dan mengatur ronde.
- **Tim peserta.** Satu meja per lembaga, atau satu tim gabungan. Mengambil keputusan setiap ronde.
- **Pengamat (opsional).** Mencatat jalannya koordinasi.

### Alur sesi (±2–3 jam)
1. **Persiapan.** Fasilitator memilih skenario dari pustaka (R2a) dan skrip suntikan, lalu menetapkan sumber daya tim dari profil R4.
2. **Ronde.** Simulasi 72 jam dipadatkan menjadi 6–8 ronde. Setiap ronde mewakili 2–12 jam simulasi.
   - **Yang dilihat peserta adalah kabut informasi, bukan peta lengkap.** Peserta hanya menerima laporan yang wajar sampai ke mereka: laporan dari distrik yang siaga aktifnya melewati ambang, dengan jeda 1–3 jam, sebagian keliru atau tidak lengkap, dan tanpa info dari distrik tanpa sinyal kecuali lewat tim keliling.
   - **Suntikan fasilitator**, mis. "Jam 15: video lama diunggah ulang dengan keterangan baru di FB", "Pendeta klasis X tidak bisa dihubungi", "RRI Serui gangguan listrik".
   - **Keputusan tim.** Memilih langkah dari kartu tindakan dengan batas sumber daya (R4). Keputusan langsung masuk ke simulasi yang sedang berjalan (F4).
   - **Penilaian singkat per ronde.** "Apakah reaksi warga di ronde ini masuk akal?" (ya/sebagian/tidak) disertai catatan.
3. **Debrief.**
   - Linimasa keputusan tim di atas kurva status.
   - Perbandingan tiga jalur pada seed yang sama: **tanpa tindakan**, **keputusan tim**, dan **rencana terbaik** dari R4 atau pustaka. Mesin deterministik, jadi pembandingnya adil.
   - Di jam berapa keputusan paling menentukan (memakai R3).
   - Ringkasan penilaian kewajaran dari peserta.
   - Ekspor PDF debrief.

### Perubahan teknis
- **Mesin.** F4 (`tambah_intervensi`, `tambah_tanam`) dan jeda deteksi F5. Untuk jalur pembanding, cukup jalankan ulang dengan seed sama dan daftar keputusan yang tercatat, tanpa perlu menyalin keadaan.
- **Model kabut informasi.** Fungsi `laporan_untuk_peserta(sim, t, sumber_daya)` di modul baru `sintetis/latihan.py` yang menghasilkan laporan bertunda dan sebagian.
- **Skrip suntikan.** `latihan/skrip_<nama>.yaml` berisi daftar kejadian berwaktu dan teks yang ditampilkan.
- **Server.** WebSocket baru `/ws/latihan` dengan status sesi (ronde, keputusan, suntikan). Log ke `out/latihan/<sesi>/`: `keputusan.json`, `suntikan.json`, `penilaian.json`, `debrief.pdf`.
- **UI.**
  - **Layar fasilitator:** kendali ronde, tombol suntik, peta lengkap (tersembunyi dari peserta), pengatur waktu.
  - **Layar peserta:** dibuka di tab atau perangkat lain dalam jaringan lokal yang sama (mis. HP peserta lewat Wi-Fi ruang rapat). Berisi laporan masuk, sumber daya tersisa, pilihan tindakan, dan tombol kirim keputusan.
- **Data validasi.** `penilaian.json` dikaitkan dengan versi model dan diteruskan ke alur tinjauan persona dan kalibrasi.

### Kriteria penerimaan
- [ ] Skenario 72 jam selesai dalam 6–8 ronde dan kurang dari 3 jam sesi. *Jumlah ronde bisa diatur (2–12); lama sesi perlu diuji dalam sesi nyata.*
- [x] Keputusan tim mengubah keadaan simulasi di ronde berikutnya.
- [x] Peserta tidak pernah melihat keadaan sebenarnya selama ronde berlangsung.
- [x] Debrief menampilkan tiga jalur pembanding dengan seed sama.
- [ ] Bisa berjalan di jaringan lokal tanpa internet. *Siap secara teknis (pustaka JS lokal, `serve --host 0.0.0.0`); belum diuji di jaringan ruang rapat.*
- [x] Penilaian kewajaran tersimpan dan bisa diekspor. *`out/latihan/<sesi>/penilaian.json` dan PDF debrief.*

### Risiko dan catatan
- Skenario bersifat fiktif dan dinyatakan begitu di awal sesi. Hindari kejadian nyata yang masih sensitif bagi peserta.
- Cakupan suntikan tetap pada **hoaks pemicu kekerasan terhadap warga sipil**. Latihan tidak dipakai untuk merancang operasi terhadap kelompok tertentu atau menekan ekspresi politik.
- Minta persetujuan peserta sebelum penilaian dan catatan mereka dipakai sebagai data penelitian. Catatan disimpan tanpa nama.
- Fasilitator perlu dilatih. Siapkan panduan fasilitator dua halaman bersama fitur ini.

---

## R6. Kajian pasca-kejadian

### Tujuan
Setiap kejadian nyata, termasuk isu yang **tidak** berkembang (*near miss*), menjadi pelajaran dan data kalibrasi, sehingga model dan kartu makin tepat dari waktu ke waktu.

### Isi satu kajian
| Bagian | Isi |
|---|---|
| Identitas | ID kajian, kabupaten/distrik, jenis isu (templat), tanggal |
| Linimasa | isu pertama muncul · pertama diketahui petugas · setiap tindakan (peran, kanal, jam) · tanda bahaya pertama (kerumunan, blokade, kekerasan) · kapan mereda |
| Sumber | daftar sumber yang dipakai (laporan lapangan, berita, cek fakta, wawancara), dengan tingkat keyakinan tiap butir linimasa |
| Hasil | apakah terjadi eskalasi, di distrik mana, berapa lama |
| Pelajaran | apa yang berhasil · apa yang menghambat (mis. tokoh tidak bisa dihubungi, sinyal mati) · usulan perubahan kartu atau profil sumber daya |

Tanpa nama orang atau akun. Pelaku tindakan ditulis sebagai peran, lokasi paling rinci tingkat distrik.

### Analisis otomatis
1. **Putar ulang di model.** Skenario dibentuk dari linimasa (tanam di distrik asal, kanal awal, tindakan nyata pada jam nyata), lalu dijalankan sebagai Monte Carlo.
2. **Bandingkan model dengan kenyataan** pada metrik pola: jam sampai memanas, distrik yang memanas, dan durasi. Hasilnya berupa tabel "seberapa meleset" beserta penjelasan sederhana.
3. **Bagaimana jika** (memakai R3): "Bila klarifikasi gereja dilakukan 4 jam lebih awal, pada skenario serupa beban siaga turun ±X%."
4. **Masukkan ke kalibrasi.**
   - `config/kalibrasi_rujukan.yaml` diubah dari satu kasus penempat (Wamena 2023) menjadi **daftar kasus**, masing-masing dengan bobot dan tingkat keyakinan.
   - `calibration.py` (grid dan ABC) menghitung galat gabungan dari semua kasus.
   - Setelah kalibrasi ulang: versi model naik, pustaka (R2a) ditandai usang, dan kartu (R1) perlu dicetak ulang.
5. **Lencana validasi per kabupaten** diperbarui, mis. "2 kasus nyata, galat jam memanas ±3 jam, terakhir diperbarui Maret 2027".

### Perubahan teknis
- Skema `kajian/<id>.yaml` beserta validasinya.
- Modul baru `sintetis/kajian.py`: ubah linimasa menjadi skenario dan spesifikasi intervensi (memakai F2 dan F5), jalankan putar ulang, susun tabel pembanding, lalu buat PDF kajian (memakai `laporan_pdf.py`).
- **Impor dari Ruang Situasi:** log `out/situasi/*.json` bisa dijadikan draf kajian dengan satu klik.
- Perluasan `calibration.py` agar mendukung banyak kasus.
- CLI:
  - `python -m sintetis kajian buat --dari-situasi <log>`
  - `python -m sintetis kajian analisis <id>`
  - `python -m sintetis kalibrasi --kasus kajian/`
- UI: formulir **Kajian pasca-kejadian** berupa linimasa yang bisa ditambah baris demi baris, dengan hasil analisis di bawahnya.

### Kriteria penerimaan
- [ ] Satu kajian bisa diisi dalam 30 menit atau kurang oleh petugas terlatih. *Formulir dan draf dari Ruang Situasi sudah ada; waktu pengisian perlu diuji dengan petugas.*
- [x] Putar ulang dan tabel pembanding terbentuk otomatis.
- [x] Kalibrasi menerima satu kasus atau lebih dengan bobot.
- [x] Setelah kalibrasi ulang, versi model berubah dan pustaka serta kartu ditandai usang.
- [x] Kasus *near miss* bisa dicatat dan ikut dipakai kalibrasi.
- [x] Tidak ada nama orang atau akun tersimpan. *Validasi menolak kolom nama/akun/telepon dan teks bernomor telepon atau @akun.*

### Risiko dan catatan
- **Data kejadian nyata sensitif.** Simpan lokal, akses terbatas, dan catat siapa mengubah apa. Pembagian keluar hanya dalam bentuk agregat.
- **Bias kasus.** Bila hanya kejadian besar yang dicatat, model akan cenderung melebih-lebihkan eskalasi. Pencatatan *near miss* wajib didorong.
- Linimasa dari berita sering tidak pasti. Setiap butir diberi tingkat keyakinan, dan butir berkeyakinan rendah diberi bobot kecil di kalibrasi.

---

## Di luar cakupan revisi ini

Butir berikut disarankan sebelumnya tetapi tidak dipilih. Catat sebagai pertimbangan berikutnya:
- Pembagian tampilan per peran dan login/audit menyeluruh. Sebagian kecil tetap dibutuhkan R2b, R5, dan R6 untuk melindungi data.
- Pembatasan modul provokator dan pembuat contoh pesan provokasi untuk non-peneliti.
- Pemindahan kunci API dari `LLM API Key.txt` ke variabel lingkungan.
- Laporan isu dari relawan lewat HP (KoboToolbox/ODK).
- Perangkat bahan prebunking siap pakai.

## Pertanyaan terbuka untuk tim

1. Siapa mitra yang mengesahkan Kartu Respons sebelum dibagikan (Diskominfo provinsi, sinode, lembaga adat)?
2. Jenis isu apa saja yang masuk pustaka tahap pertama? Apakah lima templat di R2a sudah tepat?
3. Siapa penutur asli dan praktisi lapangan yang meninjau templat kalimat kartu dan teks latihan?
4. Di kabupaten mana latihan meja pertama paling memungkinkan, dan lembaga mana yang bersedia ikut?
5. Siapa yang berwenang mengisi dan mengakses Kajian pasca-kejadian, dan di mana datanya disimpan?
6. Berapa nilai awal `jeda_deteksi_jam` yang wajar per tipe wilayah? Ini perlu ditanyakan di FGD.
