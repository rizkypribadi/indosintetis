'use strict';
/* Dashboard Indonesia Sintetis — peta + graf beranimasi (V-01…V-11). */

const ST = ['S', 'E', 'I', 'Z', 'A'];
const ST_NAMA = { S: 'Rentan', E: 'Terpapar', I: 'Penyebar', Z: 'Skeptis', A: 'Siaga aktif' };
const KANAL = ['WhatsApp', 'Facebook', 'Tatap muka', 'Radio', 'Intervensi', 'Bersikap', 'Tanam', 'Agen kunci'];
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const cssv = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const hex = h => { h = h.replace('#', ''); return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)); };
const fmt = n => Math.round(n).toLocaleString('id-ID');
const pct = (a, b) => b ? (100 * a / b).toFixed(1) + '%' : '–';

const App = {
  meta: null, world: null, worldKey: '', sims: [], T: 0, cur: 0, playing: false, speed: 4, lastStep: 0,
  maps: [], overlays: [], pulses: [[], []], rings: [], glow: [[], []], ws: null, running: false, dtJam: 0.5,
  chart: null, events: [], selected: null, recording: null, dirty: true, ambang: 0.002,
};
let COL = {};
function readColors() { ST.forEach(s => COL[s] = hex(cssv('--' + s.toLowerCase()))); }
const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
};

/* ---------------------------------------------------------------- ikon status (V-02: warna + bentuk) */
function drawShape(ctx, s, x, y, r, color) {
  ctx.fillStyle = color; ctx.strokeStyle = color; ctx.lineWidth = r * 0.42;
  ctx.beginPath();
  if (s === 'S') { ctx.arc(x, y, r * 0.75, 0, Math.PI * 2); ctx.fill(); }
  else if (s === 'E') { ctx.moveTo(x, y - r); ctx.lineTo(x + r * 0.95, y + r * 0.75); ctx.lineTo(x - r * 0.95, y + r * 0.75); ctx.closePath(); ctx.fill(); }
  else if (s === 'I') { ctx.rect(x - r * 0.8, y - r * 0.8, r * 1.6, r * 1.6); ctx.fill(); }
  else if (s === 'Z') { ctx.arc(x, y, r * 0.72, 0, Math.PI * 2); ctx.stroke(); }
  else if (s === 'A') {
    for (let k = 0; k < 10; k++) {
      const a = -Math.PI / 2 + k * Math.PI / 5, rr = k % 2 ? r * 0.45 : r;
      k ? ctx.lineTo(x + rr * Math.cos(a), y + rr * Math.sin(a)) : ctx.moveTo(x + rr * Math.cos(a), y + rr * Math.sin(a));
    }
    ctx.closePath(); ctx.fill();
  }
}
const ATLAS = (() => {
  const c = document.createElement('canvas'); c.width = 64 * 5; c.height = 64;
  const ctx = c.getContext('2d'); const mapping = {};
  ST.forEach((s, i) => { drawShape(ctx, s, 32 + i * 64, 32, 26, '#fff'); mapping[s] = { x: i * 64, y: 0, width: 64, height: 64, mask: true }; });
  return { url: c.toDataURL(), mapping };
})();

function renderLegend() {
  const el = $('#legend'); el.innerHTML = '';
  ST.forEach(s => {
    const row = document.createElement('div'); row.className = 'row';
    const c = document.createElement('canvas'); c.width = 28; c.height = 28;
    drawShape(c.getContext('2d'), s, 14, 14, 11, cssv('--' + s.toLowerCase()));
    row.append(c, `${s} · ${ST_NAMA[s]}`); el.append(row);
  });
  const sub = document.createElement('div'); sub.className = 'sub';
  sub.innerHTML = `Sinyal: <span class="sig-key" style="background:#38bdf8"></span>4G <span class="sig-key" style="background:#e6a700"></span>lemah <span class="sig-key" style="background:#8a93a8"></span>tidak ada<br>
  Garis: WA padat · tatap muka bertitik · FB tipis · viral putus-putus`;
  el.append(sub);
}

/* ---------------------------------------------------------------- peta */
const ESRI = p => [`https://server.arcgisonline.com/ArcGIS/rest/services/${p}/MapServer/tile/{z}/{y}/{x}`];
const DEM = { type: 'raster-dem', tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
  encoding: 'terrarium', tileSize: 256, maxzoom: 14, attribution: 'Elevasi: AWS Terrain Tiles (SRTM, GMTED)' };
const baseKind = () => ($('#sel-base') && $('#sel-base').value) || 'satelit';
const onImagery = () => baseKind() === 'satelit';

/* Sumber & lapisan peta dasar. Semua id berawalan "base-" agar bisa diganti tanpa mengusik lapisan deck.gl. */
function baseSpec(kind, dark) {
  const src = {}, lay = [];
  if (kind === 'satelit') {
    src['base-img'] = { type: 'raster', tiles: ESRI('World_Imagery'), tileSize: 256, maxzoom: 18,
      attribution: 'Citra © Esri, Maxar, Earthstar Geographics, komunitas pengguna GIS' };
    src['base-dem'] = DEM;
    lay.push({ id: 'base-img', type: 'raster', source: 'base-img', paint: { 'raster-saturation': -0.1, 'raster-contrast': 0.05, 'raster-brightness-max': dark ? 0.85 : 1 } });
    lay.push({ id: 'base-relief', type: 'hillshade', source: 'base-dem',
      paint: { 'hillshade-exaggeration': 0.45, 'hillshade-shadow-color': '#0b1020', 'hillshade-highlight-color': '#ffffff', 'hillshade-accent-color': '#1b2a1b' } });
  } else if (kind === 'topo') {
    src['base-img'] = { type: 'raster', tiles: ESRI('World_Topo_Map'), tileSize: 256, maxzoom: 18,
      attribution: 'Peta © Esri, HERE, Garmin, FAO, NOAA, USGS, © OpenStreetMap' };
    lay.push({ id: 'base-img', type: 'raster', source: 'base-img', paint: { 'raster-brightness-max': dark ? 0.75 : 1 } });
  } else {
    src['base-img'] = { type: 'raster', tiles: ESRI(`Canvas/World_${dark ? 'Dark' : 'Light'}_Gray_Base`), tileSize: 256, maxzoom: 16,
      attribution: 'Peta dasar © Esri, HERE, Garmin, © OpenStreetMap' };
    lay.push({ id: 'base-img', type: 'raster', source: 'base-img', paint: { 'raster-opacity': dark ? 0.8 : 0.85 } });
  }
  if (kind !== 'topo' && ($('#ly-label') ? $('#ly-label').checked : true)) {
    // Satelit: label terang di atas citra. Kanvas/topo: label bawaan Esri.
    src['base-label'] = { type: 'raster', tiles: ESRI(kind === 'kanvas' ? `Canvas/World_${dark ? 'Dark' : 'Light'}_Gray_Reference` : 'Reference/World_Boundaries_and_Places'),
      tileSize: 256, maxzoom: 16 };
    lay.push({ id: 'base-label', type: 'raster', source: 'base-label', paint: { 'raster-opacity': 0.9 } });
  }
  return { sources: src, layers: lay };
}

function baseStyle() {
  const dark = isDark(), s = baseSpec(baseKind(), dark);
  return {
    version: 8, sources: s.sources,
    layers: [{ id: 'bg', type: 'background', paint: { 'background-color': baseKind() === 'satelit' ? '#0b1a2b' : dark ? '#070a14' : '#dfe5ee' } }, ...s.layers],
  };
}

function ensureMaps(count) {
  const stage = $('#stage');
  while (App.maps.length < count) {
    const m = App.maps.length;
    const wrap = document.createElement('div'); wrap.className = 'mapwrap';
    wrap.innerHTML = `<div class="map" id="map${m}"></div><div class="maptag" id="tag${m}"></div>`;
    stage.insertBefore(wrap, $('#layers'));
    const map = new maplibregl.Map({
      container: `map${m}`, style: baseStyle(), center: [138.3, -2.0], zoom: 6.2, minZoom: 5, maxZoom: 13,
      preserveDrawingBuffer: true, attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    const ov = new deck.MapboxOverlay({ interleaved: true, layers: [], getTooltip: tooltip, onClick: info => onPick(info, m) });
    map.addControl(ov);
    map.on('move', () => { syncMaps(m); App.dirty = true; });
    App.maps.push(map); App.overlays.push(ov);
  }
  App.maps.forEach((map, i) => {
    map.getContainer().parentElement.style.display = i < count ? '' : 'none'; map.resize();
    if (App.bounds) map.fitBounds(App.bounds, { duration: 0, padding: 30 });
  });
}
let syncing = false;
function syncMaps(src) {
  if (syncing || App.sims.length < 2) return;
  syncing = true;
  const a = App.maps[src];
  App.maps.forEach((b, i) => { if (i !== src && b.getContainer().parentElement.style.display !== 'none') b.jumpTo({ center: a.getCenter(), zoom: a.getZoom() }); });
  syncing = false;
}
function swapBasemap() {
  const st = baseStyle();
  App.maps.forEach(map => {
    if (!map.isStyleLoaded() && !map.getLayer('bg')) return;
    const layers = map.getStyle().layers;
    layers.filter(l => l.id.startsWith('base-')).forEach(l => map.removeLayer(l.id));
    Object.keys(map.getStyle().sources).filter(s => s.startsWith('base-')).forEach(s => map.removeSource(s));
    const first = map.getStyle().layers.find(l => l.id !== 'bg');      // lapisan deck.gl pertama
    Object.entries(st.sources).forEach(([k, v]) => map.addSource(k, v));
    st.layers.slice(1).forEach(l => map.addLayer(l, first ? first.id : undefined));
    map.setPaintProperty('bg', 'background-color', st.layers[0].paint['background-color']);
  });
  App.dirty = true;
}

/* ---------------------------------------------------------------- dunia */
async function loadWorld(n, seed = 42) {
  const key = `${n}:${seed}`;
  if (App.worldKey === key) return;
  status(`Membangun dunia ${fmt(n)} agen…`);
  const w = await (await fetch(`/api/dunia?agen=${n}&seed=${seed}`)).json();
  const V = w.kampung.lonlat.length;
  w.kampungKab = w.kampung.distrik.map(d => w.distrik[d].kab);
  w.sampleByKampung = Array.from({ length: V }, () => []);
  w.sampel.kampung.forEach((k, i) => w.sampleByKampung[k].push(i));
  w.sampleIdx = w.sampel.pos.map((_, i) => i);
  w.keySample = w.sampleIdx.filter(i => w.sampel.kunci[i] >= 0);
  w.kampungData = w.kampung.lonlat.map((p, i) => ({ i, pos: p, sinyal: w.kampung.sinyal[i], pop: w.kampung.penduduk[i] }));
  w.distrik.forEach((d, i) => d.i = i);
  // Poligon distrik berupa multipoligon [[cincin luar, lubang…], …]: pecah per bagian untuk PolygonLayer.
  w.distrikParts = w.distrik.flatMap(d => d.poligon.map(rings => ({ d, rings })));
  w.kabPaths = (w.batas_kabupaten || []).flatMap(k => k.poligon.map(rings => ({ nama: k.nama, path: rings[0] })));
  App.world = w; App.worldKey = key;
  const xs = w.kampung.lonlat.map(p => p[0]), ys = w.kampung.lonlat.map(p => p[1]);
  App.bounds = [[Math.min(...xs) - 0.2, Math.min(...ys) - 0.2], [Math.max(...xs) + 0.2, Math.max(...ys) + 0.2]];
  App.maps.forEach(m => m.fitBounds(App.bounds, { duration: 0, padding: 30 }));
  renderWorldInfo(); status(`Dunia siap: ${fmt(n)} agen, ${w.distrik.length} distrik, ${V} kampung (${w.sumber_geografi}).`);
  App.dirty = true;
}

function renderWorldInfo() {
  const w = App.world, r = w.ringkasan;
  $('#pane-info').innerHTML = `
    <div class="kv"><b>Agen</b><span>${fmt(r.agen)}</span><b>Kelompok</b><span>${fmt(r.kelompok)}</span>
    <b>Keanggotaan</b><span>${fmt(r.keanggotaan)}</span><b>Agen kunci</b><span>${r.agen_kunci}</span>
    <b>Geografi</b><span>${r.sumber_geografi}</span></div>
    <h4>Kesiapan</h4>
    ${(App.meta.kesiapan || []).map(k => `<div class="siap ${k.status}"><b>${esc(k.butir)}</b> <span class="tag">${k.status}</span><br><span class="muted">${esc(k.keterangan)}</span></div>`).join('')}
    <table class="t"><tr><th>Kabupaten/Kota</th><th>Agen</th><th>4G</th><th>WA</th><th>Kunci</th></tr>
    ${r.kabupaten.map(k => `<tr><td>${k.kabupaten}</td><td>${fmt(k.agen)}</td><td>${Math.round(k.kampung_4g * 100)}%</td><td>${Math.round(k.pakai_wa * 100)}%</td><td>${k.agen_kunci}</td></tr>`).join('')}</table>`;
}

/* ---------------------------------------------------------------- simulasi */
function newSim(kode, nama) { return { kode, nama, frames: [], status: [], kumulatif: [] }; }

function pushFrame(sim, fr) {
  let st;
  if (fr.sampel_penuh) st = Int8Array.from(fr.sampel_penuh);
  else {
    st = Int8Array.from(sim.status[sim.status.length - 1]);
    for (const [i, s] of fr.sampel_ubah) st[i] = s;
  }
  delete fr.sampel_penuh; delete fr.sampel_ubah;
  sim.frames.push(fr); sim.status.push(st);
  for (const e of fr.events) addEvent(e, sim.kode);
}

async function run() {
  if (App.running) { App.ws && App.ws.send(JSON.stringify({ stop: true })); return; }
  const n = +$('#sel-n').value;
  await loadWorld(n);
  const req = {
    agen: n, seed_dunia: 42, seed: +$('#in-seed').value, skenario: $('#sel-sk').value,
    intervensi: $('#sel-iv').value, banding: $('#sel-cmp').value || null, llm: $('#sel-llm').value,
    simpan: $('#chk-save').checked,
  };
  resetRun(req.banding ? 2 : 1);
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/simulasi`);
  App.ws = ws; App.running = true; $('#btn-run .lbl').textContent = 'Hentikan simulasi'; $('#btn-run').classList.add('danger');
  ws.onopen = () => ws.send(JSON.stringify(req));
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.error) toast(msg.error);
    if (msg.mulai) {
      App.T = msg.T; App.dtJam = msg.dt_jam;
      App.sims = msg.kode.map(k => newSim(k, msg.nama_intervensi[k]));
      $('#scrub').max = msg.T; initChart(); App.playing = true; setPlay(true);
    }
    if (msg.frame) {
      msg.frame.forEach((fr, i) => pushFrame(App.sims[i], fr));
      updateChart(); status(`Menghitung… langkah ${App.sims[0].frames.length - 1}/${App.T}`);
      if (App.sims[0].frames.length === 1) enterFrame(0, false);
    }
    if (msg.selesai) {
      App.result = msg.hasil;
      const h = msg.hasil.map(r => `${r.kode}: pernah dengar ${(r.pernah_dengar * 100).toFixed(1)}%, distrik eskalasi ${r.distrik_eskalasi}, keputusan agen kunci ${JSON.stringify(r.manifest.llm.hitung)}${r.folder ? ', log ' + r.folder : ''}`).join(' · ');
      status(`Selesai. ${h}`); refreshReplays();
    }
  };
  ws.onclose = () => { App.running = false; $('#btn-run .lbl').textContent = 'Jalankan simulasi'; $('#btn-run').classList.remove('danger'); };
  ws.onerror = () => toast('Koneksi WebSocket gagal');
}

function resetRun(count) {
  App.sims = []; App.cur = 0; App.events = []; App.pulses = [[], []]; App.glow = [[], []]; App.rings = [];
  $('#pane-feed').innerHTML = '';
  ensureMaps(count); App.dirty = true;
}

/* ---------------------------------------------------------------- agregasi & lapisan */
function level(m) {
  if (!$('#ly-agg').checked) return 'agen';
  const z = App.maps[m].getZoom();
  return z < 7 ? 'kab' : z < 8.3 ? 'distrik' : z < 9.6 ? 'kampung' : 'agen';
}

function aggregate(fr, lvl) {
  const w = App.world, K = fr.kampung, V = w.kampung.lonlat.length;
  let keyOf, units;
  if (lvl === 'kab') { keyOf = i => w.kampungKab[i]; units = w.kabupaten.map((k, i) => ({ i, nama: k.nama, pos: k.pusat })); }
  else if (lvl === 'distrik') { keyOf = i => w.kampung.distrik[i]; units = w.distrik.map((d, i) => ({ i, nama: d.nama, pos: d.pusat })); }
  else { keyOf = i => i; units = w.kampung.lonlat.map((p, i) => ({ i, nama: w.kampung.nama[i], pos: p })); }
  units.forEach(u => { u.c = [0, 0, 0, 0, 0]; u.pop = 0; });
  for (let v = 0; v < V; v++) {
    const u = units[keyOf(v)]; const p = w.kampung.penduduk[v];
    u.pop += p; u.c[1] += K[0][v]; u.c[2] += K[1][v]; u.c[3] += K[2][v]; u.c[4] += K[3][v];
  }
  units.forEach(u => { u.c[0] = u.pop - u.c[1] - u.c[2] - u.c[3] - u.c[4]; });
  return units.filter(u => u.pop > 0);
}

function blend(c) {
  const wts = [0.12, 1, 1.4, 1, 2.2]; let tot = 0; const out = [0, 0, 0];
  c.forEach((n, s) => { const ww = n * wts[s]; tot += ww; COL[ST[s]].forEach((x, j) => out[j] += x * ww); });
  return tot ? out.map(x => x / tot) : COL.S;
}

function riskColor(r, zoom) {
  const x = r / App.ambang, k = zoom > 8.3 ? 0.45 : 1;   // lebih transparan saat melihat agen
  if (x < 0.05) return [0, 0, 0, 0];
  if (x < 1) return [230, 167, 0, (25 + 70 * x) * k];
  if (x < 3) return [229, 72, 77, (95 + 15 * (x - 1)) * k];
  return [168, 85, 247, 130 * k];
}

function buildLayers(m, now) {
  const w = App.world, sim = App.sims[m];
  if (!w) return [];
  const layers = [];
  const t = Math.min(App.cur, sim ? sim.frames.length - 1 : 0);
  const fr = sim && sim.frames[t];
  const zoom = App.maps[m].getZoom();

  if ($('#ly-risk').checked && fr) {
    const lc = onImagery() ? [255, 255, 255, 90] : isDark() ? [140, 150, 180, 70] : [80, 90, 120, 70];
    layers.push(new deck.PolygonLayer({
      id: `risk${m}`, data: w.distrikParts, getPolygon: p => p.rings, pickable: true,
      getFillColor: p => riskColor(fr.risiko[p.d.i], zoom), getLineColor: lc,
      lineWidthMinPixels: 0.5, updateTriggers: { getFillColor: [t, m, zoom > 8.3], getLineColor: [isDark(), baseKind()] },
    }));
  }
  if ($('#ly-batas').checked && w.kabPaths.length) {
    layers.push(new deck.PathLayer({
      id: `batas${m}`, data: w.kabPaths, getPath: k => k.path, widthUnits: 'pixels', getWidth: 1.4,
      getColor: onImagery() ? [255, 236, 170, 200] : isDark() ? [200, 210, 235, 150] : [40, 50, 80, 150],
      updateTriggers: { getColor: [isDark(), baseKind()] },
    }));
  }
  if ($('#ly-sig').checked) {
    layers.push(new deck.ScatterplotLayer({
      id: `sig${m}`, data: w.kampungData, getPosition: d => d.pos, getRadius: 3200, radiusMinPixels: 3,
      getFillColor: d => d.sinyal >= 1 ? [56, 189, 248, 120] : d.sinyal > 0 ? [230, 167, 0, 130] : [138, 147, 168, 130],
      pickable: true,
    }));
  }
  if (!fr) return layers;
  const status = sim.status[t];
  const lvl = level(m);
  if (lvl !== 'agen') {
    const units = aggregate(fr, lvl);
    const k = lvl === 'kab' ? 55 : lvl === 'distrik' ? 40 : 28;
    layers.push(new deck.ScatterplotLayer({
      id: `agg${m}`, data: units, getPosition: u => u.pos, getRadius: u => Math.sqrt(u.pop) * k, radiusMinPixels: 3,
      getFillColor: u => [...blend(u.c), 210], stroked: true, getLineColor: isDark() ? [255, 255, 255, 60] : [20, 26, 43, 60],
      lineWidthMinPixels: 1, pickable: true, updateTriggers: { getFillColor: [t, m, isDark()] },
    }));
    layers.push(new deck.ScatterplotLayer({
      id: `aggA${m}`, data: units.filter(u => u.c[4] > 0), getPosition: u => u.pos,
      getRadius: u => Math.sqrt(u.c[4]) * k * 1.6, radiusMinPixels: 2, getFillColor: [...COL.A, 235],
      updateTriggers: { getRadius: [t, m] },
    }));
  } else {
    const px = Math.max(3, Math.min(14, (zoom - 6) * 2.4));
    layers.push(new deck.IconLayer({
      id: `agen${m}`, data: w.sampleIdx, getPosition: i => w.sampel.pos[i],
      iconAtlas: ATLAS.url, iconMapping: ATLAS.mapping, getIcon: i => ST[status[i]],
      getColor: i => { const s = status[i]; return [...COL[ST[s]], s === 0 ? 150 : 255]; },
      getSize: i => w.sampel.kunci[i] >= 0 ? 1.9 : (status[i] === 0 ? 0.8 : 1), sizeScale: px, sizeUnits: 'pixels',
      pickable: true, updateTriggers: { getIcon: [t, m], getColor: [t, m, isDark()], getSize: [t, m] },
    }));
    layers.push(new deck.ScatterplotLayer({
      id: `kunci${m}`, data: w.keySample, getPosition: i => w.sampel.pos[i], stroked: true, filled: false,
      getRadius: 10, radiusMinPixels: px * 1.3, getLineColor: isDark() ? [255, 255, 255, 200] : [20, 26, 43, 200],
      lineWidthMinPixels: 1.2, pickable: true,
    }));
    if (!reduceMotion) {
      const g = App.glow[m].filter(x => now - x.t0 < 900);
      if (g.length) layers.push(new deck.ScatterplotLayer({
        id: `glow${m}`, data: g, getPosition: x => w.sampel.pos[x.i],
        getRadius: x => 300 + 1200 * (now - x.t0) / 900, radiusMinPixels: px * 0.8,
        getFillColor: x => [...COL[ST[x.s]], 160 * (1 - (now - x.t0) / 900)],
        updateTriggers: { getRadius: now, getFillColor: now },
      }));
    }
  }
  if ($('#ly-edge').checked) {
    const P = App.pulses[m].filter(p => now - p.t0 < p.dur + 500);
    if (P.length) {
      const dash = deck.PathStyleExtension ? [new deck.PathStyleExtension({ dash: true })] : [];
      layers.push(new deck.PathLayer({
        id: `edge${m}`, data: P, getPath: p => [p.from, p.to], widthUnits: 'pixels',
        getWidth: p => p.kanal === 0 ? 2 : p.kanal === 1 ? 1 : 1.6,
        getColor: p => { const a = Math.max(0, 1 - (now - p.t0) / (p.dur + 500)); return [...(p.jenis ? COL.Z : COL.I), 200 * a]; },
        getDashArray: p => p.kanal === 2 ? [1, 2.5] : p.viral ? [6, 4] : [0, 0], dashJustified: true,
        extensions: dash, updateTriggers: { getColor: now },
      }));
      if (!reduceMotion) layers.push(new deck.ScatterplotLayer({
        id: `pulse${m}`, data: P.filter(p => now - p.t0 < p.dur), radiusMinPixels: 3, getRadius: 400,
        getPosition: p => { const k = (now - p.t0) / p.dur; return [p.from[0] + (p.to[0] - p.from[0]) * k, p.from[1] + (p.to[1] - p.from[1]) * k]; },
        getFillColor: p => [...(p.jenis ? COL.Z : COL.I), 255], updateTriggers: { getPosition: now },
      }));
    }
  }
  if ($('#ly-radio').checked && fr.radio && fr.radio.length) {
    const st = w.radio.filter(s => fr.radio.includes(s.id));
    const R = reduceMotion ? st.map(s => ({ pos: s.pos, age: 0.6 })) :
      App.rings.filter(r => r.m === m && now - r.t0 < 2400).map(r => ({ pos: r.pos, age: (now - r.t0) / 2400 }));
    layers.push(new deck.ScatterplotLayer({
      id: `radio${m}`, data: R, getPosition: r => r.pos, getRadius: r => 8000 + r.age * 90000, stroked: true, filled: false,
      getLineColor: r => [...COL.Z, 220 * (1 - r.age)], lineWidthMinPixels: 2, updateTriggers: { getRadius: now, getLineColor: now },
    }));
  }
  return layers;
}

function tooltip({ object, layer }) {
  if (!object || !layer) return null;
  const w = App.world, id = layer.id.replace(/\d+$/, '');
  if (id === 'agen' || id === 'kunci') {
    const i = object, ki = w.sampel.kunci[i], m = +layer.id.slice(-1), sim = App.sims[m];
    const st = sim ? ST[sim.status[Math.min(App.cur, sim.frames.length - 1)][i]] : 'S';
    const p = ki >= 0 ? w.persona[ki] : null;
    return { text: p ? `${p.nama} — ${p.peran}\n${p.wilayah}\nStatus: ${ST_NAMA[st]}` :
      `Agen sintetis · ${w.sampel.umur[i]} th · ${w.kampung.nama[w.sampel.kampung[i]]}\nStatus: ${ST_NAMA[st]}` };
  }
  if (id === 'agg') {
    const c = object.c;
    return { text: `${object.nama}\nPenduduk (agen): ${fmt(object.pop)}\nE ${fmt(c[1])} · I ${fmt(c[2])} · Z ${fmt(c[3])} · A ${fmt(c[4])}` };
  }
  if (id === 'risk') {
    const m = +layer.id.slice(-1), sim = App.sims[m], fr = sim && sim.frames[Math.min(App.cur, sim.frames.length - 1)];
    const d = object.d || object;
    return { text: `Distrik ${d.nama} (${d.tipe}) · ${fmt(d.penduduk)} agen\nIndeks risiko: ${fr ? (fr.risiko[d.i] * 1000).toFixed(2) : '–'} ‰` };
  }
  if (id === 'batas') return { text: object.nama };
  if (id === 'sig') return { text: `${w.kampung.nama[object.i]}\nSinyal: ${object.sinyal >= 1 ? '4G' : object.sinyal > 0 ? 'lemah' : 'tidak ada'}` };
  return null;
}

function onPick(info, m) {
  if (!info.object || !info.layer) return;
  const id = info.layer.id.replace(/\d+$/, '');
  if (id === 'agen' || id === 'kunci') {
    const ki = App.world.sampel.kunci[info.object];
    if (ki >= 0) showKeyAgent(ki); else showAgent(info.object, m);
  }
}

/* ---------------------------------------------------------------- linimasa */
function enterFrame(t, animate) {
  App.cur = t; $('#scrub').value = t;
  const w = App.world, now = performance.now();
  App.sims.forEach((sim, m) => {
    const fr = sim.frames[t]; if (!fr) return;
    const tot = fr.jumlah.reduce((a, b) => a + b, 0);
    $(`#tag${m}`).innerHTML = `${sim.kode} · ${sim.nama}<span class="waktu">${fr.waktu}</span>
      <span class="stats">E ${pct(fr.jumlah[1], tot)} · I ${pct(fr.jumlah[2], tot)} · Z ${pct(fr.jumlah[3], tot)} · A ${pct(fr.jumlah[4], tot)}</span>`;
    if (!animate) return;
    for (const a of fr.arcs) {
      const sa = w.sampleByKampung[a[0]], sb = w.sampleByKampung[a[1]];
      const from = sa.length ? w.sampel.pos[sa[(Math.random() * sa.length) | 0]] : w.kampung.lonlat[a[0]];
      const to = sb.length ? w.sampel.pos[sb[(Math.random() * sb.length) | 0]] : w.kampung.lonlat[a[1]];
      const dist = Math.hypot(to[0] - from[0], to[1] - from[1]);
      App.pulses[m].push({ from, to, t0: now + Math.random() * 250, dur: 600 + Math.min(1600, dist * 900), jenis: a[2], kanal: a[3],
        viral: a[3] === 1 && w.kampungKab[a[0]] !== w.kampungKab[a[1]] });
    }
    if (fr.radio && fr.radio.length && !reduceMotion) {
      w.radio.filter(s => fr.radio.includes(s.id)).forEach(s => App.rings.push({ m, pos: s.pos, t0: now }));
    }
    if (t > 0 && !reduceMotion) {
      const a = sim.status[t - 1], b = sim.status[t];
      let k = 0;
      for (let i = 0; i < b.length && k < 3000; i++) if (a[i] !== b[i]) { App.glow[m].push({ i, s: b[i], t0: now }); k++; }
    }
  });
  markChart(); App.dirty = true;
  highlightFeed(t);
}

function loop(now) {
  if (App.playing && App.sims.length) {
    const avail = Math.min(...App.sims.map(s => s.frames.length)) - 1;
    if (now - App.lastStep > 1000 / App.speed && App.cur < avail) {
      App.lastStep = now; enterFrame(App.cur + 1, true);
    } else if (App.cur >= App.T && !App.running) {
      App.playing = false; setPlay(false);
      if (App.recording) setTimeout(stopRecording, 800);
    }
  }
  for (let m = 0; m < 2; m++) {
    App.pulses[m] = App.pulses[m].filter(p => now - p.t0 < p.dur + 500);
    App.glow[m] = App.glow[m].filter(g => now - g.t0 < 900);
  }
  App.rings = App.rings.filter(r => now - r.t0 < 2400);
  const animating = App.pulses.some(p => p.length) || App.glow.some(g => g.length) || App.rings.length;
  if (App.dirty || animating) {
    App.overlays.forEach((ov, m) => { if (m < Math.max(1, App.sims.length)) ov.setProps({ layers: buildLayers(m, now) }); });
    App.dirty = false;
  }
  if (App.recording) drawComposite(App.recording.canvas, 1);
  requestAnimationFrame(loop);
}

/* ---------------------------------------------------------------- grafik SEIZA (V-07) */
function initChart() {
  if (!App.chart) {
    App.chart = echarts.init($('#chart'));
    App.chart.getZr().on('click', e => {
      const p = App.chart.convertFromPixel({ seriesIndex: 0 }, [e.offsetX, e.offsetY]);
      if (p) seek(Math.round(p[0] / App.dtJam));
    });
    addEventListener('resize', () => App.chart.resize());
  }
  updateChart();
}
function chartOption() {
  const ink = cssv('--muted'), line = cssv('--line');
  const series = [];
  App.sims.forEach((sim, m) => {
    ST.forEach((s, k) => {
      series.push({
        name: App.sims.length > 1 ? `${s} (${sim.kode})` : s, type: 'line', showSymbol: false, smooth: true,
        yAxisIndex: s === 'S' || s === 'Z' ? 1 : 0,
        lineStyle: { width: s === 'A' ? 2.5 : 1.8, type: m ? 'dashed' : 'solid', color: cssv('--' + s.toLowerCase()) },
        itemStyle: { color: cssv('--' + s.toLowerCase()) },
        data: sim.frames.map(f => { const tot = f.jumlah.reduce((a, b) => a + b, 0); return [f.t * App.dtJam, +(100 * f.jumlah[k] / tot).toFixed(3)]; }),
      });
    });
  });
  const selected = {}; series.forEach(s => { if (s.name.startsWith('S')) selected[s.name] = false; });
  return {
    animation: false, grid: { left: 52, right: 52, top: 34, bottom: 24 }, textStyle: chartTheme().textStyle,
    legend: { top: 0, textStyle: { color: ink, fontSize: 12 }, selected, itemWidth: 14, itemHeight: 8, itemGap: 16, icon: 'roundRect' },
    tooltip: { ...chartTheme().tooltip, trigger: 'axis', valueFormatter: v => v + '%' },
    xAxis: { type: 'value', min: 0, max: App.T * App.dtJam, axisLabel: { color: ink, formatter: v => `T+${v}j` }, splitLine: { lineStyle: { color: line, type: [3, 4] } }, axisLine: { lineStyle: { color: line } }, axisTick: { show: false } },
    yAxis: [
      { type: 'value', name: 'E · I · A', nameTextStyle: { color: ink }, axisLabel: { color: ink, formatter: v => v + '%' }, splitLine: { lineStyle: { color: line } } },
      { type: 'value', name: 'S · Z', nameTextStyle: { color: ink }, max: 100, axisLabel: { color: ink, formatter: v => v + '%' }, splitLine: { show: false } },
    ],
    series,
  };
}
function updateChart() { if (App.chart) { App.chart.setOption(chartOption(), { replaceMerge: ['series'] }); markChart(); } }
function markChart() {
  if (!App.chart || !App.sims.length) return;
  App.chart.setOption({ series: [{ markLine: { symbol: 'none', silent: true, label: { show: false }, lineStyle: { color: cssv('--ink'), width: 1 }, data: [{ xAxis: App.cur * App.dtJam }] } }] });
}
function seek(t) {
  if (!App.sims.length) return;
  const avail = Math.min(...App.sims.map(s => s.frames.length)) - 1;
  enterFrame(Math.max(0, Math.min(avail, t)), false);
}

/* ---------------------------------------------------------------- feed & panel agen (V-09) */
function addEvent(e, kode) {
  App.events.push({ ...e, kode });
  const el = document.createElement('div'); el.className = `ev ${e.jenis}`; el.dataset.t = e.t;
  if (e.ki !== undefined) el.dataset.ki = e.ki;
  if (e.jenis === 'pesan_provokator') {
    const tg = e.serangan ? 'serang klarifikasi' : (e.jenis_aktor === 'lokal' ? 'provokator' : 'buzzer');
    el.innerHTML = `<div class="w">${App.sims.length > 1 ? `[${kode}] ` : ''}${esc(e.waktu)}</div>
      <span class="tag">${esc(tg)}</span><b>${esc(e.aktor)}</b> <span class="muted">· ${esc(e.kanal)}</span>
      <div class="bubble">“${esc(e.teks)}”</div><div class="ilus">contoh ilustratif, tidak memengaruhi perhitungan</div>`;
    $('#pane-feed').prepend(el);
    return;
  }
  const tag = { agen_kunci: e.sumber || 'agen', intervensi: 'intervensi', tanam: 'tanam' }[e.jenis] || e.jenis;
  const pesan = e.keputusan && e.keputusan.pesan_tambahan ? `<div class="msg">“${esc(e.keputusan.pesan_tambahan)}”</div>` : '';
  el.innerHTML = `<div class="w">${App.sims.length > 1 ? `[${kode}] ` : ''}${esc(e.waktu)}</div><span class="tag">${esc(tag)}</span> ${esc(e.teks)}${pesan}`;
  if (e.ki !== undefined) el.onclick = () => showKeyAgent(e.ki);
  $('#pane-feed').prepend(el);
}
function highlightFeed(t) { $$('#pane-feed .ev').forEach(el => el.style.opacity = +el.dataset.t <= t ? 1 : 0.4); }
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function showKeyAgent(ki) {
  const p = App.world.persona[ki];
  const decs = App.events.filter(e => e.ki === ki);
  $('#pane-agen').innerHTML = `<h3 style="margin:0 0 4px">${esc(p.nama)}</h3>
    <div class="kv"><b>Peran</b><span>${esc(p.peran)}</span><b>Wilayah</b><span>${esc(p.wilayah)}</span><b>Usia</b><span>${p.usia}</span>
    <b>Jangkauan</b><span>${esc(p.jangkauan)}</span><b>Sinyal</b><span>${esc(p.sinyal)}</span><b>Nilai</b><span>${p.nilai.map(esc).join('; ')}</span>
    <b>Ditinjau penutur asli</b><span>${p.ditinjau ? 'ya' : 'belum'}</span></div>
    <h4>Keputusan (${decs.length})</h4>
    ${decs.map(d => `<div class="ev"><div class="w">${App.sims.length > 1 ? `[${d.kode}] ` : ''}${esc(d.waktu)} · sumber: ${esc(d.sumber)}${d.error ? ' · ' + esc(d.error) : ''}</div>
      <pre>${esc(d.raw || JSON.stringify(d.keputusan, null, 1))}</pre></div>`).join('') || '<p class="muted">Belum ada keputusan.</p>'}
    <p class="muted">Persona fiktif. Jawaban LLM ditampilkan mentah untuk ditinjau.</p>`;
  tab('agen');
}
function showAgent(i, m) {
  const w = App.world, sim = App.sims[m];
  const hist = sim ? sim.status.map((s, t) => [t, s[i]]).filter(([t], k, arr) => t === 0 || arr[k - 1][1] !== arr[k][1]) : [];
  $('#pane-agen').innerHTML = `<h3 style="margin:0 0 4px">Agen sintetis #${i}</h3>
    <div class="kv"><b>Kampung</b><span>${esc(w.kampung.nama[w.sampel.kampung[i]])}</span><b>Umur</b><span>${w.sampel.umur[i]}</span>
    <b>Pakai WA</b><span>${w.sampel.hp[i] ? 'ya' : 'tidak'}</span></div>
    <h4>Riwayat status</h4>${hist.map(([t, s]) => `<div>${esc(sim.frames[t].waktu)} → ${ST_NAMA[ST[s]]}</div>`).join('')}
    <p class="muted">Agen fiktif dari sampel visual; analisis hanya disajikan agregat.</p>`;
  tab('agen');
}
function tab(name) {
  $$('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  $$('.pane').forEach(p => p.classList.toggle('on', p.id === `pane-${name}`));
}

/* ---------------------------------------------------------------- ekspor (V-10) */
function drawComposite(canvas, scale) {
  const cs = App.maps.filter(m => m.getContainer().parentElement.style.display !== 'none').map(m => m.getCanvas());
  const W = cs.reduce((a, c) => a + c.width, 0), H = cs[0].height, head = 54 * scale * devicePixelRatio;
  if (canvas.width !== W || canvas.height !== H + head) { canvas.width = W; canvas.height = H + head; }
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = cssv('--surface'); ctx.fillRect(0, 0, canvas.width, canvas.height);
  let x = 0; cs.forEach(c => { ctx.drawImage(c, x, head); x += c.width; });
  const f = 14 * scale * devicePixelRatio;
  ctx.fillStyle = cssv('--ink'); ctx.font = `600 ${f * 1.15}px system-ui, sans-serif`;
  ctx.fillText('Indonesia Sintetis · Provinsi Papua', f, f * 1.5);
  ctx.font = `${f}px system-ui, sans-serif`; ctx.fillStyle = cssv('--muted');
  const fr = App.sims[0] && App.sims[0].frames[App.cur];
  const lab = App.sims.map(s => `${s.kode}: ${s.nama}`).join('  |  ');
  ctx.fillText(`${fr ? fr.waktu : ''}   ${lab}`, f, f * 3);
  let lx = canvas.width - f * 30;
  ST.forEach(s => { drawShape(ctx, s, lx, f * 1.1, f * 0.5, cssv('--' + s.toLowerCase())); ctx.fillStyle = cssv('--ink'); ctx.fillText(ST_NAMA[s], lx + f * 0.8, f * 1.45); lx += f * 6; });
}
async function exportPNG() {
  const scale = 2, prev = App.maps.map(m => m.getPixelRatio());
  try {
    App.maps.forEach(m => m.setPixelRatio(devicePixelRatio * scale));
    App.dirty = true;
    await new Promise(r => setTimeout(r, 700));
  } catch (e) { /* setPixelRatio tidak tersedia: pakai resolusi layar */ }
  const c = document.createElement('canvas'); drawComposite(c, scale);
  App.maps.forEach((m, i) => { try { m.setPixelRatio(prev[i]); } catch (e) {} });
  App.dirty = true;
  download(c.toDataURL('image/png'), `sintetis_${App.sims.map(s => s.kode).join('-')}_t${App.cur}.png`);
}
function startRecording() {
  if (!App.sims.length) return toast('Jalankan simulasi dulu');
  if (App.recording) return stopRecording();
  const canvas = document.createElement('canvas'); drawComposite(canvas, 1);
  const stream = canvas.captureStream(30);
  const mime = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find(t => MediaRecorder.isTypeSupported(t));
  const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 8e6 }); const chunks = [];
  rec.ondataavailable = e => chunks.push(e.data);
  rec.onstop = () => download(URL.createObjectURL(new Blob(chunks, { type: 'video/webm' })), `sintetis_${App.sims.map(s => s.kode).join('-')}.webm`);
  App.recording = { rec, canvas }; rec.start();
  seek(0); App.playing = true; setPlay(true); $('#btn-video .lbl').textContent = 'Stop rekam';
}
function stopRecording() {
  if (!App.recording) return;
  App.recording.rec.stop(); App.recording = null; $('#btn-video .lbl').textContent = 'Video';
}
function download(url, name) { const a = document.createElement('a'); a.href = url; a.download = name; a.click(); }

/* ---------------------------------------------------------------- editor skenario (FR-05) */
const HARI = ['Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu'];
const FAKTOR_NAMA = { orang_tua_anak_sekolah: 'orang tua murid', perempuan: 'perempuan', laki_laki: 'laki-laki',
  pemuda: 'pemuda 16–30', lansia: 'lansia 55+', pengguna_hp: 'pengguna HP' };
let edState = null;
function openEditor() {
  const cur = App.meta.skenario.find(s => s.berkas === $('#sel-sk').value) || App.meta.skenario[0];
  edState = JSON.parse(JSON.stringify(cur.isi || {}));
  edState.kode = (edState.kode || 'skenario') + '_salinan';
  renderEditor(); $('#dlg-editor').showModal();
}
function renderEditor() {
  const s = edState, w = App.world;
  const kabOpt = sel => w.kabupaten.map(k => `<option value="${k.id}" ${k.id === sel ? 'selected' : ''}>${esc(k.nama)}</option>`).join('');
  const disOpt = (kab, sel) => {
    const ki = w.kabupaten.findIndex(k => k.id === kab);
    return `<option value="">(seluruh kabupaten)</option>` + w.distrik.filter(d => d.kab === ki).map(d => `<option ${d.nama === sel ? 'selected' : ''}>${esc(d.nama)}</option>`).join('');
  };
  $('#ed-form').innerHTML = `
    <label>Nama</label><input type="text" data-k="nama" value="${esc(s.nama)}">
    <label>Kode berkas</label><input type="text" data-k="kode" value="${esc(s.kode)}">
    <label>Mulai</label><span><select data-k="mulai.hari">${HARI.map(h => `<option ${h === s.mulai.hari ? 'selected' : ''}>${h}</option>`).join('')}</select>
      jam <input type="number" min="0" max="23" data-k="mulai.jam" value="${s.mulai.jam}" style="width:56px"></span>
    <label>Durasi (jam)</label><input type="number" data-k="durasi_jam" value="${s.durasi_jam}">
    <label>Langkah (menit)</label><input type="number" data-k="langkah_menit" value="${s.langkah_menit}">
    <label>Teks isu</label><textarea data-k="isu.teks">${esc(s.isu.teks)}</textarea>
    <label>Emosi pesan</label><span><input type="text" data-k="isu.emosi" value="${esc(s.isu.emosi || '')}" style="width:90px">
      intensitas <input type="range" min="0" max="1" step="0.05" data-k="isu.intensitas_emosi" value="${s.isu.intensitas_emosi}"> <b>${s.isu.intensitas_emosi}</b></span>
    <label title="Dipakai di prompt agen kunci LLM: mengapa warga bisa percaya isu ini">Alasan percaya</label><input type="text" data-k="isu.alasan_percaya" placeholder="karena takut anak-anak celaka" value="${esc(s.isu.alasan_percaya || '')}">
    <label>Pengali kerentanan</label><span>${(App.meta.faktor_identitas || []).map(f => `<label class="inline">${FAKTOR_NAMA[f] || f}
      <input type="number" step="0.1" min="0.2" max="3" data-k="isu.faktor_identitas.${f}" value="${(s.isu.faktor_identitas || {})[f] ?? 1}" style="width:56px"></label>`).join(' ')}</span>
    <label>Ajakan bertindak</label><input type="checkbox" data-k="isu.ajakan_tindakan" ${s.isu.ajakan_tindakan ? 'checked' : ''}>
    <label>Titik tanam</label><div id="ed-tanam">${s.tanam.map((t, i) => `
      <div class="tanam-row" data-i="${i}">T+<input type="number" data-t="t_jam" value="${t.t_jam ?? 0}" style="width:52px">jam
        <select data-t="kanal">${['wa', 'fb', 'tatap_muka'].map(k => `<option ${k === t.kanal ? 'selected' : ''}>${k}</option>`).join('')}</select>
        <select data-t="jenis_grup">${['sekolah', 'keluarga', 'jemaat', 'marga', 'info'].map(k => `<option ${k === t.jenis_grup ? 'selected' : ''}>${k}</option>`).join('')}</select>
        <select data-t="kabupaten">${kabOpt(t.kabupaten)}</select>
        <select data-t="distrik">${disOpt(t.kabupaten, t.distrik)}</select>
        grup <input type="number" min="1" data-t="jumlah_grup" value="${t.jumlah_grup ?? 1}" style="width:48px">
        akun medsos <input type="number" min="0" data-t="akun_medsos" value="${t.akun_medsos ?? 0}" style="width:48px">
        <button data-del="${i}">Hapus</button></div>`).join('')}
      <button id="ed-add">+ Titik tanam</button></div>
    <label title="Aktor yang SENGAJA menyebar: kebal klarifikasi, tidak kehilangan semangat, bisa menyerang klarifikasi">Provokator / buzzer</label>
    <div id="ed-prov">${(s.provokator || []).map((p, i) => `
      <div class="tanam-row" data-i="${i}">
        <select data-p="jenis">${['buzzer', 'lokal'].map(k => `<option ${k === p.jenis ? 'selected' : ''}>${k}</option>`).join('')}</select>
        ${p.jenis === 'buzzer' ? `
          <select data-p="kanal">${['fb', 'wa'].map(k => `<option ${k === (p.kanal || 'fb') ? 'selected' : ''}>${k}</option>`).join('')}</select>
          ${p.kanal === 'wa' ? `<select data-p="jenis_grup">${['keluarga', 'marga', 'jemaat', 'sekolah'].map(k => `<option ${k === p.jenis_grup ? 'selected' : ''}>${k}</option>`).join('')}</select>` : ''}
          akun <input type="number" min="1" data-p="akun" value="${p.akun ?? 10}" style="width:48px">
          grup/akun <input type="number" min="1" data-p="grup_per_akun" value="${p.grup_per_akun ?? 3}" style="width:44px">
          intensitas <input type="number" step="0.5" min="0.5" data-p="intensitas" value="${p.intensitas ?? 2}" style="width:48px">
          <label class="inline"><input type="checkbox" data-p="terkoordinasi" ${p.terkoordinasi !== false ? 'checked' : ''}> terkoordinasi</label>
          akun baru stlh <input type="number" min="0" data-p="pulih_jam" value="${p.pulih_jam ?? 0}" style="width:44px">jam`
        : `orang <input type="number" min="1" data-p="jumlah" value="${p.jumlah ?? 3}" style="width:48px">
          <label class="inline"><input type="checkbox" data-p="ajak_jaga_kampung" ${p.ajak_jaga_kampung !== false ? 'checked' : ''}> ajak jaga kampung</label>`}
        <select data-p="kabupaten">${kabOpt(p.kabupaten)}</select>
        <select data-p="distrik">${disOpt(p.kabupaten, p.distrik)}</select>
        T+<input type="number" min="0" data-p="mulai_jam" value="${p.mulai_jam ?? 0}" style="width:48px">–<input type="number" min="1" data-p="selesai_jam" value="${p.selesai_jam ?? s.durasi_jam}" style="width:48px">jam
        <label class="inline"><input type="checkbox" data-p="serang_klarifikasi" ${p.serang_klarifikasi !== false ? 'checked' : ''}> serang klarifikasi</label>
        <button data-pdel="${i}">Hapus</button>
        <input type="text" data-p="nama" placeholder="nama aktor (opsional)" value="${esc(p.nama || '')}" style="width:100%">
        <textarea data-p="contoh_pesan" rows="2" style="width:100%" placeholder="Contoh pesan ilustratif, satu per baris (opsional)">${esc((p.contoh_pesan || []).join('\n'))}</textarea>
        ${p.serang_klarifikasi !== false ? `<input type="text" data-p="contoh_serangan" placeholder="Contoh pesan menyerang klarifikasi (opsional)" value="${esc(p.contoh_serangan || '')}" style="width:100%">` : ''}
        </div>`).join('')}
      <button id="ed-padd">+ Provokator</button>
      ${(s.provokator || []).length ? `<button id="ed-pesan" title="Isi contoh pesan yang masih kosong dengan AI">Buat contoh pesan (AI)</button>
      <span id="ed-pesan-msg" class="muted"></span>` : ''}</div>
    ${s.catatan ? `<div class="catatan"><b>Asumsi AI:</b> ${esc(s.catatan)}</div>` : ''}`;
  $$('#ed-form [data-k]').forEach(el => el.oninput = () => {
    const v = el.type === 'checkbox' ? el.checked : el.type === 'number' || el.type === 'range' ? +el.value : el.value;
    const ks = el.dataset.k.split('.'); let o = edState;
    ks.slice(0, -1).forEach(k => o = o[k] = o[k] || {});
    if (ks[1] === 'faktor_identitas' && v === 1) delete o[ks.at(-1)]; else o[ks.at(-1)] = v;
    if (el.type === 'range') el.nextElementSibling.textContent = v;
    yamlPreview();
  });
  $$('#ed-tanam .tanam-row').forEach(row => $$('[data-t]', row).forEach(el => el.onchange = () => {
    const t = edState.tanam[+row.dataset.i]; const k = el.dataset.t;
    const v = el.type === 'number' ? +el.value : el.value;
    if (v === '' || (k === 'akun_medsos' && v === 0)) delete t[k]; else t[k] = v;
    if (k === 'kabupaten') { delete t.distrik; renderEditor(); }
    yamlPreview();
  }));
  $$('#ed-tanam [data-del]').forEach(b => b.onclick = () => { edState.tanam.splice(+b.dataset.del, 1); renderEditor(); });
  $('#ed-add').onclick = () => { edState.tanam.push({ t_jam: 0, kanal: 'wa', jenis_grup: 'keluarga', kabupaten: 'kota_jayapura', jumlah_grup: 3 }); renderEditor(); };
  $$('#ed-prov .tanam-row').forEach(row => $$('[data-p]', row).forEach(el => el.onchange = () => {
    const p = edState.provokator[+row.dataset.i]; const k = el.dataset.p;
    let v = el.type === 'checkbox' ? el.checked : el.type === 'number' ? +el.value : el.value;
    if (k === 'contoh_pesan') v = v.split('\n').map(x => x.trim()).filter(Boolean);
    if (k === 'nama' || k === 'contoh_serangan') v = v.trim();
    if (v === '' || (Array.isArray(v) && !v.length) || (k === 'pulih_jam' && v === 0)) delete p[k]; else p[k] = v;
    if (k === 'serang_klarifikasi' && !v) { delete p.contoh_serangan; renderEditor(); }
    if (k === 'kabupaten') delete p.distrik;
    if (k === 'jenis' || k === 'kanal' || k === 'kabupaten') {
      if (k === 'jenis') ['kanal', 'jenis_grup', 'akun', 'grup_per_akun', 'intensitas', 'terkoordinasi', 'pulih_jam', 'jumlah', 'ajak_jaga_kampung'].forEach(x => delete p[x]);
      if (k === 'kanal') { if (v === 'wa') p.jenis_grup = p.jenis_grup || 'keluarga'; else delete p.jenis_grup; }
      renderEditor();
    }
    yamlPreview();
  }));
  $$('#ed-prov [data-pdel]').forEach(b => b.onclick = () => {
    edState.provokator.splice(+b.dataset.pdel, 1);
    if (!edState.provokator.length) delete edState.provokator;
    renderEditor();
  });
  if ($('#ed-pesan')) $('#ed-pesan').onclick = async () => {
    const b = $('#ed-pesan'); b.disabled = true; $('#ed-pesan-msg').textContent = 'AI sedang menulis contoh pesan…';
    try {
      const llm = $('#sel-llm').value;
      const r = await fetch('/api/ai/contoh-pesan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ isi: edState, penyedia: ['abm'].includes(llm) ? null : llm }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      edState = j.isi; renderEditor();
      $('#ed-pesan-msg').textContent = j.info.diisi ? `${j.info.diisi} aktor diisi · ${j.info.model} · $${j.info.biaya_usd}`
        : 'Semua aktor sudah punya contoh pesan (kosongkan dulu bila ingin diganti).';
    } catch (err) { $('#ed-pesan-msg').textContent = 'Gagal: ' + err.message; b.disabled = false; }
  };
  $('#ed-padd').onclick = () => {
    (edState.provokator = edState.provokator || []).push({ jenis: 'buzzer', kanal: 'fb', kabupaten: App.world.kabupaten[0].id, akun: 10,
      grup_per_akun: 3, terkoordinasi: true, intensitas: 2, mulai_jam: 2, selesai_jam: edState.durasi_jam, serang_klarifikasi: true });
    renderEditor();
  };
  yamlPreview();
}
function yamlPreview() { $('#ed-yaml').textContent = jsyaml.dump(edState, { lineWidth: 100 }); }
function blankScenario() {
  edState = { nama: 'Skenario baru', kode: 'skenario_baru', mulai: { hari: 'Sabtu', jam: 7 }, langkah_menit: 30, durasi_jam: 48,
    isu: { teks: '', emosi: 'takut', intensitas_emosi: 0.7, faktor_identitas: {}, ajakan_tindakan: false },
    tanam: [{ t_jam: 0, kanal: 'wa', jenis_grup: 'keluarga', kabupaten: App.world.kabupaten[0].id, jumlah_grup: 3 }] };
  renderEditor();
}
async function draftScenario() {
  const desk = $('#ed-desk').value.trim();
  if (desk.length < 10) { $('#ed-ai-msg').textContent = 'Tulis deskripsi skenario dulu.'; return; }
  $('#ed-ai').disabled = true; $('#ed-ai-msg').textContent = 'AI sedang menyusun skenario… (bisa 30–90 detik)';
  try {
    const r = await fetch('/api/ai/skenario', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ deskripsi: desk, penyedia: aiProvider(), agen: +$('#sel-n').value }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail);
    edState = j.isi; renderEditor();
    $('#ed-ai-msg').textContent = `Tersusun oleh ${j.info.model}. Periksa isian di bawah, ubah bila perlu, lalu simpan.`;
  } catch (e) { $('#ed-ai-msg').textContent = 'Gagal: ' + e.message; }
  finally { $('#ed-ai').disabled = false; }
}
async function saveScenario() {
  const r = await fetch('/api/skenario', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ isi: edState, berkas: edState.kode, agen: +$('#sel-n').value }) });
  const j = await r.json();
  if (!r.ok) { $('#ed-msg').textContent = 'Tidak valid: ' + j.detail; return; }
  $('#ed-msg').textContent = `Tersimpan sebagai scenarios/${j.berkas}`;
  await loadMeta(j.berkas);
}

/* ---------------------------------------------------------------- Monte Carlo */
async function openMC() {
  const list = await (await fetch('/api/mc')).json();
  $('#mc-sel').innerHTML = list.map(n => `<option>${esc(n)}</option>`).join('') || '<option value="">(belum ada hasil)</option>';
  $('#dlg-mc').showModal();
  if (list.length) showMC(list[0]);
}
async function showMC(nama) {
  if (!nama) return;
  const d = await (await fetch(`/api/mc/${encodeURIComponent(nama)}`)).json();
  $('#mc-report').href = d.laporan || '#';
  const tipe = ['kota', 'pesisir', 'pedalaman', 'kepulauan', 'semua'].filter(t => d.peringkat.some(r => r.tipe === t));
  const codes = [...new Set(d.efek.map(e => e.intervensi))].sort();
  const chart = echarts.getInstanceByDom($('#mc-chart')) || echarts.init($('#mc-chart'));
  const ink = cssv('--muted'), palette = [cssv('--accent'), '#64748b', '#1fb5a0', '#a855f7', '#e6a700', '#e5484d'];
  const W = 0.8 / codes.length;   // lebar batang dalam satuan kategori; batang & IK digambar sendiri agar selalu sejajar
  const series = codes.map((c, ci) => ({
    name: c, type: 'custom', color: palette[ci % palette.length],
    data: tipe.map((t, ti) => {
      const e = d.efek.find(x => x.tipe === t && x.intervensi === c);
      if (!e) return null;
      const base = e.efek_persen ? e.selisih / (e.efek_persen / 100) : 0;
      return [ti + (ci - (codes.length - 1) / 2) * W, +e.efek_persen.toFixed(1), base ? 100 * e.selisih_lo / base : 0, base ? 100 * e.selisih_hi / base : 0];
    }).filter(Boolean),
    renderItem: (params, api) => {
      const x = api.value(0), v = api.value(1);
      const [x0, y0] = api.coord([x - W * 0.45, 0]), [x1, y1] = api.coord([x + W * 0.45, v]);
      const xm = (x0 + x1) / 2, ylo = api.coord([x, api.value(2)])[1], yhi = api.coord([x, api.value(3)])[1];
      const stroke = cssv('--ink');
      return { type: 'group', children: [
        { type: 'rect', shape: { x: x0, y: Math.min(y0, y1), width: x1 - x0, height: Math.abs(y1 - y0) }, style: { fill: api.visual('color') } },
        { type: 'line', shape: { x1: xm, y1: ylo, x2: xm, y2: yhi }, style: { stroke, lineWidth: 1 } },
        { type: 'line', shape: { x1: xm - 3, y1: ylo, x2: xm + 3, y2: ylo }, style: { stroke } },
        { type: 'line', shape: { x1: xm - 3, y1: yhi, x2: xm + 3, y2: yhi }, style: { stroke } }] };
    },
    encode: { x: 0, y: [1, 2, 3], tooltip: [1, 2, 3] },
    dimensions: ['x', 'efek %', 'IK bawah %', 'IK atas %'],
  }));
  chart.setOption({
    animation: false, title: { text: 'Efek terhadap baseline A: beban siaga aktif (%), IK 95%', textStyle: { fontSize: 13, fontWeight: 600, color: cssv('--ink') } },
    legend: { top: 26, textStyle: { color: ink }, icon: 'roundRect', itemGap: 16 }, tooltip: { ...chartTheme().tooltip, trigger: 'item' }, textStyle: chartTheme().textStyle, grid: { top: 64, left: 52, right: 14, bottom: 28 },
    xAxis: { type: 'value', min: -0.5, max: tipe.length - 0.5, interval: 1, splitLine: { show: false },
      axisLabel: { color: ink, formatter: v => tipe[Math.round(v)] ?? '' } },
    yAxis: { type: 'value', axisLabel: { color: ink, formatter: v => v + '%' }, splitLine: { lineStyle: { color: cssv('--line') } } },
    series,
  }, true);
  const man = d.manifest;
  $('#mc-body').innerHTML = `<p class="muted">${fmt(man.agen)} agen · ${man.putaran} putaran/intervensi · mode ${esc(man.mode_llm)} · keputusan agen kunci ${esc(JSON.stringify(man.keputusan_agen_kunci))}</p>
    <h4>Rekomendasi draf</h4><ul class="rec">${d.rekomendasi.map(r => `<li>${esc(r).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')}</li>`).join('')}</ul>
    <h4>Peringkat (1 = beban siaga terkecil) & stabilitas bootstrap</h4>
    <table class="t"><tr><th>Tipe</th>${[1, 2, 3, 4, 5, 6].map(i => `<th>#${i}</th>`).join('')}</tr>
    ${tipe.map(t => `<tr><td>${t}</td>${d.peringkat.filter(r => r.tipe === t).map(r => `<td title="${esc(man.intervensi[r.intervensi])}">${r.intervensi} <span class="muted">${r.nilai.toFixed(0)} · ${Math.round(r.stabilitas * 100)}%</span></td>`).join('')}</tr>`).join('')}</table>`;
  showKeterlambatan(d.keterlambatan);
}
function showKeterlambatan(k) {
  const box = $('#mc-ket');
  box.hidden = !k;
  if (!k) return;
  const chart = echarts.getInstanceByDom($('#mc-ket-chart')) || echarts.init($('#mc-ket-chart'));
  const ink = cssv('--muted'), palette = [cssv('--accent'), '#1fb5a0', '#e6a700', '#a855f7', '#e5484d'];
  const kode = Object.keys(k.kurva || {});
  const series = [];
  kode.forEach((c, i) => {
    const pts = k.kurva[c], col = palette[i % palette.length], nm = (k.ringkasan[c] || {}).nama || c;
    series.push({ name: nm, type: 'line', data: pts.map(p => [p.jam, +p.nilai.toFixed(1)]), color: col, symbolSize: 5,
      markLine: (k.ringkasan[c] || {}).batas_waktu_jam != null ? { symbol: 'none', label: { formatter: `batas ±jam ${Math.round(k.ringkasan[c].batas_waktu_jam)}`, color: ink },
        lineStyle: { type: 'dashed', color: col }, data: [{ xAxis: k.ringkasan[c].batas_waktu_jam }] } : undefined });
    series.push({ name: nm, type: 'line', data: pts.map(p => [p.jam, p.nilai_lo]), lineStyle: { opacity: 0 }, symbol: 'none', stack: 'ik' + c, tooltip: { show: false } });
    series.push({ name: nm, type: 'line', data: pts.map(p => [p.jam, p.nilai_hi - p.nilai_lo]), lineStyle: { opacity: 0 }, symbol: 'none', stack: 'ik' + c,
      areaStyle: { color: col, opacity: 0.15 }, tooltip: { show: false } });
  });
  if (kode.length) {
    const b = k.kurva[kode[0]][0].baseline;
    series.push({ name: 'Tanpa tindakan', type: 'line', data: k.kurva[kode[0]].map(p => [p.jam, b]), color: ink, lineStyle: { type: 'dotted' }, symbol: 'none' });
  }
  chart.setOption({ animation: false, legend: { top: 0, textStyle: { color: ink }, data: [...kode.map(c => (k.ringkasan[c] || {}).nama || c), 'Tanpa tindakan'] },
    tooltip: { ...chartTheme().tooltip, trigger: 'axis' }, textStyle: chartTheme().textStyle, grid: { top: 36, left: 52, right: 14, bottom: 36 },
    xAxis: { type: 'value', name: 'jam sejak isu diketahui', nameLocation: 'middle', nameGap: 24, axisLabel: { color: ink } },
    yAxis: { type: 'value', name: 'orang-jam / 1.000', axisLabel: { color: ink }, splitLine: { lineStyle: { color: cssv('--line') } } }, series }, true);
  $('#mc-ket-kal').innerHTML = Object.values(k.ringkasan || {}).map(r => `<li>${esc(r.kalimat || '')}${(r.peringatan || []).length ? ` <b>${esc(r.peringatan.join('; '))}</b>` : ''}</li>`).join('');
}
async function startKeterlambatan() {
  const body = { skenario: $('#sel-sk').value, intervensi: 'CDFK', putaran: +$('#mc-n').value || 30, agen: +$('#mc-agen').value };
  const r = await fetch('/api/keterlambatan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) return toast((await r.json()).detail);
  const poll = async () => {
    const s = await (await fetch('/api/keterlambatan-status')).json();
    $('#mc-status').textContent = s.error ? `Gagal: ${s.error}` : s.jalan ? `Harga keterlambatan… ${s.log.at(-1) || ''}` : s.hasil ? `Selesai: ${s.hasil}` : '';
    if (s.jalan) setTimeout(poll, 2500); else if (s.hasil) { await openMC(); $('#mc-sel').value = s.hasil; showMC(s.hasil); }
  };
  poll();
}

/* ---------------------------------------------------------------- kartu respons */
async function openKartu() {
  const m = await (await fetch('/api/lapangan/meta')).json();
  const p = m.pustaka;
  $('#kartu-status').innerHTML = esc(p.teks).replace(/\n/g, '<br>') + (p.usang ? ' <b>Pustaka usang: kartu memuat peringatan.</b>' : '');
  $('#kartu-list').innerHTML = m.kabupaten.map(k => `<li><span>${esc(k.nama)}</span><a class="btn" href="/api/kartu/${encodeURIComponent(k.id)}.pdf" target="_blank" rel="noopener">Unduh PDF</a></li>`).join('');
  $('#dlg-kartu').showModal();
}

async function startMC() {
  const body = { agen: +$('#mc-agen').value, putaran: +$('#mc-n').value, skenario: $('#sel-sk').value,
    llm: $('#mc-beku').value ? $('#sel-llm').value : 'abm', beku: $('#mc-beku').value || null };
  const r = await fetch('/api/mc', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) return toast((await r.json()).detail);
  const poll = async () => {
    const s = await (await fetch('/api/mc-status')).json();
    $('#mc-status').textContent = s.error ? `Gagal: ${s.error}` : s.jalan ? `Berjalan… ${s.log.at(-1) || ''}` : s.hasil ? `Selesai: ${s.hasil}` : '';
    if (s.jalan) setTimeout(poll, 2000); else if (s.hasil) openMC();
  };
  poll();
}

/* ---------------------------------------------------------------- laporan AI */
let aiMd = '', aiPdf = '';
function aiProvider() {
  const sel = $('#sel-llm').value;
  if (['sumopod', 'deepseek', 'ollama', 'mock'].includes(sel)) return sel;
  return App.meta.llm.sumopod_kunci ? 'sumopod' : App.meta.llm.deepseek_kunci ? 'deepseek' : 'mock';
}
async function openAI(pilih) {
  const [runs, mc] = await Promise.all([fetch('/api/runs').then(r => r.json()), fetch('/api/mc').then(r => r.json())]);
  const last = App.result ? `Putaran dashboard terakhir (${App.result.map(r => r.kode).join(' vs ')})` : 'Putaran dashboard terakhir (belum ada)';
  $('#ai-src').innerHTML = `<option value="putaran|">${esc(last)}</option>`
    + (mc.length ? `<optgroup label="Monte Carlo">${mc.map(n => `<option value="mc|${esc(n)}">${esc(n)}</option>`).join('')}</optgroup>` : '')
    + (runs.length ? `<optgroup label="Log tersimpan">${runs.map(r => `<option value="log|${esc(r.nama)}">${esc(r.nama)}</option>`).join('')}</optgroup>` : '');
  if (pilih) $('#ai-src').value = pilih; else if (!App.result && mc.length) $('#ai-src').value = `mc|${mc[0]}`;
  $('#ai-llm').value = aiProvider();
  $('#dlg-ai').showModal();
}
async function makeReport() {
  const [sumber, nama] = $('#ai-src').value.split('|');
  $('#ai-go').disabled = true; $('#ai-pdf').disabled = true; aiPdf = '';
  $('#ai-msg').textContent = 'AI sedang menulis laporan dan menyusun PDF… (bisa 1–3 menit)';
  try {
    const r = await fetch('/api/ai/laporan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sumber, nama, penyedia: $('#ai-llm').value, fokus: $('#ai-fokus').value }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail);
    aiMd = j.markdown; $('#ai-out').innerHTML = j.html;
    $('#ai-dl').disabled = $('#ai-copy').disabled = false;
    aiPdf = j.pdf || ''; $('#ai-pdf').disabled = !aiPdf;
    if (!('pdf' in j)) toast('Server dashboard masih versi lama: tutup jendela server lalu jalankan ulang jalankan.bat');
    $('#ai-msg').textContent = `Tersimpan di ${j.berkas.replace(/\.md$/, '.pdf / .md')}` + (j.info.biaya_usd ? ` · ±${j.info.biaya_usd} USD` : '')
      + (j.pdf_error ? ` · PDF gagal: ${j.pdf_error}` : '');
  } catch (e) { $('#ai-msg').textContent = 'Gagal: ' + e.message; }
  finally { $('#ai-go').disabled = false; }
}

/* ---------------------------------------------------------------- alur otomatis */
const ALUR_IKON = { menunggu: '', jalan: '<span class="spin" aria-hidden="true"></span>', selesai: '<svg><use href="#i-check"/></svg>',
  dilewati: '–', gagal: '<svg><use href="#i-x"/></svg>' };
let alurPoll = null;
async function openAlur() {
  $('#al-sk').innerHTML = $('#sel-sk').innerHTML; $('#al-sk').value = $('#sel-sk').value;
  const tokoh = $('#sel-llm').value; $('#al-llm').value = tokoh;
  $('#al-ai').value = aiProvider();
  $('#dlg-alur').showModal();
  pollAlur();
}
async function startAlur() {
  const body = { skenario: $('#al-sk').value, llm: $('#al-llm').value, pilot: +$('#al-pilot').value, putaran: +$('#al-n').value,
    agen: +$('#al-agen').value, penyedia: $('#al-ai').value, fokus: $('#al-fokus').value, kurva_keterlambatan: $('#al-kurva').checked };
  const r = await fetch('/api/alur', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) return toast((await r.json()).detail);
  $('#al-hasil').hidden = true;
  pollAlur();
}
async function pollAlur() {
  clearTimeout(alurPoll);
  let s;
  try { s = await (await fetch('/api/alur-status')).json(); } catch (e) { return; }
  renderAlur(s);
  if (s.jalan) alurPoll = setTimeout(pollAlur, 2000);
}
function renderAlur(s) {
  const ada = s.tahap && s.tahap.length;
  $('#al-steps').hidden = !ada;
  $('#alur-form').classList.toggle('dim', !!s.jalan);
  $('#al-go').disabled = !!s.jalan;
  if (ada) $('#al-steps').innerHTML = s.tahap.map((t, i) => `<li class="st-${t.status}"><span class="dot">${ALUR_IKON[t.status] || i + 1}</span>
    <div><b>${esc(t.judul)}</b><small>${esc(t.catatan || (t.status === 'menunggu' ? 'menunggu' : ''))}</small></div></li>`).join('');
  $('#al-log').textContent = s.jalan ? (s.log || []).at(-1) || '' : '';
  $('#al-msg').textContent = s.jalan ? 'Berjalan di server… dialog boleh ditutup, proses tetap jalan.' : s.error ? `Gagal: ${s.error}` : '';
  const h = s.hasil;
  $('#al-hasil').hidden = !h;
  if (!h) return;
  const iv = App.meta.intervensi;
  $('#al-hasil').innerHTML = `<h4>Selesai dalam ${Math.round(h.detik / 60)} menit</h4>
    <p>Langkah terbaik untuk semua wilayah: <b>${esc(h.terbaik)} · ${esc(iv[h.terbaik] || '')}</b>.
    Putaran contoh memakai seed ${h.seed_peta}, yang paling mewakili hasil rata-rata Monte Carlo.</p>
    <div class="ctl">
      ${h.pdf ? `<a class="btn primary" href="${h.pdf}" target="_blank" rel="noopener">Buka laporan PDF</a>` : `<span class="muted">PDF gagal: ${esc(h.pdf_error || '')}</span>`}
      <a class="btn" href="${h.html}" target="_blank" rel="noopener">Versi HTML</a>
      <button data-peta="${esc(h.log_peta[h.terbaik])}">Putar di peta: ${esc(h.terbaik)}</button>
      <button data-peta="${esc(h.log_peta.A)}">Putar di peta: tanpa intervensi</button>
      <button id="al-mc">Lihat Monte Carlo</button>
    </div>
    <p class="muted">Tip: setelah memutar di peta, pakai tombol Video untuk merekam bahan briefing.</p>`;
  $$('#al-hasil [data-peta]').forEach(b => b.onclick = async () => { $('#dlg-alur').close(); await refreshReplays(); replay(b.dataset.peta); });
  $('#al-mc').onclick = async () => { $('#dlg-alur').close(); await openMC(); $('#mc-sel').value = h.folder; showMC(h.folder); };
}

/* ---------------------------------------------------------------- replay log tersimpan */
async function refreshReplays() {
  const runs = await (await fetch('/api/runs')).json();
  $('#sel-replay').innerHTML = '<option value="">Putar ulang log…</option>' +
    runs.map(r => `<option value="${esc(r.nama)}">${esc(r.nama)} (${fmt(r.agen)} agen)</option>`).join('');
}
async function replay(nama) {
  if (!nama) return;
  status('Memuat log…');
  const d = await (await fetch(`/api/runs/${encodeURIComponent(nama)}`)).json();
  await loadWorld(d.manifest.agen, d.manifest.seed_dunia);
  resetRun(1);
  App.T = d.T; App.dtJam = d.manifest.langkah_menit / 60; $('#scrub').max = d.T;
  App.sims = [newSim(d.manifest.intervensi, `replay ${nama}`)];
  d.frames.forEach(fr => pushFrame(App.sims[0], fr));
  initChart(); seek(0); App.playing = true; setPlay(true);
  status(`Replay ${nama}: seed ${d.manifest.seed}, versi ${d.manifest.versi_kode}`);
}

/* ---------------------------------------------------------------- utilitas UI */
function status(s) { $('#status-line').textContent = s; }
function setPlay(on) { const b = $('#btn-play'); b.classList.toggle('is-playing', !!on); b.setAttribute('aria-label', on ? 'Jeda' : 'Putar'); }
function togglePanel() {
  const ciut = document.documentElement.classList.toggle('panel-ciut');
  try { localStorage.setItem('panelCiut', ciut ? '1' : '0'); } catch (e) {}
  const t0 = performance.now();
  const step = now => { App.maps.forEach(m => m.resize()); if (App.chart) App.chart.resize(); if (now - t0 < 360) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
function chartTheme() {
  return { textStyle: { fontFamily: 'Inter, system-ui, sans-serif' },
    tooltip: { backgroundColor: cssv('--surface'), borderColor: cssv('--line-strong'), borderWidth: 1, padding: [8, 12],
      textStyle: { color: cssv('--ink'), fontSize: 12 }, extraCssText: 'border-radius:10px;box-shadow:0 10px 30px -10px rgba(0,0,0,.35);' } };
}
function toast(s) {
  const el = document.createElement('div'); el.className = 'toast'; el.textContent = s; document.body.append(el);
  setTimeout(() => el.remove(), 4000);
}
async function loadMeta(selectFile) {
  App.meta = await (await fetch('/api/meta')).json();
  $('#sel-sk').innerHTML = App.meta.skenario.map(s => `<option value="${esc(s.berkas)}" ${s.berkas === selectFile ? 'selected' : ''}>${esc(s.nama)}</option>`).join('');
  const iv = Object.entries(App.meta.intervensi);
  $('#sel-iv').innerHTML = iv.map(([k, v]) => `<option value="${k}">${k} · ${esc(v)}</option>`).join('');
  $('#sel-cmp').innerHTML = '<option value="">—</option>' + iv.map(([k, v]) => `<option value="${k}">${k} · ${esc(v)}</option>`).join('');
  App.ambang = App.meta.ambang_eskalasi || App.ambang;
  if (!App.meta.llm.deepseek_kunci) $('#sel-llm option[value=deepseek]').textContent = 'DeepSeek (kunci tidak ada)';
}

function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  try { t ? localStorage.setItem('tema', t) : localStorage.removeItem('tema'); } catch (e) {}
  readColors(); renderLegend(); swapBasemap(); updateChart(); App.dirty = true;
}

async function init() {
  try { const t = localStorage.getItem('tema'); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
  try { const b = localStorage.getItem('petaDasar'); if (b) $('#sel-base').value = b; } catch (e) {}
  readColors(); renderLegend(); ensureMaps(1);
  $('#sel-base').onchange = e => { try { localStorage.setItem('petaDasar', e.target.value); } catch (er) {} swapBasemap(); };
  $('#btn-run').onclick = run;
  $('#btn-play').onclick = () => { App.playing = !App.playing; setPlay(App.playing); if (App.playing && App.cur >= App.T) seek(0); };
  $('#sel-speed').onchange = e => App.speed = +e.target.value;
  $('#scrub').oninput = e => { App.playing = false; setPlay(false); seek(+e.target.value); };
  $('#btn-png').onclick = exportPNG;
  $('#btn-video').onclick = startRecording;
  $('#btn-panel').onclick = togglePanel;
  $('#btn-theme').onclick = () => applyTheme(isDark() ? 'light' : 'dark');
  $('#btn-editor').onclick = openEditor; $('#ed-close').onclick = () => $('#dlg-editor').close(); $('#ed-save').onclick = saveScenario;
  $('#btn-mc').onclick = openMC; $('#mc-close').onclick = () => $('#dlg-mc').close();
  $('#mc-sel').onchange = e => showMC(e.target.value); $('#mc-start').onclick = startMC;
  $('#mc-ket-start').onclick = startKeterlambatan;
  $('#btn-kartu').onclick = openKartu; $('#kartu-close').onclick = () => $('#dlg-kartu').close();
  $('#ed-ai').onclick = draftScenario; $('#ed-blank').onclick = blankScenario;
  $('#btn-ai').onclick = () => openAI(); $('#ai-close').onclick = () => $('#dlg-ai').close();
  $('#btn-alur').onclick = openAlur; $('#al-close').onclick = () => { clearTimeout(alurPoll); $('#dlg-alur').close(); };
  $('#al-go').onclick = startAlur;
  $('#ai-pdf').onclick = () => aiPdf && window.open(aiPdf, '_blank', 'noopener'); $('#ai-go').onclick = makeReport;
  $('#mc-ai').onclick = () => { $('#dlg-mc').close(); openAI($('#mc-sel').value ? `mc|${$('#mc-sel').value}` : ''); };
  $('#ai-dl').onclick = () => download(URL.createObjectURL(new Blob([aiMd], { type: 'text/markdown' })), 'laporan_ai.md');
  $('#ai-copy').onclick = () => navigator.clipboard.writeText(aiMd).then(() => toast('Teks laporan disalin'));
  $('#sel-replay').onchange = e => replay(e.target.value);
  $('#sel-n').onchange = e => loadWorld(+e.target.value);
  $$('#layers input').forEach(el => el.onchange = () => App.dirty = true);
  $('#ly-label').onchange = swapBasemap;
  $$('.tabs button').forEach(b => b.onclick = () => tab(b.dataset.tab));
  addEventListener('keydown', e => {
    if (e.target.matches('input, textarea, select')) return;
    if (e.key === ' ') { e.preventDefault(); $('#btn-play').click(); }
    if (e.key === 'ArrowRight') seek(App.cur + 1);
    if (e.key === 'ArrowLeft') seek(App.cur - 1);
  });
  if (reduceMotion) App.speed = 2, $('#sel-speed').value = '2';
  await loadMeta('isu_sentani.yaml');
  await loadWorld(+$('#sel-n').value);
  refreshReplays();
  requestAnimationFrame(loop);
}
init().catch(e => { console.error(e); status('Gagal memuat: ' + e.message); });
