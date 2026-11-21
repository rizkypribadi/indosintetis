# Eksplorasi Model "Indonesia Sintetis": Klarifikasi Resmi Media Sosial vs Klarifikasi Lewat Gereja dan Tokoh Adat pada Skenario Penculikan Anak di Serui

## Ringkasan eksekutif

- Laporan ini membandingkan dua putaran simulasi berbasis agen untuk skenario hipotetis isu penculikan anak di Serui: intervensi C (klarifikasi resmi di media sosial, T+6 jam) dan intervensi D (klarifikasi lewat gereja dan tokoh adat, T+12 jam). Semua agen, kampung, dan distrik bersifat fiktif; parameter belum dikalibrasi penuh.
- Pada kedua putaran, proporsi penyebar (I) memuncak sangat awal, yaitu 3,14% pada jam ke-13,5 (C) dan 3,14% pada jam ke-13,5 (D). Artinya, puncak penyebaran terjadi jauh sebelum intervensi mana pun masuk.
- Puncak siaga aktif (A) berbeda: 3,34% pada jam ke-72 untuk C, dan 2,92% pada jam ke-53,5 untuk D. Angka ini menunjukkan perbedaan pola waktu, tetapi belum bisa disebut sebagai efek intervensi yang signifikan karena hanya satu putaran per skenario.
- Cakupan "pernah dengar" hampir sama (54,07% untuk C, 52,97% untuk D), sedangkan proporsi yang tidak lagi rentan di akhir lebih tinggi pada D (86,68%) dibanding C (81,11%).
- Karena ini putaran tunggal, kesimpulan tentang keunggulan salah satu intervensi belum dapat ditarik. Diperlukan Monte Carlo dengan banyak ulangan untuk memperoleh IK 95% dan menilai stabilitas peringkat.

## Skenario dan pengaturan

- Skenario: isu hipotetis penculikan anak di Serui, Kabupaten Kepulauan Yapen, mulai Senin jam 12.00, disimulasikan 3 hari (72 jam).
- Isu menyebar lewat pesan berantai di grup WhatsApp orang tua murid, mulut ke mulut di pasar, dua akun Facebook lokal, dan percakapan setelah ibadah. Isu memuat ajakan tindakan (jaga sekolah, pelabuhan, jalan masuk kampung, periksa orang baru) dengan intensitas emosi 0,9 (takut dan marah).
- Skala model: 100.000 agen, 104 distrik, seed 1, seed dunia 42, mode agen kunci "sumopod" dengan model deepseek-v4-flash.
- Intervensi C: klarifikasi resmi Polres/Diskominfo di Facebook dan WA pada T+6 jam (Senin 18.00).
- Intervensi D: imbauan Sinode gereja dan lembaga adat bahwa isu culik anak itu hoaks, pada T+12 jam (Selasa 00.00), diterima 72 agen kunci dan 2.456 jemaat/kampung.
- Keputusan agen kunci: 455 keputusan pada C (408 dari LLM, 38 cache, 9 ABM) dan 537 keputusan pada D (379 LLM, 150 cache, 8 ABM).

## Dinamika penyebaran

- Puncak penyebar (I) terjadi sangat cepat pada kedua putaran: 3,14% pada jam ke-13,5. Ini berarti penyebaran memuncak sekitar 1,5 jam setelah isu ditanam lewat tatap muka (T+5) dan sebelum intervensi C masuk (T+6) maupun D masuk (T+12).
- Proporsi terpapar (E) melonjak di awal (6,63% pada jam ke-6 untuk C; 6,51% untuk D), lalu turun tajam menjadi 0,80% pada jam ke-12 dan mendekati nol setelahnya.
- Proporsi skeptis/meluruskan (Z) tumbuh cepat: 31,90% pada jam ke-6 (C) dan 32,04% (D), lalu naik menjadi 57,21% (C) dan 56,89% (D) pada jam ke-12. Pada akhir simulasi, Z mencapai 75,29% (C) dan 82,34% (D).
- Siaga aktif (A) naik bertahap: 0,89% pada jam ke-6 menjadi 3,34% pada jam ke-72 (C), dan 0,87% menjadi 2,61% pada jam ke-72 (D). Puncak A pada D tercatat 2,92% di jam ke-53,5.
- Kanal paparan hoaks pertama didominasi tatap muka di semua tipe wilayah, terutama pedalaman (75,1% pada C; 72,0% pada D) dan pesisir (68,7% pada C; 67,5% pada D). WhatsApp lebih menonjol di kota (58,1% pada C; 56,5% pada D). Radio tidak tercatat sebagai kanal paparan pertama (0,0%).
- Koreksi sampai lewat: pada C, jalur intervensi menyumbang 0,2% koreksi, sedangkan pada D jalur intervensi menyumbang 9,3%. Jalur tatap muka tetap dominan (33,3% pada C; 30,8% pada D), diikuti WA (29,3% pada C; 26,6% pada D).
- Distrik eskalasi terawal pada C muncul di Kab. Jayapura 05 (jam 2,5; risiko maks 1,98%) dan pada D juga di Kab. Jayapura 05 (jam 2,5; risiko maks 1,98%). Ini menunjukkan eskalasi awal terjadi di wilayah bertipe kota, bukan di lokasi isu.

## Perbandingan per tipe wilayah

| Tipe wilayah | Puncak penyebar C | Puncak penyebar D | Puncak siaga C (jam) | Puncak siaga D (jam) | Tidak lagi rentan akhir C | Tidak lagi rentan akhir D |
|---|---|---|---|---|---|---|
| Kota | 1,43% (jam 13,0) | 1,69% (jam 13,0) | 1,37% (jam 7,5) | 1,44% (jam 22,5) | 85,43% | 90,29% |
| Pesisir | 4,75% (jam 14,0) | 4,33% (jam 15,0) | 4,91% (jam 72,0) | 4,21% (jam 53,5) | 65,73% | 76,60% |
| Pedalaman | 5,18% (jam 13,5) | 4,92% (jam 13,5) | 5,65% (jam 72,0) | 4,72% (jam 51,0) | 77,63% | 83,39% |
| Kepulauan | 4,14% (jam 13,5) | 3,97% (jam 13,0) | 4,95% (jam 71,5) | 4,00% (jam 53,5) | 80,60% | 85,74% |

- Pedalaman mencatat puncak penyebar tertinggi pada kedua putaran (5,18% untuk C; 4,92% untuk D), diikuti pesisir dan kepulauan. Kota memiliki puncak penyebar terendah (1,43% untuk C; 1,69% untuk D).
- Siaga per 1.000 penduduk paling tinggi di pedalaman (2.805,8 pada C; 2.566,1 pada D) dan pesisir (2.713,8 pada C; 2.252,2 pada D), jauh di atas kota (794,8 pada C; 885,2 pada D).
- Distrik dengan puncak siaga tertinggi pada C: Kepulauan Yapen 04 (13,43%), Kepulauan Yapen 13 (13,14%), Mamberamo Raya 03 (12,45%), Waropen 11 (12,42%), Keerom 07 (12,02%). Pada D: Supiori 05 (10,36%), Keerom 03 (10,12%), Keerom 07 (9,13%), Keerom 06 (9,07%), Kab. Jayapura 12 (8,81%).
- Jumlah distrik yang mengalami eskalasi: 102 dari 104 pada C, dan 104 dari 104 pada D. Eskalasi pertama muncul pada jam 2,5 di kedua putaran.
- Perbedaan angka antarwilayah dan antarpu-taran di atas belum diuji secara statistik. Tanpa Monte Carlo, belum bisa disimpulkan apakah perbedaan ini efek intervensi atau variasi acak satu kali jalan.

## Peran agen kunci

- Pada C, dari 455 keputusan: 247 tanya dulu, 78 luruskan, 69 teruskan + ajak jaga kampung, 33 abaikan, 25 teruskan, 3 tanya dulu + ajak jaga kampung.
- Pada D, dari 537 keputusan: 267 tanya dulu, 121 luruskan, 84 teruskan + ajak jaga kampung, 35 abaikan, 30 teruskan.
- Proporsi "luruskan" lebih tinggi pada D (121 dari 537) dibanding C (78 dari 455), sejalan dengan masuknya imbauan gereja dan lembaga adat pada T+12. Namun ini baru satu putaran, sehingga belum bisa disebut efek yang stabil.
- Contoh pesan agen kunci (kutipan dari data):
  - Pdt. Yohana (Pendeta jemaat, Kota Jayapura 02), aksi tanya_dulu, Senin 12.30: "Sa dengar kabar ini. Sa minta kitorang jang langsung percaya. Sa mau tanya dulu ke pihak berwenang dan sekolah, apakah0 benar ada kejadian. Sambil tunggu, jang biarkan anak-anak pulang sendiri. Kitorang jaga anak deng cara yang damai, bukan main hakim sendiri."
  - Kaka Yohanes (Tokoh pemuda, Kab. Jayapura 15), aksi teruskan, Senin 14.30: "Pemuda, bapa-bapa! Kabar dari pangkalan ojek: ada orang naik motor tanpa plat tawari anak SD permen dan uang, hampir bawa ke pelabuhan. Sa teruskan biar kitorang waspada. Jaga anak di sekolah dan jalan masuk kampung. Periksa orang baru yang tra dikenal!"
  - Ondoafi Matius (Tokoh adat, Kab. Jayapura 06), aksi luruskan, Senin 16.30 (putaran D): "Sa dengar kabar itu, tapi banyak yang bilang itu hoaks. Jangan cepat percaya. Kitorang cek dulu ke orang yang dipercaya. Jaga anak-anak tetap dekat, tapi jangan main hakim sendiri. Kalau ada yang curiga, lapor ke bapa/mama di kampung dulu, jangan langsung sebar."

## Rekomendasi draf

Catatan: rekomendasi berikut adalah arahan untuk pengujian lanjutan dan perencanaan komunikasi, bukan hasil prediksi. Semua angka di atas berasal dari satu putaran eksplorasi.

- Untuk tim riset: jalankan Monte Carlo (misalnya puluhan hingga ratusan ulangan per skenario) sebelum menyimpulkan efek intervensi. Laporkan IK 95% untuk puncak penyebar, puncak siaga, siaga per 1.000, dan proporsi tidak lagi rentan, serta stabilitas peringkat antarputaran. Bandingkan C dan D dengan baseline A pada kondisi seed dan parameter yang sama.
- Untuk mitra pemerintah daerah (kota): prioritaskan kanal WhatsApp dan Facebook untuk klarifikasi cepat, karena paparan pertama di kota didominasi WA (58,1% pada C; 56,5% pada D). Waktu respons perlu diuji lebih awal dari T+6, mengingat puncak penyebar sudah terjadi pada jam ke-13,5.
- Untuk mitra pemerintah daerah (pesisir, pedalaman, kepulauan): utamakan jalur tatap muka (ibadah, pasar, adat, sekolah) karena kanal ini mendominasi paparan pertama (67,5%–75,1%). Libatkan tokoh gereja dan tokoh adat sejak awal, bukan hanya di T+12, dan uji apakah percepatan waktu imbauan menurunkan puncak siaga.
- Untuk wilayah dengan puncak siaga tinggi (misalnya distrik bertipe kepulauan dan pedalaman pada daftar di atas): siapkan pesan yang menenangkan sekaligus mengarahkan pelaporan ke pihak berwenang, bukan tindakan sendiri. Uji efeknya lewat Monte Carlo sebelum dijadikan kebijakan.
- Untuk semua pihak: gunakan hasil ini sebagai bahan diskusi skenario dan perencanaan kanal, bukan sebagai dasar klaim bahwa satu intervensi lebih baik dari yang lain.

## Keterbatasan dan langkah validasi

- Ini adalah putaran tunggal dari dashboard. Satu putaran tidak cukup untuk menyimpulkan efek intervensi; perbedaan antara C dan D bisa berasal dari variasi acak, bukan dari intervensi.
- Belum ada IK 95% dan belum ada uji signifikansi. Semua perbandingan angka bersifat deskriptif.
- Parameter model belum dikalibrasi penuh. Agen, kampung, dan distrik bersifat fiktif; nama agen kunci adalah persona sintetis.
- Hasil adalah eksplorasi model, bukan prediksi kejadian nyata. Model tidak memperhitungkan faktor di luar data, seperti dinamika politik lokal, kondisi jaringan, atau respons institusi nyata.
- Langkah validasi yang disarankan: (1) Monte Carlo dengan banyak ulangan dan pelaporan IK 95%; (2) uji sensitivitas terhadap waktu intervensi (lebih awal dari T+6 dan T+12); (3) uji sensitivitas terhadap kanal (misalnya menambah radio yang pada data ini 0,0%); (4) kalibrasi parameter dengan data lapangan bila tersedia; (5) bandingkan hasil model dengan evaluasi komunikasi risiko nyata di wilayah bertipe serupa, tanpa menyamakan agen sintetis dengan individu atau kelompok nyata.

---

> Draf dibuat otomatis oleh deepseek-v4-flash (sumopod) pada 20261119-094543 dari angka hasil simulasi. Periksa ulang sebelum dipakai; semua agen fiktif dan parameter belum dikalibrasi penuh.
