'use strict';
/* Latihan meja (R5): satu halaman untuk fasilitator dan peserta lewat WebSocket /ws/latihan. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const q = new URLSearchParams(location.search);
let PERAN = q.get('peran') === 'fasilitator' ? 'fasilitator' : 'peserta';
let SESI = q.get('sesi'), ws = null, ST = null, META = null, chart = null;

function pesan(t) { const p = $('#pesan'); p.hidden = !t; p.textContent = t || ''; if (t) setTimeout(() => p.hidden = true, 6000); }
function kirim(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }

function sambung(pertama) {
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/latihan`);
  ws.onopen = () => ws.send(JSON.stringify(pertama));
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.error) { pesan(m.error); $('#b-msg') && ($('#b-msg').textContent = ''); }
    if (m.sesi) {
      SESI = m.sesi; PERAN = m.peran;
      history.replaceState(null, '', `/latihan?sesi=${encodeURIComponent(SESI)}&peran=${PERAN}`);
      $('#f-buat').hidden = true; $('#ruang').hidden = false; $('#fas').hidden = PERAN !== 'fasilitator';
      $('#tautan').textContent = `${location.origin}/latihan?sesi=${SESI}&peran=peserta`;
    }
    if (m.status) render(m.status);
    if (m.debrief) renderDebrief(m.debrief);
  };
  ws.onclose = () => pesan('Sambungan terputus. Muat ulang halaman untuk bergabung lagi.');
}

function isiDistrik(kab) {
  const k = META && META.kabupaten.find(x => x.id === kab);
  if (!k || $('#p-distrik').dataset.kab === kab) return;
  $('#p-distrik').dataset.kab = kab;
  $('#p-distrik').innerHTML = k.distrik.map(d => `<option>${esc(d)}</option>`).join('');
}
function render(s) {
  ST = s;
  isiDistrik(s.kabupaten);
  $('#judul').textContent = `Latihan meja · ${s.skenario}`;
  $('#info').textContent = `ronde ${s.ronde}/${s.n_ronde} · jam simulasi ${s.jam} · ${PERAN}${s.selesai ? ' · SELESAI' : ''}`;
  $('#versi').textContent = s.versi_model;
  $('#kabar').innerHTML = s.kabar.slice().reverse().map(k => `<div class="kabar"><b>Jam ${k.jam}</b> · ${esc(k.teks)}</div>`).join('');
  $('#laporan').innerHTML = s.laporan.slice().reverse().map(l => `<li class="l${l.level}"><b>${esc(l.distrik)}</b> <span class="muted">${esc(l.kabupaten)} · diterima jam ${l.jam_diterima} · ${esc(l.sumber)}</span><br>${esc(l.teks)}</li>`).join('')
    || '<li class="muted">Belum ada laporan. Laporan datang terlambat 1–3 jam, sebagian keliru, dan tidak ada kabar dari distrik tanpa sinyal kecuali lewat tim keliling.</li>';
  const sisa = s.sisa;
  $('#sisa').textContent = `Sisa: ${sisa.tokoh} tokoh · ${sisa.tim} orang tim keliling · radio ${sisa.radio.join(', ') || 'habis'} · akun resmi ${sisa.medsos ? 'tersedia' : 'sudah dipakai'}`;
  const sel = $('#p-pilihan'), lama = sel.value;
  sel.innerHTML = s.pilihan.map(p => `<option value="${esc(p.id)}" ${p.tersedia ? '' : 'disabled'} data-maks="${p.maks || ''}" data-jml="${p.perlu_jumlah ? 1 : ''}">${esc(p.label)}${p.tersedia ? '' : ' — ' + esc(p.alasan)}</option>`).join('');
  if ([...sel.options].some(o => o.value === lama)) sel.value = lama;
  sel.onchange(); $('#f-putus button').disabled = s.selesai;
  $('#keputusan').innerHTML = s.keputusan.length ? '<tr><th>Ronde</th><th>Jam</th><th>Langkah</th><th>Jumlah</th></tr>' +
    s.keputusan.map(k => `<tr><td>${k.ronde}</td><td>${k.jam}</td><td>${esc(k.label)}</td><td>${k.jumlah ?? ''}</td></tr>`).join('') : '';
  $('#n-msg').textContent = s.penilaian_ronde.includes(s.ronde) ? `Ronde ${s.ronde} sudah dinilai (penilaian tambahan tetap boleh).` : '';
  if (PERAN === 'fasilitator' && s.fasilitator) renderFas(s);
}
function renderFas(s) {
  const f = s.fasilitator;
  $('#f-ronde').disabled = s.selesai;
  $('#f-setuju').checked = s.persetujuan;
  $('#f-skrip').innerHTML = f.skrip.map((x, i) => `<button data-i="${i}" ${f.skrip_sudah.includes(i) ? 'disabled' : ''} title="${esc(x.teks)}">Jam ${x.jam}: ${esc(x.teks.slice(0, 40))}…</button>`).join('');
  $$('#f-skrip [data-i]').forEach(b => b.onclick = () => kirim({ aksi: 'suntik', indeks: +b.dataset.i }));
  $('#f-distrik').innerHTML = '<tr><th>Distrik</th><th>Siaga ‰</th><th>Risiko × ambang</th><th>Memanas</th></tr>' +
    f.distrik.map(d => `<tr><td>${esc(d.distrik)} <span class="muted">${esc(d.kabupaten)}</span></td><td>${d.siaga_permil}</td><td>${d.risiko_x_ambang}</td><td>${d.memanas ? 'ya' : ''}</td></tr>`).join('');
  if (window.echarts) {
    chart = chart || echarts.init($('#chart-f'));
    const ink = getComputedStyle(document.documentElement).getPropertyValue('--muted');
    chart.setOption({ animation: false, legend: { top: 0, textStyle: { color: ink } }, grid: { top: 28, left: 48, right: 10, bottom: 28 }, tooltip: { trigger: 'axis' },
      xAxis: { type: 'value', name: 'jam', axisLabel: { color: ink } }, yAxis: { type: 'value', axisLabel: { color: ink, formatter: v => (v * 1000).toFixed(0) + '‰' } },
      series: [{ name: 'Penyebar', type: 'line', symbol: 'none', color: '#e5484d', data: f.kurva.jam.map((j, i) => [j, f.kurva.penyebar[i]]) },
               { name: 'Siaga aktif', type: 'line', symbol: 'none', color: '#a855f7', data: f.kurva.jam.map((j, i) => [j, f.kurva.siaga[i]]) },
               ...s.keputusan.map(k => ({ type: 'line', markLine: { symbol: 'none', label: { formatter: k.label.slice(0, 18), color: ink }, data: [{ xAxis: k.jam }] } }))] }, true);
  }
}
function renderDebrief(d) {
  const box = $('#debrief'); box.hidden = false;
  const k = d.keputusan_menentukan[0];
  box.innerHTML = `<h2>Debrief</h2><table class="tabel"><tr><th>Jalur (seed sama)</th><th>Beban siaga /1.000</th><th>Puncak ‰</th><th>Distrik memanas</th></tr>
    ${Object.values(d.jalur).map(j => `<tr><td>${esc(j.nama)}</td><td>${Math.round(j.beban_siaga_per_1000)}</td><td>${j.puncak_siaga_permil}</td><td>${j.distrik_memanas}</td></tr>`).join('')}</table>
    <p class="muted">${esc(d.catatan || '')}</p>
    ${k ? `<p><b>Keputusan paling menentukan:</b> jam ${k.jam}, ${esc(k.label)} (tanpa keputusan ini beban siaga naik ±${Math.round(k.beban_naik_bila_tidak_dilakukan)}).</p>` : ''}
    <p>Penilaian kewajaran: ya ${d.penilaian_ringkas.ya}, sebagian ${d.penilaian_ringkas.sebagian}, tidak ${d.penilaian_ringkas.tidak}.</p>
    ${d.pdf_url ? `<p><a class="tombol utama" href="${esc(d.pdf_url)}" target="_blank" rel="noopener">PDF debrief</a></p>` : d.pdf_error ? `<p class="muted">PDF gagal: ${esc(d.pdf_error)}</p>` : ''}`;
}

async function siapkanBuat() {
  META = await (await fetch('/api/lapangan/meta')).json();
  $('#versi').textContent = META.versi_model.teks;
  const isi = (sel, arr) => sel.innerHTML = arr.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join('');
  isi($('#b-jenis'), META.jenis.map(j => [j.id, j.nama]));
  isi($('#b-kab'), META.kabupaten.map(k => [k.id, k.nama]));
  isi($('#b-kanal'), META.kanal.map(k => [k.id, k.nama]));
  isi($('#b-profil'), META.profil.map(p => [p, p]));
  isi($('#b-skrip'), [['', '(tanpa skrip)'], ...META.skrip.map(s => [s.berkas, s.nama])]);
  if (META.profil.includes('kep_yapen')) { $('#b-kab').value = 'kep_yapen'; $('#b-profil').value = 'kep_yapen'; }
  $('#f-buat').hidden = false;
  $('#f-buat').onsubmit = e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target).entries());
    $('#b-msg').textContent = 'Menyiapkan dunia simulasi…';
    sambung({ buat: { ...f, ronde: +f.ronde, agen: +f.agen, durasi_jam: +f.durasi_jam,
      skenario: `${$('#b-jenis').value}__${$('#b-kab').value}__${$('#b-kanal').value}__${$('#b-ajak').value}` } });
  };
}

function siapkanRuang() {
  $('#p-pilihan').onchange = () => {
    const o = $('#p-pilihan').selectedOptions[0];
    $('#p-jml-wrap').hidden = !(o && o.dataset.jml);
    if (o && o.dataset.maks) { $('#p-jumlah').max = o.dataset.maks; $('#p-jumlah').placeholder = `maks. ${o.dataset.maks}`; }
  };
  $('#f-putus').onsubmit = e => {
    e.preventDefault();
    const distrik = [...$('#p-distrik').selectedOptions].map(o => o.value);
    kirim({ aksi: 'putuskan', pilihan: $('#p-pilihan').value, jumlah: +$('#p-jumlah').value || null, distrik, catatan: $('#p-catatan').value });
    $('#p-catatan').value = '';
  };
  $('#f-nilai').onsubmit = e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target).entries());
    if (!f.nilai) return pesan('Pilih ya, sebagian, atau tidak');
    kirim({ aksi: 'nilai', ronde: ST ? ST.ronde : 0, nilai: f.nilai, catatan: f.catatan }); e.target.reset();
  };
  $('#f-ronde').onclick = () => { $('#f-ronde').disabled = true; kirim({ aksi: 'ronde' }); };
  $('#f-debrief').onclick = () => { $('#f-debrief').textContent = 'Menghitung tiga jalur…'; kirim({ aksi: 'debrief' }); };
  $('#f-suntik').onclick = () => { const t = $('#f-teks').value.trim(); if (t) kirim({ aksi: 'suntik', teks: t }); $('#f-teks').value = ''; };
  $('#f-setuju').onchange = e => kirim({ aksi: 'setuju', persetujuan: e.target.checked });
}

(async function init() {
  siapkanRuang();
  try { META = META || await (await fetch('/api/lapangan/meta')).json(); } catch (e) { /* tetap jalan tanpa daftar distrik */ }
  if (SESI) { $('#ruang').hidden = false; $('#fas').hidden = PERAN !== 'fasilitator'; sambung({ gabung: SESI, peran: PERAN }); }
  else if (PERAN === 'fasilitator') siapkanBuat();
  else pesan('Minta tautan peserta dari fasilitator (berisi ?sesi=...).');
})();
