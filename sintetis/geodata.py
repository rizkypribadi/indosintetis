"""Perakit geografi nyata: data/kampung.csv dan batas wilayah dari sumber terbuka.

Sumber (semua diunduh sekali ke data/mentah/, lalu dipakai ulang):
  - Kode, nama, dan poligon kabupaten/distrik/kampung sesuai Kepmendagri No 300.2.2-2430 Tahun 2025
    (github.com/cahyadsn/wilayah dan wilayah_boundaries, lisensi MIT).
  - Penduduk per kabupaten: Dukcapil, Kepmendagri No 300.2.2-2138 Tahun 2025 (wilayah_penduduk.sql).
  - Sebaran penduduk di dalam kabupaten: WorldPop 2020 constrained 100 m (CC BY 4.0). Total kabupaten
    tetap mengikuti Dukcapil; WorldPop hanya menentukan porsi tiap kampung dan titik permukimannya.
  - Sinyal: ubin uji kecepatan seluler Ookla Open Data (CC BY-NC-SA 4.0), beberapa kuartal terakhir.
    Ini PROKSI: tidak ada uji ≠ pasti tidak ada sinyal. Data Podes (BPS) tetap lebih baik bila tersedia.
  - Jarak ke laut dan elevasi: ubin elevasi Terrarium (AWS Terrain Tiles, Mapzen/USGS SRTM, domain publik).

Keluaran: data/kampung.csv (format yang dibaca population.py), data/batas_distrik.geojson,
data/batas_kabupaten.geojson, data/geodata_sumber.json (sumber, aturan, dan ringkasan pemeriksaan).
"""
from __future__ import annotations

import io
import json
import math
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DATA_DIR, load_region

MENTAH = DATA_DIR / "mentah"
GH = "https://raw.githubusercontent.com/cahyadsn/{repo}/master/db/{path}"
WORLDPOP = ("https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/BSGM/IDN/"
            "idn_ppp_2020_UNadj_constrained.tif")
OOKLA = ("https://ookla-open-data.s3.amazonaws.com/parquet/performance/type=mobile/year={y}/quarter={q}/"
         "{y}-{m:02d}-01_performance_mobile_tiles.parquet")
TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
BBOX = (134.0, -4.6, 141.2, 1.2)          # lon0, lat0, lon1, lat1: sembilan kab/kota Papua (termasuk Towe & Mapia)
_BB = "_".join(f"{v:g}" for v in BBOX)
PULAU = {"kep_yapen", "biak_numfor", "supiori"}
_ROW = re.compile(r"\('(91[0-9.]*)','([^']*)',(-?[0-9.]+),(-?[0-9.]+),'(\[.*?\])'\)", re.S)


# ------------------------------------------------------------------ unduhan
def _unduh(url: str, path: Path, log=print) -> Path:
    import httpx

    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"  unduh {url}")
    tmp = path.with_suffix(path.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=600,
                      headers={"User-Agent": "sintetis-geodata/1.0"}) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.replace(path)
    return path


def _poligon_sql(path: Path) -> list[tuple[str, str, float, float, list]]:
    """Baris (kode, nama, lat, lng, multipoligon [[ring[[lon, lat]...]]...]) dari berkas wilayah_boundaries."""
    out = []
    for kode, nama, lat, lng, p in _ROW.findall(path.read_text(encoding="utf-8")):
        g, d, x = json.loads(p), 0, None
        x = g
        while isinstance(x, list):
            d, x = d + 1, x[0]
        g = [[g]] if d == 2 else [g] if d == 3 else g    # cincin / poligon → multipoligon
        mp = [[[[float(b), float(a)] for a, b in ring] for ring in poly] for poly in g]   # [lat,lng] → [lon,lat]
        out.append((kode, nama.replace("\\'", "'"), float(lat), float(lng), mp))
    return out


# ------------------------------------------------------------------ penduduk (WorldPop)
def _baca_worldpop(path: Path, bbox=BBOX):
    """Potong raster WorldPop ke kotak Papua dengan membaca hanya strip yang perlu (raster utuh ±9 GB)."""
    import tifffile

    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        sx, sy, _ = page.tags["ModelPixelScaleTag"].value
        _, _, _, x0, y0, _ = page.tags["ModelTiepointTag"].value
        H, W = page.shape
        rps = page.rowsperstrip
        c0, c1 = int((bbox[0] - x0) / sx), int(math.ceil((bbox[2] - x0) / sx))
        r0, r1 = int((y0 - bbox[3]) / sy), int(math.ceil((y0 - bbox[1]) / sy))
        out = np.zeros((r1 - r0, c1 - c0), np.float32)
        fh = tf.filehandle
        for s in range(r0 // rps, (r1 - 1) // rps + 1):
            fh.seek(page.dataoffsets[s])
            seg, _, _ = page.decode(fh.read(page.databytecounts[s]), s)
            seg = np.asarray(seg).reshape(-1, W)
            a, b = s * rps, s * rps + seg.shape[0]
            lo, hi = max(a, r0), min(b, r1)
            out[lo - r0:hi - r0] = seg[lo - a:hi - a, c0:c1]
    out[~np.isfinite(out) | (out < 0)] = 0
    lon0 = x0 + c0 * sx
    lat0 = y0 - r0 * sy
    return out, (lon0, lat0, sx, sy)       # pusat piksel (i, j) = lon0 + (j+.5)sx, lat0 - (i+.5)sy


def _mask_poligon(mp: list, tf, shape) -> tuple[slice, slice, np.ndarray] | None:
    from matplotlib.path import Path as MPath

    lon0, lat0, sx, sy = tf
    allpts = np.concatenate([np.asarray(r[0]) for r in mp])
    j0 = max(0, int((allpts[:, 0].min() - lon0) / sx)); j1 = min(shape[1], int((allpts[:, 0].max() - lon0) / sx) + 2)
    i0 = max(0, int((lat0 - allpts[:, 1].max()) / sy)); i1 = min(shape[0], int((lat0 - allpts[:, 1].min()) / sy) + 2)
    if j1 <= j0 or i1 <= i0:
        return None
    jj, ii = np.meshgrid(np.arange(j0, j1), np.arange(i0, i1))
    pts = np.c_[lon0 + (jj.ravel() + 0.5) * sx, lat0 - (ii.ravel() + 0.5) * sy]
    m = np.zeros(len(pts), bool)
    for poly in mp:
        inside = MPath(np.asarray(poly[0])).contains_points(pts)
        for hole in poly[1:]:
            inside &= ~MPath(np.asarray(hole)).contains_points(pts)
        m |= inside
    return slice(i0, i1), slice(j0, j1), m.reshape(jj.shape)


# ------------------------------------------------------------------ elevasi & laut (Terrarium)
def _dem(z: int = 9, bbox=BBOX, log=print):
    from PIL import Image

    def tx(lon): return (lon + 180) / 360 * 2 ** z
    def ty(lat): return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2 ** z
    x0, x1 = int(tx(bbox[0])), int(tx(bbox[2]))
    y0, y1 = int(ty(bbox[3])), int(ty(bbox[1]))
    H, W = (y1 - y0 + 1) * 256, (x1 - x0 + 1) * 256
    elev = np.zeros((H, W), np.float32)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            p = _unduh(TERRARIUM.format(z=z, x=x, y=y), MENTAH / "dem" / f"{z}_{x}_{y}.png", log)
            a = np.asarray(Image.open(p).convert("RGB"), np.float32)
            elev[(y - y0) * 256:(y - y0 + 1) * 256, (x - x0) * 256:(x - x0 + 1) * 256] = \
                a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768

    def ke_piksel(lon, lat):
        lon, lat = np.asarray(lon, float), np.radians(np.asarray(lat, float))
        row = (1 - np.arcsinh(np.tan(lat)) / np.pi) / 2 * 2 ** z * 256 - y0 * 256
        col = (lon + 180) / 360 * 2 ** z * 256 - x0 * 256
        return np.clip(row, 0, H - 1), np.clip(col, 0, W - 1)
    return elev, ke_piksel, 40075.016 / (2 ** z * 256)   # km per piksel di khatulistiwa


def _jarak_laut(elev: np.ndarray) -> np.ndarray:
    """Jarak (piksel) ke laut. Laut = sel ≤ 0 m yang tersambung ke tepi mosaik (danau dataran rendah tidak ikut)."""
    from scipy import ndimage

    air = elev <= 0
    lab, _ = ndimage.label(air)
    tepi = np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    laut = np.isin(lab, tepi[tepi > 0])
    return ndimage.distance_transform_edt(~laut)


# ------------------------------------------------------------------ sinyal (Ookla)
def _ookla(kuartal: int, log=print) -> pd.DataFrame:
    import duckdb
    import httpx

    path = MENTAH / f"ookla_{_BB}_{kuartal}q.parquet"
    if path.exists():
        return pd.read_parquet(path)
    tersedia = []
    for y in range(date.today().year, 2019, -1):
        for q in (4, 3, 2, 1):
            u = OOKLA.format(y=y, q=q, m=3 * q - 2)
            try:
                if httpx.head(u, timeout=20).status_code == 200:
                    tersedia.append((y, q, u))
            except httpx.HTTPError:
                pass
            if len(tersedia) == kuartal:
                break
        if len(tersedia) == kuartal:
            break
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    bag = []
    for y, q, u in tersedia:
        log(f"  Ookla seluler {y} K{q}")
        bag.append(con.execute(
            f"SELECT quadkey, tile_x AS lon, tile_y AS lat, avg_d_kbps, tests, devices, {y * 10 + q} AS kuartal "
            f"FROM read_parquet('{u}') WHERE tile_x BETWEEN {BBOX[0]} AND {BBOX[2]} AND tile_y BETWEEN {BBOX[1]} AND {BBOX[3]}").df())
    df = pd.concat(bag, ignore_index=True)
    MENTAH.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


# ------------------------------------------------------------------ titik kampung (OpenStreetMap)
def _osm_tempat(log=print) -> pd.DataFrame:
    """Titik place=village/hamlet/town/... OSM di kotak Papua. Dipakai bila WorldPop tidak melihat permukiman."""
    import httpx

    path = MENTAH / f"osm_tempat_{_BB}.json"
    if not path.exists():
        log("  Overpass OSM: titik permukiman")
        q = (f"[out:json][timeout:120];node[place~\"^(city|town|village|hamlet|suburb|neighbourhood|isolated_dwelling)$\"]"
             f"({BBOX[1]},{BBOX[0]},{BBOX[3]},{BBOX[2]});out;")
        r = None
        for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                    "https://overpass.private.coffee/api/interpreter", "https://overpass-api.de/api/interpreter"):
            try:
                r = httpx.post(url, data={"data": q}, timeout=180, headers={"User-Agent": "sintetis-geodata/1.0"})
                if r.status_code == 200 and r.text.lstrip().startswith("{"):
                    break
                log(f"  {url}: HTTP {r.status_code}, coba server lain")
            except httpx.HTTPError as e:
                log(f"  {url}: {e}, coba server lain")
            r = None
        if r is None:
            raise RuntimeError("Semua server Overpass gagal; coba lagi beberapa menit lagi")
        MENTAH.mkdir(parents=True, exist_ok=True)
        path.write_text(r.text, encoding="utf-8")
    el = json.loads(path.read_text(encoding="utf-8"))["elements"]
    return pd.DataFrame([{"lon": e["lon"], "lat": e["lat"], "nama": e.get("tags", {}).get("name", ""),
                          "place": e.get("tags", {}).get("place", "")} for e in el])


def _norm(s: str) -> str:
    s = re.sub(r"^(kampung|kelurahan|desa)\s+", "", str(s).lower().strip())
    return re.sub(r"[^a-z0-9]", "", s)


# ------------------------------------------------------------------ geometri
def _luas_km2(mp: list) -> float:
    tot = 0.0
    for poly in mp:
        for k, ring in enumerate(poly):
            r = np.asarray(ring)
            x = np.radians(r[:, 0]) * 6371.0 * math.cos(math.radians(r[:, 1].mean()))
            y = np.radians(r[:, 1]) * 6371.0
            a = abs(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
            tot += -a if k else a
    return tot


def _jarak_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def _feature(props: dict, mp: list, nd: int = 4) -> dict:
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "MultiPolygon",
                         "coordinates": [[[[round(a, nd), round(b, nd)] for a, b in ring] for ring in poly] for poly in mp]}}


# ------------------------------------------------------------------ perakitan
def bangun(kuartal: int = 4, ambang_4g_mbps: float = 5.0, radius_sinyal_km: float = 2.0,
           pantai_km: float = 3.0, keluar: Path | None = None, timpa: bool = False, log=print) -> dict:
    from scipy import ndimage

    keluar = keluar or DATA_DIR / "kampung.csv"
    if keluar.exists() and not timpa:
        raise FileExistsError(f"{keluar} sudah ada. Pakai --timpa untuk menggantinya (salinan lama disimpan sebagai .bak).")
    region = load_region()
    kab_cfg = {k["kode"]: k for k in region["kabupaten"]}
    kode_ke_id = {k["kode"]: k["id"] for k in region["kabupaten"]}

    log("1/5 Kode & batas wilayah Kepmendagri 2025")
    wil = _unduh(GH.format(repo="wilayah", path="wilayah.sql"), MENTAH / "wilayah.sql", log).read_text(encoding="utf-8")
    nama = dict(re.findall(r"\('(91\.[0-9.]+)','([^']*)'\)", wil))
    pend = {k: int(t) for k, t in re.findall(r"\('(91\.[0-9]{2})','[^']*','[0-9]+','[0-9]+','([0-9]+)'\)",
            _unduh(GH.format(repo="wilayah", path="wilayah_penduduk.sql"), MENTAH / "wilayah_penduduk.sql", log)
            .read_text(encoding="utf-8"))}
    kab_poly = _poligon_sql(_unduh(GH.format(repo="wilayah_boundaries", path="kab/wilayah_boundaries_kab_91.sql"),
                                   MENTAH / "batas_kab_91.sql", log))
    kec_poly = _poligon_sql(_unduh(GH.format(repo="wilayah_boundaries", path="kec/wilayah_boundaries_kec_91.sql"),
                                   MENTAH / "batas_kec_91.sql", log))
    kel = []
    for kk in kab_cfg:
        kel += _poligon_sql(_unduh(GH.format(repo="wilayah_boundaries", path=f"kel/91/wilayah_boundaries_kel_{kk}.sql"),
                                   MENTAH / f"batas_kel_{kk}.sql", log))
    kel = [r for r in kel if r[0][:5] in kab_cfg]
    resmi = sorted(k for k in nama if len(k) == 13 and k[:5] in kab_cfg)
    tanpa_poligon = sorted(set(resmi) - {r[0] for r in kel})

    log("2/5 Sebaran penduduk WorldPop 100 m")
    wp, tf = _baca_worldpop(_unduh(WORLDPOP, MENTAH / "worldpop_idn_2020_100m.tif", log))
    halus = ndimage.uniform_filter(wp, size=5) * 25          # penduduk dalam jendela ±250 m
    rows = []
    for kode, nm, lat, lng, mp in kel:
        r = {"kode_kampung": kode, "kode_distrik": kode[:8], "kode_kab": kode[:5], "kampung": nama.get(kode, nm),
             "kelurahan": kode[9] == "1", "luas_km2": _luas_km2(mp), "lon_tengah": lng, "lat_tengah": lat, "wp": 0.0}
        m = _mask_poligon(mp, tf, wp.shape)
        if m is not None and m[2].any():
            si, sj, mask = m
            r["wp"] = float(wp[si, sj][mask].sum())
            h = np.where(mask, halus[si, sj], -1)
            if h.max() > 0:                                     # titik permukiman: piksel terpadat di dalam kampung
                i, j = np.unravel_index(np.argmax(h), h.shape)
                r["lon"] = tf[0] + (sj.start + j + 0.5) * tf[2]
                r["lat"] = tf[1] - (si.start + i + 0.5) * tf[3]
        rows.append(r)
    df = pd.DataFrame(rows)
    df["titik"] = np.where(df["lon"].notna(), "worldpop", "")
    # Kampung tanpa permukiman WorldPop: pakai titik OSM di dalam poligon (nama cocok didahulukan), lalu titik tengah.
    from matplotlib.path import Path as MPath

    osm = _osm_tempat(log)
    osm_xy = osm[["lon", "lat"]].to_numpy()
    rank = {"village": 0, "town": 0, "city": 0, "suburb": 1, "hamlet": 2, "neighbourhood": 3, "isolated_dwelling": 4}
    for idx in df.index[df["titik"] == ""]:
        mp = kel[idx][4]
        inside = np.zeros(len(osm), bool)
        for poly in mp:
            inside |= MPath(np.asarray(poly[0])).contains_points(osm_xy)
        if inside.any():
            cand = osm[inside].copy()
            cand["skor"] = [(_norm(n) != _norm(df.at[idx, "kampung"])) * 10 + rank.get(p, 5)
                            for n, p in zip(cand["nama"], cand["place"])]
            best = cand.sort_values("skor").iloc[0]
            df.loc[idx, ["lon", "lat", "titik"]] = [best["lon"], best["lat"], "osm"]
    df.loc[df["titik"] == "", "titik"] = "tengah_poligon"
    df["lon"] = df["lon"].fillna(df["lon_tengah"])
    df["lat"] = df["lat"].fillna(df["lat_tengah"])
    # Kampung resmi tanpa permukiman terdeteksi WorldPop tetap berpenghuni: beri lantai kecil sebelum diskalakan.
    lantai = 0.25 * df.groupby("kode_kab")["wp"].transform(lambda s: s[s > 0].median() if (s > 0).any() else 1.0)
    df["wp_nol"] = df["wp"] <= 0
    df["bobot"] = np.where(df["wp_nol"], lantai, df["wp"])
    df["distrik"] = df["kode_distrik"].map(nama)
    df["kabupaten_id"] = df["kode_kab"].map(kode_ke_id)
    # Total per distrik dari BPS (Kecamatan/Distrik Dalam Angka) bila diisi; sisanya dibagi menurut WorldPop.
    bps = {}
    pd_csv = DATA_DIR / "penduduk_distrik.csv"
    if pd_csv.exists():
        t = pd.read_csv(pd_csv).dropna(subset=["penduduk"])
        bps = {(a, str(b).strip().lower()): float(c) for a, b, c in zip(t["kabupaten_id"], t["distrik"], t["penduduk"])}
        log(f"  {len(bps)} total distrik dari {pd_csv.name}")
    df["penduduk"] = 0
    df["sumber_penduduk"] = "worldpop"
    for kk, sub in df.groupby("kode_kab"):
        tot = pend.get(kk) or kab_cfg[kk]["penduduk"]
        kunci = list(zip(sub["kabupaten_id"], sub["distrik"].str.lower()))
        tetap = np.array([bps.get(k, np.nan) for k in kunci])
        ada = ~np.isnan(tetap)
        w = sub["bobot"].to_numpy()
        hasil = np.zeros(len(sub))
        if ada.any():
            dsub = sub["kode_distrik"].to_numpy()
            for dk in np.unique(dsub[ada]):
                m = dsub == dk
                hasil[m] = w[m] / w[m].sum() * tetap[m][0]
            sisa = max(tot - sum({d: t for d, t in zip(dsub[ada], tetap[ada])}.values()), 0)
            if (~ada).any():
                hasil[~ada] = w[~ada] / w[~ada].sum() * sisa
            df.loc[sub.index[ada], "sumber_penduduk"] = "bps_distrik"
        else:
            hasil = w / w.sum() * tot
        df.loc[sub.index, "penduduk"] = np.round(hasil).astype(int).clip(min=1)
    contoh = df.groupby(["kabupaten_id", "distrik"], sort=False)["penduduk"].sum().reset_index()
    contoh["penduduk_perkiraan_worldpop"] = contoh.pop("penduduk")
    contoh["penduduk"] = ""
    contoh["sumber"] = ""
    contoh.to_csv(DATA_DIR / "penduduk_distrik_contoh.csv", index=False)

    log("3/5 Elevasi dan jarak ke laut")
    elev, ke_piksel, km_px = _dem(log=log)
    dlaut = _jarak_laut(elev)
    pi, pj = ke_piksel(df["lon"].to_numpy(), df["lat"].to_numpy())
    pi, pj = np.round(pi).astype(int), np.round(pj).astype(int)
    df["elevasi_m"] = np.round(elev[pi, pj]).astype(int)
    df["jarak_laut_km"] = np.round(dlaut[pi, pj] * km_px * np.cos(np.radians(df["lat"])), 2)
    tepi = []
    for _, _, _, _, mp in kel:     # jarak terdekat tepi poligon kampung ke laut (kampung yang wilayahnya menyentuh pantai)
        v = np.concatenate([np.asarray(p[0]) for p in mp])
        a, b = ke_piksel(v[:, 0], v[:, 1])
        tepi.append(float(dlaut[np.round(a).astype(int), np.round(b).astype(int)].min()) * km_px)
    df["tepi_laut_km"] = np.round(tepi, 2)

    log("4/5 Sinyal seluler (proksi Ookla)")
    ok = _ookla(kuartal, log)
    ok = ok.groupby("quadkey").apply(lambda g: pd.Series({
        "lon": g["lon"].iat[0], "lat": g["lat"].iat[0], "tests": g["tests"].sum(),
        "mbps": np.average(g["avg_d_kbps"], weights=g["tests"]) / 1000}), include_groups=False).reset_index()
    from scipy.spatial import cKDTree

    tree = cKDTree(np.c_[ok["lon"], ok["lat"]])
    rad_deg = radius_sinyal_km / 111.0
    uji, cepat, maks = [], [], []
    for lo, la in zip(df["lon"], df["lat"]):
        ids = tree.query_ball_point([lo, la], rad_deg)
        sub = ok.iloc[ids]
        uji.append(int(sub["tests"].sum()))
        maks.append(round(float(sub["mbps"].max()), 1) if len(sub) else 0.0)
        cepat.append(bool((sub["mbps"] >= ambang_4g_mbps).any()))
    df["ookla_uji"], df["ookla_mbps_maks"] = uji, maks
    df["sinyal"] = np.where(cepat, "4g", np.where(np.asarray(uji) > 0, "lemah", "tidak"))

    log("5/5 Tipe wilayah, pusat distrik, berkas keluaran")
    df["kepadatan"] = df["penduduk"] / df["luas_km2"].clip(lower=0.05)
    kota = df["kelurahan"] | ((df["kabupaten_id"] == "kota_jayapura") & (df["kepadatan"] >= 250))
    df["tipe_kampung"] = np.select(
        [kota, df["kabupaten_id"].isin(PULAU),
         (df["jarak_laut_km"] <= pantai_km) | ((df["titik"] != "worldpop") & (df["tepi_laut_km"] <= 0.5))], ["kota", "kepulauan", "pesisir"], "pedalaman")
    # Tipe distrik = tipe kampung dengan penduduk terbanyak; population.py memakai tipe distrik.
    td = df.groupby(["kode_distrik", "tipe_kampung"])["penduduk"].sum().reset_index() \
        .sort_values("penduduk").drop_duplicates("kode_distrik", keep="last").set_index("kode_distrik")["tipe_kampung"]
    df["tipe"] = df["kode_distrik"].map(td)
    df["pusat_distrik"] = (df.groupby("kode_distrik")["penduduk"].transform("max") == df["penduduk"]) \
        & ~df.duplicated(["kode_distrik", "penduduk"])
    df = df.sort_values("kode_kampung").reset_index(drop=True)

    kolom = ["kabupaten_id", "distrik", "kampung", "lon", "lat", "penduduk", "sinyal", "tipe", "pusat_distrik",
             "kode_kampung", "kode_distrik", "tipe_kampung", "kelurahan", "luas_km2", "elevasi_m", "jarak_laut_km",
             "tepi_laut_km", "titik", "sumber_penduduk",
             "ookla_uji", "ookla_mbps_maks", "wp_nol"]
    out = df[kolom].copy()
    out["lon"], out["lat"] = out["lon"].round(5), out["lat"].round(5)
    out["luas_km2"] = out["luas_km2"].round(2)
    for c in ("pusat_distrik", "kelurahan", "wp_nol"):
        out[c] = out[c].astype(int)
    if keluar.exists():
        keluar.replace(keluar.with_suffix(".csv.bak"))
    out.to_csv(keluar, index=False)

    dist_feats = [_feature({"kode": k, "nama": nama.get(k, nm), "kabupaten_id": kode_ke_id[k[:5]]}, mp)
                  for k, nm, _, _, mp in kec_poly if k[:5] in kab_cfg]
    kab_feats = [_feature({"kode": k, "nama": nm, "kabupaten_id": kode_ke_id[k]}, mp)
                 for k, nm, _, _, mp in kab_poly if k in kab_cfg]
    for fn, feats in (("batas_distrik.geojson", dist_feats), ("batas_kabupaten.geojson", kab_feats)):
        (DATA_DIR / fn).write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")),
                                   encoding="utf-8")

    ringkas = []
    for k in region["kabupaten"]:
        s = out[out["kabupaten_id"] == k["id"]]
        ringkas.append({"kabupaten_id": k["id"], "distrik": int(s["distrik"].nunique()), "kampung": len(s),
                        "penduduk": int(s["penduduk"].sum()),
                        "sinyal_4g": round(float((s["sinyal"] == "4g").mean()), 2),
                        "sinyal_lemah": round(float((s["sinyal"] == "lemah").mean()), 2),
                        "sinyal_perkiraan_lama": k.get("sinyal"),
                        "tipe_distrik": s.drop_duplicates("distrik")["tipe"].value_counts().to_dict(),
                        "kampung_tanpa_permukiman_worldpop": int(s["wp_nol"].sum()),
                        "titik": s["titik"].value_counts().to_dict()})
    meta = {
        "dibangun": date.today().isoformat(),
        "sumber": {
            "wilayah": "Kepmendagri No 300.2.2-2430 Tahun 2025 via github.com/cahyadsn/wilayah (MIT)",
            "batas": "github.com/cahyadsn/wilayah_boundaries (MIT), poligon kabupaten/distrik/kampung",
            "penduduk_kabupaten": "Dukcapil, Kepmendagri No 300.2.2-2138 Tahun 2025 (wilayah_penduduk.sql)",
            "sebaran_penduduk": "WorldPop 2020 constrained 100 m, UN-adjusted (CC BY 4.0)",
            "sinyal": f"Ookla Open Data, ubin seluler {kuartal} kuartal terakhir (CC BY-NC-SA 4.0)",
            "elevasi": "AWS Terrain Tiles / Terrarium z9 (SRTM dkk., domain publik)",
        },
        "aturan": {
            "penduduk": "total distrik dari data/penduduk_distrik.csv (BPS) bila diisi; selain itu total kabupaten Dukcapil dibagi ke kampung sebanding penduduk WorldPop di dalam poligon kampung; "
                        "kampung tanpa permukiman WorldPop diberi bobot 25% median kampung sekabupaten",
            "titik_kampung": "piksel terpadat (jendela 500 m) di dalam poligon kampung; bila WorldPop kosong, titik "
                             "place OSM di dalam poligon (nama cocok didahulukan); bila tidak ada, titik tengah poligon",
            "sinyal": f"4g bila ada ubin Ookla ≥ {ambang_4g_mbps} Mbps dalam {radius_sinyal_km} km dari titik kampung; "
                      f"lemah bila ada uji tetapi lebih lambat; tidak bila tidak ada uji sama sekali",
            "tipe_kampung": f"kota = kelurahan, atau kampung Kota Jayapura dengan kepadatan ≥ 250/km²; kepulauan = kab "
                            f"{sorted(PULAU)}; pesisir = titik kampung ≤ {pantai_km} km dari laut, atau (titik bukan dari WorldPop dan tepi "
                            f"poligon ≤ 0,5 km dari laut); sisanya pedalaman",
            "tipe_distrik": "tipe kampung dengan penduduk terbanyak di distrik itu",
            "pusat_distrik": "kampung berpenduduk terbanyak di distrik (bukan ibu kota resmi)",
        },
        "pemeriksaan": {"kampung_resmi": len(resmi), "kampung_berpoligon": len(out),
                        "kampung_resmi_tanpa_poligon": [f"{k} {nama[k]}" for k in tanpa_poligon],
                        "penduduk_worldpop_tertangkap_poligon": round(float(df["wp"].sum()), 0)},
        "kabupaten": ringkas,
    }
    (DATA_DIR / "geodata_sumber.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
