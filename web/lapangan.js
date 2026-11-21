'use strict';
/* Alat lapangan: Ruang Situasi (R2b), Kartu (R1), Perencana (R4), Latihan (R5), Kajian (R6). Tanpa pustaka luar. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pct = x => x == null ? '–' : Math.round(x * 100) + '%';
const num = (x, d = 0) => x == null ? '–' : (+x).toFixed(d).replace('-', '−');
let META = null;

async function api(url, opt) {
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

function tab(nama) {
  $$('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === nama));
  $$('.pane').forEach(p => p.classList.toggle('on', p.id === 'pane-' + nama));
  if (nama === 'latihan') muatSesi();
  if (nama === 'kajian') muatKajian();
}
function isiSelect(sel, items, val) { sel.innerHTML = items.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join(''); if (val != null) sel.value = val; }
function distrikUntuk(kab) { return (META.kabupaten.find(k => k.id === kab) || { distrik: [] }).distrik; }
function nowLocal() { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); }

/* ---------------------------------------------------------------- Ruang Situasi */
function siapkanSituasi() {
  isiSelect($('#s-jenis'), [...META.jenis.map(j => [j.id, j.nama + (j.perlu_tinjauan ? ' (perlu tinjauan)' : '')]), ['lainnya', 'Lainnya…']]);
  isiSelect($('#s-kab'), META.kabupaten.map(k => [k.id, k.nama]));
  const isiDis = () => isiSelect($('#s-dis'), [['', '(tidak tahu)'], ...distrikUntuk($('#s-kab').value).map(d => [d, d])]);
  $('#s-kab').onchange = isiDis; isiDis();
  $('#s-jenis').onchange = () => $('#s-desk-wrap').hidden = $('#s-jenis').value !== 'lainnya';
  $('#s-kanal').innerHTML = META.kanal.map((k, i) => `<label><input type="radio" name="kanal" value="${k.id}" ${i ? '' : 'checked'}>${esc(k.nama)}</label>`).join('');
  $('#s-diket').value = nowLocal();
  $('#f-situasi').onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(e.target), body = Object.fromEntries(f.entries());
    body.waktu_sekarang = nowLocal();
    const btn = e.target.querySelector('button[type=submit]'); btn.disabled = true;
    try { tampilSituasi(await post('/api/situasi', body)); }
    catch (er) { $('#s-hasil').innerHTML = `<div class="kosong">Gagal: ${esc(er.message)}</div>`; }
    finally { btn.disabled = false; }
  };
}
function tampilSituasi(h) {
  const c = h.kecocokan || {}, w = h.waktu || {}, p = h.posisi, ts = h.tindakan_sekarang;
  const pus = typeof h.pustaka === 'string' ? h.pustaka : (h.pustaka || {}).teks || '';
  let html = `<section><div class="besar">${esc(h.kabupaten_nama)}</div>
    <p>Kecocokan dengan pustaka: <span class="pil ${esc(c.label)}">${esc(c.label)}</span>${(c.tidak_cocok || []).length ? ` <span class="muted">tidak cocok: ${esc(c.tidak_cocok.join(', '))}</span>` : ''}</p>
    <p class="muted">Jam ke-${num(w.jam_sejak_beredar, 1)} sejak isu beredar${w.jam_beredar_diperkirakan ? ' (diperkirakan dari jeda deteksi bawaan)' : ''} · jam ke-${num(w.jam_sejak_diketahui, 1)} sejak diketahui petugas</p>
    ${(h.pustaka || {}).usang ? '<p class="pil sedang">Pustaka usang untuk versi model sekarang</p>' : ''}
    <p class="muted">${esc(pus).replace(/\n/g, '<br>')}</p></section>`;
  if (p) html += `<section><h3>Posisi di kurva</h3><p>${esc(p.kalimat)}</p><p class="muted">Pada skenario serupa tanpa tindakan, ${pct(p.peluang_memanas_tanpa_tindakan)} putaran memanas.</p></section>`;
  if (h.catatan_tanda_bahaya) html += `<section><h3>Tanda bahaya</h3><p>${esc(h.catatan_tanda_bahaya)}</p></section>`;
  html += `<section><h3>Tindakan sekarang</h3>${ts ? `<div class="besar">${esc(ts.langkah)}</div>
    <p>${esc(ts.nama)} · menurunkan beban siaga ±${num(-ts.efek_persen)}%${ts.efek_rentang && ts.efek_rentang[0] != null ? ` (IK 95% ${num(-ts.efek_rentang[1])}–${num(-ts.efek_rentang[0])}%)` : ''} · keyakinan <span class="pil ${esc(ts.keyakinan)}">${esc(ts.keyakinan)}</span></p>
    <p class="muted">${esc(ts.alasan || '')}</p>` : '<p>Belum ada tindakan yang terbukti menurunkan beban siaga dan masih bisa dilakukan.</p>'}
    <p><b>Harga keterlambatan.</b> ${esc(h.harga_keterlambatan || '')}</p></section>`;
  if ((h.tindakan || []).length) html += `<section><h3>Semua pilihan</h3><ul class="tindakan">${h.tindakan.map(t => `<li class="${esc(t.status)}">
    <b>${esc(t.kode)} · ${esc(t.langkah)}</b> <span class="pil ${esc(t.status)}">${{ bisa: 'masih bisa', lewat: 'jendela lewat', sebelum_isu: 'hanya sebelum isu' }[t.status]}</span>
    <span class="pil ${esc(t.keyakinan)}">${esc(t.keyakinan)}</span><br><span class="muted">efek ${num(t.efek_persen)}% · ${esc(t.alasan || '')}</span></li>`).join('')}</ul></section>`;
  if ((h.distrik || []).length) html += `<section><h3>Distrik yang kemungkinan terdampak berikutnya</h3><table class="tabel"><tr><th>Distrik</th><th>Peluang memanas</th><th>Biasanya jam ke-</th></tr>
    ${h.distrik.map(d => `<tr><td>${esc(d.nama)}${d.asal ? ' <b>(asal)</b>' : ''} <span class="muted">${esc(d.kabupaten)}</span></td><td>${pct(d.peluang_memanas)}</td><td>${num(d.jam_p10)}–${num(d.jam_p90)}</td></tr>`).join('')}</table>
    <p class="muted">"Pada skenario serupa", bukan "akan terjadi".</p></section>`;
  html += `<section class="aksi"><a class="tombol" href="${esc(h.tautan.kartu)}" target="_blank" rel="noopener">Kartu respons kabupaten</a>
    <button id="s-ke-rencana">Perencana</button><button id="s-hitung">Hitung khusus (latar)</button><button id="s-kajian">Jadikan draf kajian</button>
    <span id="s-msg" class="muted"></span></section><section id="s-khusus" hidden></section>
    <p class="muted">Log: out/situasi/${esc(h.id)}.json · ${esc(h.versi_model.teks)}</p>`;
  $('#s-hasil').innerHTML = html;
  $('#s-ke-rencana').onclick = () => { tab('perencana'); const pr = META.profil.find(x => x === h.masukan.kabupaten); if (pr) $('#r-profil').value = pr; };
  $('#s-kajian').onclick = async () => { try { const k = await post(`/api/kajian/dari-situasi/${encodeURIComponent(h.id)}`); $('#s-msg').textContent = `Draf kajian ${k.id} tersimpan.`; } catch (e) { $('#s-msg').textContent = e.message; } };
  $('#s-hitung').onclick = async () => {
    try { await post(`/api/situasi/${encodeURIComponent(h.id)}/hitung`); } catch (e) { $('#s-msg').textContent = e.message; return; }
    $('#s-msg').textContent = 'Menghitung simulasi khusus di latar… hasil pustaka tetap berlaku sementara.';
    const poll = async () => {
      const s = await api(`/api/situasi/${encodeURIComponent(h.id)}/status`);
      if (s.jalan) { $('#s-msg').textContent = 'Menghitung… ' + (s.log || []).at(-1); return setTimeout(poll, 2500); }
      if (s.error) { $('#s-msg').textContent = 'Gagal: ' + s.error; return; }
      $('#s-msg').textContent = 'Hitung khusus selesai.';
      const box = $('#s-khusus'); box.hidden = false;
      box.innerHTML = `<h3>Hitung khusus (mulai sekarang)</h3><ul class="tindakan">${(s.hasil || []).map(r => `<li><b>${esc(r.kode)}</b> ${esc(r.nama)} · ${esc(r.wilayah)}: ${num(r.efek_persen)}% ${r.signifikan ? '' : '<span class="muted">(belum pasti)</span>'}</li>`).join('')}</ul>`;
    };
    poll();
  };
}

/* ---------------------------------------------------------------- Kartu */
function siapkanKartu() {
  $('#k-status').textContent = META.pustaka.teks;
  $('#k-list').innerHTML = META.kabupaten.map(k => `<li><span><b>${esc(k.nama)}</b> <span class="muted">${k.distrik.length} distrik</span></span>
    <a class="tombol utama" href="/api/kartu/${encodeURIComponent(k.id)}.pdf" target="_blank" rel="noopener">Unduh PDF</a></li>`).join('');
}

/* ---------------------------------------------------------------- Perencana */
let rencanaHasil = null;
function siapkanPerencana() {
  isiSelect($('#r-profil'), META.profil.map(p => [p, p]));
  isiSelect($('#r-jenis'), META.jenis.map(j => [j.id, j.nama]));
  isiSelect($('#r-kanal'), META.kanal.map(k => [k.id, k.nama]));
  $('#f-rencana').onsubmit = async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target).entries());
    const kab = f.profil;                     // nama profil = id kabupaten (config/sumber_daya/<kab>.yaml)
    const body = { profil: f.profil, agen: +f.agen, putaran_awal: +f.putaran_awal, ketahanan: +f.ketahanan,
      skenario: `${$('#r-jenis').value}__${kab}__${$('#r-kanal').value}__${$('#r-ajak').value}` };
    try { await post('/api/perencana', body); } catch (er) { $('#r-status').textContent = er.message; return; }
    pollRencana();
  };
  pollRencana();
}
async function pollRencana() {
  const s = await api('/api/perencana-status');
  $('#r-status').textContent = s.jalan ? 'Berjalan… ' + ((s.log || []).at(-1) || '') : s.error ? 'Gagal: ' + s.error : '';
  $('#f-rencana button[type=submit]').disabled = !!s.jalan;
  if (s.jalan) return setTimeout(pollRencana, 3000);
  if (s.hasil) tampilRencana(s.hasil);
}
function tampilRencana(h) {
  rencanaHasil = h;
  $('#r-hasil').innerHTML = `<section><p class="muted">${h.jumlah_rencana_layak} rencana layak · ${h.ronde.length} ronde penyempurnaan · ${Math.round(h.detik / 60)} menit · ${esc(h.versi_model.teks)}</p>
    <ul>${h.kalimat_sumber_daya.map(k => `<li>${esc(k)}</li>`).join('')}</ul></section>` +
    h.rencana.map((r, i) => `<section class="rencana"><h3>#${i + 1} · efek ${num(r.efek_persen)}% <span class="muted">(IK 95% ${num(r.efek_rentang[0])} s.d. ${num(r.efek_rentang[1])}%)</span>
      <span class="pil ${esc(r.keyakinan)}">${esc(r.keyakinan)}</span></h3>
      <ol>${r.langkah.map(l => `<li>jam ${num(l.jam_sejak_diketahui, 1)} sejak isu diketahui: ${esc(l.label)}</li>`).join('')}</ol>
      ${r.ketahanan ? `<p class="muted">Uji ketahanan: peringkat sama di ${pct(r.ketahanan.peringkat_sama)} sampel parameter, lebih baik dari tanpa tindakan di ${pct(r.ketahanan.lebih_baik_dari_A)}.</p>` : ''}
      ${r.peringatan ? `<p class="pil sedang">${esc(r.peringatan)}</p>` : ''}
      <button data-simpan="${esc(r.kode)}">Simpan rencana (untuk kartu & latihan)</button></section>`).join('');
  $$('#r-hasil [data-simpan]').forEach(b => b.onclick = async () => {
    try { const j = await post('/api/perencana/simpan', { kode: b.dataset.simpan }); b.textContent = 'Tersimpan: ' + j.berkas; b.disabled = true; } catch (e) { b.textContent = e.message; }
  });
}

/* ---------------------------------------------------------------- Latihan */
async function muatSesi() {
  const d = await api('/api/latihan');
  $('#l-sesi').innerHTML = d.sesi.map(s => `<li><span><b>${esc(s.id)}</b> · ${esc(s.skenario)} · ronde ${s.ronde}/${s.n_ronde}</span>
    <span class="aksi"><a class="tombol" href="/latihan?sesi=${encodeURIComponent(s.id)}&peran=fasilitator">Fasilitator</a>
    <a class="tombol" href="/latihan?sesi=${encodeURIComponent(s.id)}&peran=peserta">Peserta</a></span></li>`).join('') || '<li class="muted">Belum ada sesi berjalan.</li>';
}

/* ---------------------------------------------------------------- Kajian */
const JENIS_BUTIR = ['muncul', 'diketahui', 'tindakan', 'tanda_bahaya', 'mereda'];
const KANAL_TINDAKAN = ['', 'gereja_adat', 'medsos_resmi', 'radio', 'tim_keliling', 'penindakan', 'lainnya'];
function barisLini(b = {}) {
  const tr = document.createElement('tr');
  const opt = (arr, v) => arr.map(x => `<option ${x === v ? 'selected' : ''}>${x}</option>`).join('');
  tr.innerHTML = `<td><input type="number" step="0.5" min="0" value="${esc(b.jam ?? '')}" data-k="jam" style="width:80px"></td>
    <td><select data-k="jenis">${opt(JENIS_BUTIR, b.jenis)}</select></td>
    <td><input data-k="peran" value="${esc(b.peran || '')}" placeholder="peran"></td>
    <td><select data-k="kanal">${opt(KANAL_TINDAKAN, b.kanal || '')}</select></td>
    <td><input data-k="distrik" value="${esc(b.distrik || '')}"></td>
    <td><input data-k="keterangan" value="${esc(b.keterangan || '')}"></td>
    <td><select data-k="keyakinan">${opt(['tinggi', 'sedang', 'rendah'], b.keyakinan || 'sedang')}</select></td>
    <td><button type="button" title="hapus">×</button></td>`;
  tr.querySelector('button').onclick = () => tr.remove();
  return tr;
}
function kepalaLini() { $('#kj-lini').innerHTML = '<tr><th>Jam</th><th>Butir</th><th>Peran</th><th>Kanal tindakan</th><th>Distrik</th><th>Keterangan</th><th>Keyakinan</th><th></th></tr>'; }
async function muatKajian() {
  const d = await api('/api/kajian');
  $('#kj-daftar').innerHTML = '<tr><th>ID</th><th>Kabupaten</th><th>Jenis</th><th>Tanggal</th><th>Analisis</th></tr>' +
    d.daftar.map(k => `<tr class="pilih-baris" data-id="${esc(k.id)}"><td>${esc(k.id)}${k.near_miss ? ' <span class="pil sedang">near miss</span>' : ''}</td><td>${esc(k.kabupaten)}</td><td>${esc(k.jenis_isu)}</td><td>${esc(k.tanggal)}</td><td>${k.dianalisis ? 'ya' : '–'}</td></tr>`).join('');
  $$('#kj-daftar [data-id]').forEach(tr => tr.onclick = () => bukaKajian(tr.dataset.id));
  const logs = await api('/api/situasi');
  isiSelect($('#kj-situasi'), [['', 'Draf dari log Ruang Situasi…'], ...logs.map(l => [l.id, `${l.id} · ${l.kabupaten || ''}`])]);
  $('#kj-situasi').onchange = async e => {
    if (!e.target.value) return;
    try { const k = await post(`/api/kajian/dari-situasi/${encodeURIComponent(e.target.value)}`); await muatKajian(); bukaKajian(k.id); } catch (er) { alert(er.message); }
  };
}
function siapkanKajian() {
  isiSelect($('#kj-kab'), META.kabupaten.map(k => [k.id, k.nama]));
  isiSelect($('#kj-jenis'), [...META.jenis.map(j => [j.id, j.nama]), ['lainnya', 'Lainnya']]);
  isiSelect($('#kj-kanal'), META.kanal.map(k => [k.id, k.nama]));
  const isiDis = v => isiSelect($('#kj-dis'), [['', '–'], ...distrikUntuk($('#kj-kab').value).map(d => [d, d])], v);
  $('#kj-kab').onchange = () => isiDis(); isiDis();
  $('#kj-baru').onclick = () => isiFormKajian({ id: `kj-${new Date().toISOString().slice(0, 10)}-${Math.random().toString(36).slice(2, 6)}`,
    tanggal: new Date().toISOString().slice(0, 10), linimasa: [{ jam: 0, jenis: 'muncul', keyakinan: 'sedang' }] }, null);
  $('#kj-tambah').onclick = () => $('#kj-lini').append(barisLini({ jenis: 'tindakan' }));
  $('#f-kajian').onsubmit = async e => { e.preventDefault(); await simpanKajian(false); };
  $('#kj-analisis').onclick = () => simpanKajian(true);
}
async function bukaKajian(id) { const d = await api(`/api/kajian/${encodeURIComponent(id)}`); isiFormKajian(d.isi, d.analisis); }
function isiFormKajian(k, analisis) {
  const f = $('#f-kajian'); f.hidden = false; $('#kj-judul').textContent = `Kajian ${k.id}`;
  for (const n of ['id', 'tanggal', 'kabupaten', 'jenis_isu', 'kanal_awal']) if (k[n] != null) f.elements[n].value = k[n];
  $('#kj-kab').dispatchEvent(new Event('change')); if (k.distrik) $('#kj-dis').value = k.distrik;
  f.elements.ajakan_tindakan.checked = !!k.ajakan_tindakan; f.elements.near_miss.checked = !!k.near_miss;
  const h = k.hasil || {}; f.elements.eskalasi.checked = !!h.eskalasi; f.elements.hasil_distrik.value = (h.distrik || []).join(', ');
  f.elements.durasi_jam.value = h.durasi_jam ?? '';
  const p = k.pelajaran || {}; for (const n of ['berhasil', 'menghambat', 'usulan']) f.elements[n].value = (p[n] || []).join('\n');
  kepalaLini(); (k.linimasa || []).forEach(b => $('#kj-lini').append(barisLini(b)));
  f.dataset.sumber = JSON.stringify(k.sumber || []);
  tampilAnalisis(analisis, k.id);
}
function kumpulKajian() {
  const f = $('#f-kajian'), v = n => f.elements[n].value.trim();
  const lines = n => v(n).split('\n').map(s => s.trim()).filter(Boolean);
  const lini = $$('#kj-lini tr').slice(1).map(tr => {
    const b = {};
    tr.querySelectorAll('[data-k]').forEach(el => { if (el.value !== '') b[el.dataset.k] = el.dataset.k === 'jam' ? +el.value : el.value; });
    return b;
  });
  return { id: v('id'), tanggal: v('tanggal'), kabupaten: v('kabupaten'), distrik: v('distrik') || null, jenis_isu: v('jenis_isu'),
    kanal_awal: v('kanal_awal'), ajakan_tindakan: f.elements.ajakan_tindakan.checked, near_miss: f.elements.near_miss.checked,
    linimasa: lini, sumber: JSON.parse(f.dataset.sumber || '[]'),
    hasil: { eskalasi: f.elements.eskalasi.checked, distrik: v('hasil_distrik').split(',').map(s => s.trim()).filter(Boolean), durasi_jam: v('durasi_jam') ? +v('durasi_jam') : null },
    pelajaran: { berhasil: lines('berhasil'), menghambat: lines('menghambat'), usulan: lines('usulan') } };
}
async function simpanKajian(analisis) {
  const k = kumpulKajian();
  try { await post('/api/kajian', { isi: k, pengguna: $('#f-kajian').elements.pengguna.value }); }
  catch (e) { $('#kj-msg').textContent = 'Belum tersimpan: ' + e.message; return; }
  $('#kj-msg').textContent = 'Tersimpan.'; muatKajian();
  if (!analisis) return;
  try { await post(`/api/kajian/${encodeURIComponent(k.id)}/analisis`, {}); } catch (e) { $('#kj-msg').textContent = e.message; return; }
  const poll = async () => {
    const s = await api('/api/kajian-status');
    if (s.jalan) { $('#kj-msg').textContent = 'Putar ulang di model… ' + ((s.log || []).at(-1) || ''); return setTimeout(poll, 3000); }
    $('#kj-msg').textContent = s.error ? 'Gagal: ' + s.error : 'Analisis selesai.';
    if (s.hasil) tampilAnalisis(s.hasil, k.id);
  };
  poll();
}
function tampilAnalisis(a, id) {
  const box = $('#kj-hasil');
  if (!a) { box.innerHTML = ''; return; }
  const sel = t => Array.isArray(t) ? t.join(', ') : typeof t === 'object' && t ? Object.entries(t).map(([k, v]) => `${k}: ${[].concat(v).join(', ') || '–'}`).join(' · ') : t;
  box.innerHTML = `<h3>Seberapa meleset</h3><table class="tabel"><tr><th>Metrik</th><th>Kenyataan</th><th>Model</th><th>Penjelasan</th></tr>
    ${a.seberapa_meleset.map(t => `<tr><td>${esc(t.metrik)}</td><td>${esc(sel(t.kenyataan))}</td><td>${esc(sel(t.model))}${t.model_rentang ? ` <span class="muted">(${num(t.model_rentang[0], 1)}–${num(t.model_rentang[1], 1)})</span>` : ''}</td><td>${esc(t.penjelasan)}</td></tr>`).join('')}</table>
    <h3>Bagaimana jika</h3><ul>${a.bagaimana_jika.map(j => `<li>${esc(j.kalimat)}</li>`).join('') || '<li class="muted">Tidak ada tindakan yang bisa dimodelkan.</li>'}</ul>
    <p class="aksi"><a class="tombol" href="/api/kajian/${encodeURIComponent(id)}.pdf" target="_blank" rel="noopener">PDF kajian</a>
    <span class="muted">Untuk kalibrasi ulang: <code>python -m sintetis kalibrasi --kasus kajian/</code></span></p>`;
}

/* ---------------------------------------------------------------- mulai */
async function init() {
  META = await api('/api/lapangan/meta');
  $('#versi').textContent = META.versi_model.teks;
  if (META.pustaka.usang || !META.pustaka.ada) { $('#usang').hidden = false; $('#usang').textContent = META.pustaka.teks; }
  siapkanSituasi(); siapkanKartu(); siapkanPerencana(); siapkanKajian();
  $$('.tabs button').forEach(b => b.onclick = () => { history.replaceState(null, '', '#' + b.dataset.tab); tab(b.dataset.tab); });
  tab((location.hash.slice(1).split('?')[0]) || 'situasi');
}
init().catch(e => { document.body.insertAdjacentHTML('beforeend', `<p class="peringatan">Gagal memuat: ${esc(e.message)}</p>`); });
