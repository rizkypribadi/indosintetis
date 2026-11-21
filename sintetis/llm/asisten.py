"""Asisten LLM untuk peneliti (di luar simulasi): menyusun skenario dari deskripsi bebas dan menulis
laporan naratif dari hasil putaran tunggal atau Monte Carlo.

Berbeda dengan agen kunci, panggilan di sini tidak memengaruhi dinamika model dan tidak di-cache.
Angka di laporan selalu dihitung di Python dulu (fungsi ringkas_*); LLM hanya menafsirkan.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from .. import KANAL, STATUS, TIPE_WILAYAH, A, I, S
from ..config import CONFIG_DIR, FAKTOR_IDENTITAS, HARI, JENIS_GRUP, load_yaml, validate_scenario
from .orchestrator import _extract_json, resolve_api_key
from .prompt import GLOSARIUM

PENYEDIA_URUT = ("sumopod", "deepseek")
ARTI_STATUS = {"S": "rentan (belum dengar)", "E": "terpapar (dengar, belum bersikap)", "I": "penyebar",
               "Z": "skeptis/meluruskan", "A": "siaga aktif (berpotensi tindakan fisik, mis. jaga kampung)"}


# ------------------------------------------------------------------ pemanggilan LLM
def pilih_penyedia(penyedia: str | None) -> str:
    """Pakai penyedia yang diminta bila berupa LLM; untuk 'abm'/kosong pilih yang kuncinya tersedia."""
    cfg = load_yaml(CONFIG_DIR / "llm.yaml")["penyedia"]
    if penyedia in cfg:
        return penyedia
    for p in PENYEDIA_URUT:
        if resolve_api_key(cfg[p].get("env_kunci")):
            return p
    raise RuntimeError("Tidak ada kunci API LLM (SUMOPOD_API_KEY / DEEPSEEK_API_KEY / 'LLM API Key.txt'). "
                       "Pilih Ollama lokal atau mock.")


def chat(system: str, user: str, penyedia: str, max_tokens: int = 8000, json_mode: bool = False) -> tuple[str, dict]:
    cfg = load_yaml(CONFIG_DIR / "llm.yaml")
    pcfg = cfg["penyedia"][penyedia]
    from openai import OpenAI

    env = pcfg.get("env_kunci")
    key = resolve_api_key(env) if env else "ollama"
    if not key:
        raise RuntimeError(f"Kunci API {penyedia} tidak ditemukan ({env} atau 'LLM API Key.txt').")
    client = OpenAI(api_key=key, base_url=pcfg["base_url"], timeout=240, max_retries=2)
    resp = client.chat.completions.create(
        model=pcfg["model"], messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.4, max_tokens=max_tokens, **({"response_format": {"type": "json_object"}} if json_mode else {}))
    choice = resp.choices[0]
    text = re.sub(r"<think>.*?</think>", "", choice.message.content or "", flags=re.S).strip()
    u = resp.usage
    info = {"penyedia": penyedia, "model": pcfg["model"], "terpotong": choice.finish_reason == "length",
            "token_masuk": getattr(u, "prompt_tokens", 0) or 0, "token_keluar": getattr(u, "completion_tokens", 0) or 0}
    t = pcfg["tarif"]
    info["biaya_usd"] = round((info["token_masuk"] * t["input_cache_miss"] + info["token_keluar"] * t["output"]) / 1e6, 4)
    if not text:
        if info["terpotong"]:
            raise TokenHabis(f"LLM tidak mengembalikan teks karena batas {max_tokens} token habis untuk penalaran. "
                             "Coba lagi.", info)
        raise RuntimeError("LLM tidak mengembalikan teks. Coba lagi.")
    return text, info


class TokenHabis(RuntimeError):
    """Model penalaran menghabiskan seluruh batas token untuk berpikir sebelum menulis jawaban."""

    def __init__(self, pesan: str, info: dict):
        super().__init__(pesan)
        self.info = info


# ------------------------------------------------------------------ penyusun skenario
SISTEM_SKENARIO = """Kamu membantu tim riset menyusun skenario HIPOTETIS penyebaran isu/hoaks untuk simulasi
berbasis agen di Provinsi Papua. Semua warga di model fiktif. Ubah deskripsi bebas pengguna menjadi satu
objek JSON skenario. Topik isu boleh apa saja (kesehatan, bencana, pemilu, harga, keamanan, dll.).

Aturan isi:
- isu.teks adalah pesan berantai seperti yang beredar di grup WA/FB (maks. 400 karakter). Untuk warga Papua
  pakai Melayu Papua sehari-hari kecuali pengguna meminta lain (glosarium: {glosarium}; hindari
  "beta/pung/basudara" yang khas Ambon). Fiktif, tanpa nama orang sungguhan.
- Jangan menjadikan suku, etnis, agama, ras, atau kelompok tertentu sebagai pelaku atau sasaran kebencian.
  Bila deskripsi pengguna begitu, ganti dengan "orang tak dikenal" dan jelaskan di "catatan".
- intensitas_emosi 0..1 (seberapa menakutkan/memancing emosi pesan).
- alasan_percaya: frasa pendek mengapa warga yang baik pun bisa percaya dan meneruskan isu INI, diawali
  "karena", mis. "karena takut keluarga jatuh sakit" (maks. 120 karakter, tanpa menyebut kelompok).
- ajakan_tindakan true bila pesan mengajak warga berjaga, menghadang, atau bertindak fisik.
- faktor_identitas: pengali kerentanan (0.5..3) hanya untuk kelompok yang paling terdampak, kunci dari:
  {faktor}. Kosongkan bila tidak ada.
- tanam = titik awal isu ditanam. kanal "wa" memakai jenis_grup ({jenis}) dan jumlah_grup;
  kanal "fb" memakai jenis_grup "info" dan jumlah_grup, atau akun_medsos (jumlah akun lokal yang mengunggah);
  kanal "tatap_muka" memakai "jumlah" (orang). t_jam = jam setelah T+0 (>= 0).
- kabupaten WAJIB berupa id dari daftar wilayah; distrik (opsional) harus nama persis dari daftar kabupaten itu.
  Bila tempat yang disebut pengguna tidak ada di daftar, pilih wilayah yang paling mirip dan jelaskan di "catatan".
- mulai.hari salah satu dari {hari}; ingat ibadah Minggu pagi dan hari pasar memengaruhi tatap muka.
- provokator (opsional) = aktor yang SENGAJA menyebar, hanya bila pengguna menyebut provokator, buzzer, akun
  bayaran, atau penggerak massa. Tiap item: jenis "buzzer" (kanal "fb" atau "wa" dengan jenis_grup, akun 1..100,
  grup_per_akun, terkoordinasi true/false, intensitas 0.5..5, serang_klarifikasi, pulih_jam opsional) atau
  "lokal" (jumlah 1..20, ajak_jaga_kampung). Keduanya: kabupaten (id), distrik opsional, mulai_jam, selesai_jam,
  nama (deskriptif, mis. "Jaringan akun FB anonim Wamena"), contoh_pesan dan contoh_serangan (lihat aturan pesan).
  Jangan mengaitkan provokator dengan suku, agama, atau kelompok tertentu.
{aturan_pesan}- Bila deskripsi tidak menyebut sesuatu, pilih asumsi wajar dan tulis di "catatan".

Daftar wilayah (id kabupaten: nama — tipe — distrik):
{wilayah}

Kunci JSON: nama, kode (snake_case maks. 40 huruf), mulai {{hari, jam}}, langkah_menit (15|30|60),
durasi_jam (6..168), isu {{teks, emosi, intensitas_emosi, alasan_percaya, faktor_identitas, ajakan_tindakan}},
tanam [ ... ], provokator [ ... ] (opsional), komentar_contoh [2-4 string komentar warga], catatan (asumsi yang kamu buat, bahasa Indonesia).
Jawab hanya JSON."""


ATURAN_PESAN = """- contoh_pesan: 2-3 contoh unggahan ILUSTRATIF (maks. 300 karakter) per provokator, sesuai kanal dan perannya:
  buzzer FB = unggahan/komentar akun anonim yang meyakinkan ("su banyak yang lihat sendiri"), buzzer WA =
  pesan terusan berantai, lokal = ucapan langsung di pasar/pangkalan/kampung (bila ajak_jaga_kampung, berisi
  ajakan berjaga). Urutkan dari awal ke akhir kampanye (eskalasi wajar). Melayu Papua sehari-hari
  ("kitong", bukan "katong"; hindari "beta/pung/basudara" yang khas Ambon). Jangan sekadar menyalin isu.teks.
- contoh_serangan: satu pesan (maks. 300 karakter) yang menyerang klarifikasi resmi, mis. "jang percaya,
  itu pemerintah tutup-tutupi", WAJIB diisi bila serang_klarifikasi true (bawaan true), kosong bila false.
- Pesan fiktif: tanpa nama orang/akun/lembaga sungguhan, tanpa nomor telepon atau tautan, tanpa menyebut
  suku/agama/ras sebagai pelaku atau sasaran, tanpa instruksi kekerasan terperinci atau senjata."""


def _daftar_wilayah(geo) -> str:
    rows = []
    for k, kid in enumerate(geo.kab_id):
        ds = [geo.distrik_nama[d] for d in np.flatnonzero(geo.distrik_kab == k)]
        tipe = sorted({TIPE_WILAYAH[geo.distrik_tipe[d]] for d in np.flatnonzero(geo.distrik_kab == k)})
        rows.append(f"- {kid}: {geo.kab_nama[k]} — {'/'.join(tipe)} — {', '.join(ds)}")
    return "\n".join(rows)


def susun_skenario(deskripsi: str, geo, penyedia: str | None = None) -> tuple[dict, dict]:
    deskripsi = deskripsi.strip()
    if len(deskripsi) < 10:
        raise ValueError("Deskripsi skenario terlalu pendek.")
    p = pilih_penyedia(penyedia)
    if p == "mock":
        raw = {"nama": deskripsi[:60], "isu": {"teks": deskripsi[:400]}, "tanam": [{}],
               "catatan": "Mode mock: deskripsi dipakai langsung sebagai teks isu, sisanya nilai bawaan."}
        info = {"penyedia": "mock", "model": "mock", "biaya_usd": 0}
    else:
        system = SISTEM_SKENARIO.format(glosarium=GLOSARIUM, faktor=", ".join(FAKTOR_IDENTITAS), jenis=", ".join(JENIS_GRUP),
                                        hari=", ".join(HARI), wilayah=_daftar_wilayah(geo), aturan_pesan=ATURAN_PESAN)
        user = f"Deskripsi skenario dari pengguna:\n{deskripsi}"
        biaya = 0.0
        for batas in (16000, 32000):          # model penalaran: token berpikir ikut dihitung; ulang sekali bila habis
            try:
                text, info = chat(system, user, p, max_tokens=batas, json_mode=True)
                break
            except TokenHabis as e:
                biaya += e.info.get("biaya_usd", 0)
                if batas == 32000:
                    raise
        info["biaya_usd"] = round(info["biaya_usd"] + biaya, 4)
        raw = json.loads(_extract_json(text))
    sc = rapikan_skenario(raw, geo)
    sc["deskripsi"] = deskripsi
    return sc, info


def rapikan_skenario(raw: dict, geo) -> dict:
    """Paksa keluaran LLM ke bentuk yang lolos validate_scenario dan cocok dengan geografi."""
    def num(v, lo, hi, default):
        try:
            return min(max(float(v), lo), hi)
        except (TypeError, ValueError):
            return default

    mulai = raw.get("mulai") or {}
    isu = raw.get("isu") or {}
    nama = str(raw.get("nama") or "Skenario baru").strip()[:120]
    kode = re.sub(r"[^a-z0-9_]+", "_", str(raw.get("kode") or nama).lower()).strip("_")[:40] or "skenario_baru"
    menit = int(raw.get("langkah_menit") or 30)
    sc = {
        "nama": nama, "kode": kode,
        "mulai": {"hari": mulai.get("hari") if mulai.get("hari") in HARI else "Sabtu",
                  "jam": int(num(mulai.get("jam"), 0, 23, 7))},
        "langkah_menit": menit if menit in (15, 30, 60) else 30,
        "durasi_jam": int(num(raw.get("durasi_jam"), 6, 168, 48)),
        "isu": {
            "teks": str(isu.get("teks") or nama).strip()[:600],
            "emosi": str(isu.get("emosi") or "takut")[:30],
            "intensitas_emosi": round(num(isu.get("intensitas_emosi"), 0, 1, 0.7), 2),
            "faktor_identitas": {k: round(num(v, 0.2, 3, 1), 2) for k, v in (isu.get("faktor_identitas") or {}).items()
                                 if k in FAKTOR_IDENTITAS},
            "ajakan_tindakan": bool(isu.get("ajakan_tindakan", False)),
            **({"alasan_percaya": str(isu["alasan_percaya"]).strip()[:160]} if isu.get("alasan_percaya") else {}),
        },
        "tanam": [],
    }
    kab_of = {**{_norm(n): geo.kab_id[i] for i, n in enumerate(geo.kab_nama)}, **{k: k for k in geo.kab_id}}
    distrik_of: dict[str, list[str]] = {}
    for d, n in enumerate(geo.distrik_nama):
        distrik_of.setdefault(n.lower(), []).append(geo.kab_id[geo.distrik_kab[d]])
    for tn in raw.get("tanam") or []:
        if not isinstance(tn, dict):
            continue
        kab, cand = _norm(tn.get("kabupaten")), distrik_of.get(str(tn.get("distrik") or "").lower(), [])
        if kab not in kab_of or (cand and kab_of[kab] not in cand):
            kab = cand[0] if len(cand) == 1 else next((k for n, k in kab_of.items() if kab and kab in n), kab)
        kab = kab_of.get(kab, geo.kab_id[0])
        kanal = tn.get("kanal") if tn.get("kanal") in ("wa", "fb", "tatap_muka") else "wa"
        out = {"t_jam": round(num(tn.get("t_jam"), 0, sc["durasi_jam"], 0), 1), "kanal": kanal, "kabupaten": kab}
        ki = geo.kab_id.index(kab)
        ds = {geo.distrik_nama[d].lower(): geo.distrik_nama[d] for d in np.flatnonzero(geo.distrik_kab == ki)}
        if str(tn.get("distrik") or "").lower() in ds:
            out["distrik"] = ds[str(tn["distrik"]).lower()]
        if kanal == "tatap_muka":
            out["jumlah"] = int(num(tn.get("jumlah"), 1, 200, 5))
        elif kanal == "fb" and tn.get("akun_medsos"):
            out["akun_medsos"] = int(num(tn.get("akun_medsos"), 1, 20, 2))
        else:
            jg = tn.get("jenis_grup")
            out["jenis_grup"] = jg if jg in JENIS_GRUP else ("info" if kanal == "fb" else "keluarga")
            out["jumlah_grup"] = int(num(tn.get("jumlah_grup"), 1, 50, 3))
        sc["tanam"].append(out)
    if not sc["tanam"]:
        sc["tanam"] = [{"t_jam": 0, "kanal": "wa", "jenis_grup": "keluarga", "kabupaten": geo.kab_id[0], "jumlah_grup": 3}]
    prov = []
    for pv in raw.get("provokator") or []:
        if not isinstance(pv, dict) or pv.get("jenis") not in ("buzzer", "lokal"):
            continue
        kab = kab_of.get(_norm(pv.get("kabupaten")))
        out = {"jenis": pv["jenis"], **({"nama": str(pv["nama"])[:60]} if pv.get("nama") else {}),
               **({"kabupaten": kab} if kab else {})}
        if kab and pv.get("distrik"):
            ki = geo.kab_id.index(kab)
            ds = {geo.distrik_nama[d].lower(): geo.distrik_nama[d] for d in np.flatnonzero(geo.distrik_kab == ki)}
            if str(pv["distrik"]).lower() in ds:
                out["distrik"] = ds[str(pv["distrik"]).lower()]
        mulai = round(num(pv.get("mulai_jam"), 0, sc["durasi_jam"] - 1, 0), 1)
        out["mulai_jam"] = mulai
        if pv.get("selesai_jam") is not None:
            out["selesai_jam"] = round(num(pv.get("selesai_jam"), mulai + 1, sc["durasi_jam"] + 1, sc["durasi_jam"]), 1)
        out["serang_klarifikasi"] = bool(pv.get("serang_klarifikasi", True))
        if pv["jenis"] == "buzzer":
            kanal = pv.get("kanal") if pv.get("kanal") in ("fb", "wa") else "fb"
            out["kanal"] = kanal
            if kanal == "wa":
                jg = pv.get("jenis_grup")
                out["jenis_grup"] = jg if jg in ("sekolah", "keluarga", "jemaat", "marga") else "keluarga"
            out["akun"] = int(num(pv.get("akun"), 1, 100, 10))
            out["grup_per_akun"] = int(num(pv.get("grup_per_akun"), 1, 20, 3))
            out["terkoordinasi"] = bool(pv.get("terkoordinasi", True))
            out["intensitas"] = round(num(pv.get("intensitas"), 0.5, 5, 2), 2)
            if pv.get("pulih_jam") is not None:
                out["pulih_jam"] = round(num(pv.get("pulih_jam"), 1, 72, 12), 1)
        else:
            out["jumlah"] = int(num(pv.get("jumlah"), 1, 20, 3))
            out["ajak_jaga_kampung"] = bool(pv.get("ajak_jaga_kampung", True))
        out.update(_rapikan_pesan(pv, out["serang_klarifikasi"]))
        prov.append(out)
    if prov:
        sc["provokator"] = prov
    kom = [str(k.get("teks") or next(iter(k.values()), "")) if isinstance(k, dict) else str(k)
           for k in (raw.get("komentar_contoh") or [])]
    kom = [k.strip()[:200] for k in kom if k.strip()][:4]
    if kom:
        sc["komentar_contoh"] = kom
    if raw.get("catatan"):
        sc["catatan"] = str(raw["catatan"])[:1500]
    validate_scenario(sc)
    return sc


def _teks_pesan(x) -> str:
    if isinstance(x, dict):
        x = x.get("teks") or x.get("pesan") or next(iter(x.values()), "")
    return re.sub(r"\s+", " ", str(x or "")).strip()[:300]


def _rapikan_pesan(pv: dict, serang: bool) -> dict:
    cp = pv.get("contoh_pesan")
    cp = [cp] if isinstance(cp, (str, dict)) else (cp or [])
    out = {}
    cp = [t for t in (_teks_pesan(x) for x in cp) if t][:5]
    if cp:
        out["contoh_pesan"] = cp
    cs = _teks_pesan(pv.get("contoh_serangan"))
    if serang and cs:
        out["contoh_serangan"] = cs
    return out


SISTEM_PESAN = """Kamu membantu tim riset membuat contoh pesan ILUSTRATIF untuk aktor penyebar hoaks dalam simulasi
berbasis agen di Provinsi Papua. Contoh ini hanya ditampilkan di catatan peristiwa agar peneliti dan mitra
pemerintah memahami seperti apa kampanye itu terdengar; tidak memengaruhi perhitungan.
{aturan_pesan}
Glosarium Melayu Papua: {glosarium}
Masukan: isu dan daftar provokator (indeks, jenis, kanal, wilayah, waktu). Jawab JSON
{{"provokator": [{{"indeks": 0, "contoh_pesan": [...], "contoh_serangan": "..."}}, ...]}}. Jawab hanya JSON."""


def buat_contoh_pesan(sc: dict, penyedia: str | None = None, timpa: bool = False) -> tuple[dict, dict]:
    """Isi contoh_pesan/contoh_serangan untuk provokator yang belum punya (atau semua bila timpa)."""
    sc = json.loads(json.dumps(sc))
    prov = sc.get("provokator") or []
    target = [i for i, pv in enumerate(prov) if timpa or not pv.get("contoh_pesan")]
    if not target:
        return sc, {"penyedia": "-", "model": "-", "biaya_usd": 0, "diisi": 0}
    p = pilih_penyedia(penyedia)
    if p == "mock":
        for i in target:
            pv = prov[i]
            pv.update(_rapikan_pesan({"contoh_pesan": [f"(contoh {pv['jenis']}) {sc.get('isu', {}).get('teks', '')[:200]}"],
                                      "contoh_serangan": "(contoh) Jang percaya klarifikasi itu, dong tutup-tutupi."},
                                     pv.get("serang_klarifikasi", True)))
        return sc, {"penyedia": "mock", "model": "mock", "biaya_usd": 0, "diisi": len(target)}
    isi = {"isu": sc.get("isu"), "mulai": sc.get("mulai"), "durasi_jam": sc.get("durasi_jam"),
           "provokator": [{"indeks": i, **{k: v for k, v in prov[i].items() if k not in ("contoh_pesan", "contoh_serangan")},
                           "serang_klarifikasi": prov[i].get("serang_klarifikasi", True)} for i in target]}
    system = SISTEM_PESAN.format(aturan_pesan=ATURAN_PESAN, glosarium=GLOSARIUM)
    user = json.dumps(isi, ensure_ascii=False)
    biaya = 0.0
    for batas in (8000, 16000):
        try:
            text, info = chat(system, user, p, max_tokens=batas, json_mode=True)
            break
        except TokenHabis as e:
            biaya += e.info.get("biaya_usd", 0)
            if batas == 16000:
                raise
    info["biaya_usd"] = round(info["biaya_usd"] + biaya, 4)
    raw = json.loads(_extract_json(text))
    n = 0
    for item in raw.get("provokator") or []:
        try:
            i = int(item.get("indeks"))
        except (TypeError, ValueError, AttributeError):
            continue
        if i in target:
            baru = _rapikan_pesan(item, prov[i].get("serang_klarifikasi", True))
            if baru:
                prov[i].pop("contoh_serangan", None)
                prov[i].update(baru)
                n += 1
    info["diisi"] = n
    return sc, info


def _norm(x) -> str:
    x = str(x or "").lower().replace("kabupaten", "kab").replace(".", " ")
    return re.sub(r"[^a-z0-9]+", "_", x).strip("_")


# ------------------------------------------------------------------ ringkasan angka untuk laporan
def _r(x, n=4):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def ringkas_putaran(ts: np.ndarray, risk: np.ndarray, esk: np.ndarray, events: list[dict], manifest: dict,
                    geo, dpop: np.ndarray, dt_h: float, skenario: dict | None, nama_intervensi: str,
                    pernah_dengar: float | None = None, kanal_masuk: np.ndarray | None = None,
                    paparan_provokator: np.ndarray | None = None) -> dict:
    """Fakta ringkas satu putaran. ts: (T+1, D, 5) jumlah agen per status per distrik."""
    ts = ts.astype(np.float64)
    T1 = ts.shape[0]
    total = ts.sum(1)
    N = float(dpop.sum())
    jam = lambda t: _r(t * dt_h, 1)  # noqa: E731

    def metrik(dm: np.ndarray) -> dict:
        s, popn, e = ts[:, dm].sum(1), float(dpop[dm].sum()), esk[dm]
        return {"penduduk_agen": int(popn), "distrik": int(dm.sum()),
                "puncak_penyebar": _r(s[:, I].max() / popn), "jam_puncak_penyebar": jam(int(s[:, I].argmax())),
                "puncak_siaga": _r(s[:, A].max() / popn), "jam_puncak_siaga": jam(int(s[:, A].argmax())),
                "siaga_jam_per_1000": _r(s[:, A].sum() * dt_h / popn * 1000, 1),
                "tidak_lagi_rentan_akhir": _r(1 - s[-1, S] / popn),
                "distrik_eskalasi": int((e >= 0).sum()),
                "jam_eskalasi_pertama": jam(int(e[e >= 0].min())) if (e >= 0).any() else None}

    langkah = sorted({*range(0, T1, max(1, int(round(6 / dt_h)))), T1 - 1})
    deret = [{"jam": jam(t), **{st: _r(total[t, j] / N) for j, st in enumerate(STATUS)}} for t in langkah]
    order = np.argsort(np.where(esk >= 0, esk, 10**9))
    esk_list = [{"distrik": geo.distrik_nama[d], "kabupaten": geo.kab_nama[geo.distrik_kab[d]],
                 "tipe": TIPE_WILAYAH[geo.distrik_tipe[d]], "jam": jam(int(esk[d])),
                 "risiko_maks": _r(risk[:, d].max())} for d in order if esk[d] >= 0][:12]
    siaga_d = ts[:, :, A].max(0) / np.maximum(dpop, 1)
    top_siaga = [{"distrik": geo.distrik_nama[d], "kabupaten": geo.kab_nama[geo.distrik_kab[d]],
                  "tipe": TIPE_WILAYAH[geo.distrik_tipe[d]], "puncak_siaga": _r(siaga_d[d])}
                 for d in np.argsort(-siaga_d)[:8] if siaga_d[d] > 0]

    kunci = [e for e in events if e.get("jenis") == "agen_kunci" and e.get("keputusan")]
    aksi: dict[str, int] = {}
    for e in kunci:
        a = e["keputusan"]["aksi"] + (" + ajak jaga kampung" if e["keputusan"].get("ajakan_jaga_kampung") else "")
        aksi[a] = aksi.get(a, 0) + 1
    contoh, dipakai = [], set()
    for e in kunci:   # satu-dua contoh per jenis aksi, dari awal kejadian
        a = e["keputusan"]["aksi"]
        if sum(1 for c in contoh if c["aksi"] == a) < 2 and e["keputusan"].get("pesan_tambahan") and e["teks"] not in dipakai:
            dipakai.add(e["teks"])
            contoh.append({"waktu": e["waktu"], "agen": e["teks"].split(":")[0], "aksi": a,
                           "pesan": e["keputusan"]["pesan_tambahan"][:280],
                           "alasan": (e["keputusan"].get("alasan_singkat") or "")[:300]})
    lain = [{"waktu": e["waktu"], "jenis": e["jenis"], "teks": e["teks"][:200]}
            for e in events if e.get("jenis") not in ("agen_kunci", "pesan_provokator")][:25]   # contoh pesan: ilustratif

    sc = skenario or {}
    return {
        "intervensi": {"kode": manifest.get("intervensi"), "nama": nama_intervensi},
        "pengaturan": {"agen": manifest.get("agen"), "seed": manifest.get("seed"), "seed_dunia": manifest.get("seed_dunia"),
                       "durasi_jam": jam(T1 - 1), "mode_agen_kunci": (manifest.get("llm") or {}).get("mode"),
                       "model": (manifest.get("llm") or {}).get("model"),
                       "sumber_keputusan_agen_kunci": (manifest.get("llm") or {}).get("hitung")},
        "skenario": {k: sc.get(k) for k in ("nama", "deskripsi", "mulai", "isu", "tanam", "provokator") if k in sc},
        "semua_wilayah": {**metrik(np.ones(len(dpop), bool)),
                          **({"pernah_dengar": _r(pernah_dengar)} if pernah_dengar is not None else {})},
        "per_tipe_wilayah": {t: metrik(geo.distrik_tipe == i) for i, t in enumerate(TIPE_WILAYAH)
                             if (geo.distrik_tipe == i).any()},
        "deret_proporsi_tiap_6_jam": deret,
        "_grafik": _deret_grafik(ts, dpop, geo, dt_h),
        "distrik_eskalasi_terawal": esk_list,
        "distrik_puncak_siaga_tertinggi": top_siaga,
        "agen_kunci": {"jumlah_keputusan": len(kunci), "per_aksi": aksi, "contoh": contoh},
        "peristiwa_lain": lain,
        **({"kanal": _ringkas_kanal(kanal_masuk)} if kanal_masuk is not None else {}),
        **({"provokator": {"ringkasan": manifest.get("provokator"),
                           "porsi_paparan_dari_provokator": _r(float(paparan_provokator.sum())
                                                               / max(float(kanal_masuk[0].sum()), 1.0), 3)}}
           if paparan_provokator is not None and kanal_masuk is not None else {}),
    }


def _deret_grafik(ts: np.ndarray, dpop: np.ndarray, geo, dt_h: float) -> dict:
    """Deret lengkap untuk grafik PDF (tidak dikirim ke LLM: kunci berawalan '_')."""
    step = max(1, ts.shape[0] // 200)
    idx = np.arange(0, ts.shape[0], step)
    tot = ts[idx].sum(1) / float(dpop.sum())
    tipe = {}
    for i, t in enumerate(TIPE_WILAYAH):
        dm = geo.distrik_tipe == i
        if dm.any():
            s = ts[idx][:, dm].sum(1) / float(dpop[dm].sum())
            tipe[t] = {"I": np.round(s[:, I], 5).tolist(), "A": np.round(s[:, A], 5).tolist()}
    return {"jam": np.round(idx * dt_h, 2).tolist(), "proporsi": np.round(tot, 5).tolist(), "tipe": tipe}


def tanpa_grafik(obj):
    """Buang kunci berawalan '_' (data grafik) sebelum fakta dikirim ke LLM."""
    if isinstance(obj, dict):
        return {k: tanpa_grafik(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [tanpa_grafik(v) for v in obj]
    return obj


def _ringkas_kanal(km: np.ndarray) -> dict:
    """Porsi kanal: dari mana warga pertama mendengar hoaks, dan lewat mana koreksi sampai. km: (2, tipe, 8)."""
    def porsi(v: np.ndarray, idx: tuple[int, ...]) -> dict:
        tot = max(float(v[list(idx)].sum()), 1.0)
        return {KANAL[i]: _r(v[i] / tot, 3) for i in idx}

    out = {"paparan_hoaks_pertama": {"semua": porsi(km[0].sum(0), (0, 1, 2, 3))},
           "koreksi_sampai_lewat": {"semua": porsi(km[1].sum(0), (0, 1, 2, 3, 4, 5, 7))},
           "catatan": "bersikap = memutuskan sendiri tidak percaya; intervensi = klarifikasi resmi/prebunking; "
                      "agen_kunci = keputusan tokoh"}
    for i, t in enumerate(TIPE_WILAYAH):
        if km.shape[1] == len(TIPE_WILAYAH) and km[0, i].sum():
            out["paparan_hoaks_pertama"][t] = porsi(km[0, i], (0, 1, 2, 3))
    return out


def ringkas_mc(d: Path, skenario: dict | None = None) -> dict:
    import pandas as pd

    from ..analysis import METRIK, paired_effect, ranking, recommendations, summarize

    df = pd.read_parquet(d / "hasil.parquet")
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    summ, eff, rank = summarize(df), paired_effect(df), ranking(df, n_boot=500)
    summ["tipe"] = summ["tipe"].astype(str)
    cols = (["tipe", "intervensi", "putaran", "peluang_eskalasi"] + [c for m in METRIK for c in (m, m + "_lo", m + "_hi")]
            + ["paparan_wa", "paparan_fb", "paparan_tatap_muka", "paparan_radio", "paparan_provokator"])
    clean = lambda recs: [{k: (_r(v) if isinstance(v, float) else v) for k, v in r.items()} for r in recs]  # noqa: E731
    sc = man.get("isi_skenario") or skenario or {"nama": man.get("skenario")}
    kurva = d / "kurva_rata2.json"
    grafik = {"kurva": json.loads(kurva.read_text(encoding="utf-8")) if kurva.exists() else None,
              "dt_jam": int(sc.get("langkah_menit", 30)) / 60}
    return {
        "pengaturan": {k: man.get(k) for k in ("agen", "putaran", "seed_dunia", "seed", "mode_llm", "tabel_beku",
                                               "keputusan_agen_kunci", "versi_kode")}
                      | ({"parameter": "tiap seed memakai satu set parameter dari posterior kalibrasi ABC; IK memuat "
                                       "ketidakpastian parameter",
                          "posterior": {k: v for k, v in man["posterior"].items() if k != "indeks_per_seed"}}
                         if man.get("posterior") else {}),
        "skenario": {k: sc.get(k) for k in ("nama", "deskripsi", "mulai", "isu", "tanam", "provokator") if k in sc},
        "intervensi": man["intervensi"],
        "arti_metrik": METRIK | {"peluang_eskalasi": "Peluang minimal satu distrik melewati ambang risiko",
                                 "paparan_provokator": "Porsi paparan hoaks yang datang langsung dari buzzer/provokator"},
        "metrik_utama": "siaga_jam_per_1000 (makin kecil makin baik)",
        "ringkasan_rata2_IK95": clean(summ[[c for c in cols if c in summ]].to_dict("records")),
        "efek_vs_baseline_A_berpasangan": clean(eff.to_dict("records")),
        "peringkat_dan_stabilitas_bootstrap": clean(rank.to_dict("records")),
        "rekomendasi_otomatis": recommendations(rank, eff, man["intervensi"]),
        "_grafik": grafik,
        **({"analisis_sensitivitas": json.loads((d / "sensitivitas.json").read_text(encoding="utf-8"))}
           if (d / "sensitivitas.json").exists() else {}),
        **({"indeks_sobol": {k: v for k, v in json.loads((d / "sobol.json").read_text(encoding="utf-8")).items()
                             if k != "rentang"}}
           if (d / "sobol.json").exists() else {}),
    }


# ------------------------------------------------------------------ penulis laporan
SISTEM_LAPORAN = """Kamu analis kebijakan komunikasi publik. Tulis laporan untuk tim riset dan mitra pemerintah
daerah dari hasil simulasi berbasis agen "Indonesia Sintetis" (model SEIZA + agen kunci LLM) di Provinsi Papua.
Semua agen fiktif; geografi kampung sintetis; parameter belum dikalibrasi penuh.
Arti status agen: {status}.
Sumber kabar: WA, FB, tatap muka (ibadah, pasar, adat, sekolah), radio. Intervensi diuji terhadap baseline A.
Bila skenario punya "provokator": buzzer (akun anonim terkoordinasi) dan provokator lapangan menyebar dengan sengaja,
kebal klarifikasi, dan bisa menyerang klarifikasi; paparan_provokator = porsi paparan yang datang langsung dari mereka.
Bahas perannya tanpa menyebut kelompok sungguhan sebagai pelaku.

Aturan:
- Pakai HANYA angka yang ada di DATA. Jangan mengarang angka, nama, atau kejadian. Proporsi tulis sebagai persen.
- Untuk Monte Carlo: sebut IK 95% dan bedakan efek yang signifikan dari yang belum; sebut stabilitas peringkat.
- Untuk putaran tunggal: tegaskan satu putaran tidak cukup untuk menyimpulkan efek intervensi; sarankan Monte Carlo.
  Bila ada dua putaran (banding), bandingkan keduanya dengan hati-hati.
- Hasil adalah eksplorasi model, bukan prediksi kejadian nyata.
- Analisis per tipe wilayah/distrik, bukan individu. Jangan menyebut suku, etnis, agama, atau kelompok sebagai pelaku.
- Bahasa Indonesia baku yang jelas untuk pembaca non-teknis. Format Markdown: satu judul "# ", bagian "## ",
  daftar "- ", tabel "|" bila membantu. Tanpa blok kode, tanpa HTML.

Struktur:
# <judul singkat>
## Ringkasan eksekutif  (3-5 butir paling penting)
## Skenario dan pengaturan
## Dinamika penyebaran  (kapan memuncak, kanal/wilayah yang menonjol)
## Perbandingan per tipe wilayah{banding}
## Peran agen kunci  (bila ada datanya; kutip 1-3 pesan contoh)
## Rekomendasi draf  (konkret: siapa, lewat kanal apa, kapan, untuk tipe wilayah mana)
## Keterbatasan dan langkah validasi"""


SISTEM_LAPANGAN = """Kamu menulis laporan untuk TIM LAPANGAN: petugas Diskominfo kabupaten, aparat distrik dan kampung,
tokoh gereja, tokoh adat, dan relawan. Mereka sibuk, membaca di HP, dan bukan ahli statistik. Laporan berasal dari
alur otomatis simulasi berbasis agen "Indonesia Sintetis" di Provinsi Papua: eksplorasi, Monte Carlo semua
intervensi, lalu satu putaran contoh yang paling mewakili hasil rata-rata. Semua warga di model fiktif.
Arti status warga: {status}. "Siaga aktif" = warga yang siap bertindak fisik (mis. jaga kampung, menghadang).
"Beban siaga" = jumlah orang-jam siaga aktif per 1.000 penduduk; makin kecil makin aman.

Aturan bahasa:
- Kalimat pendek dan langsung. Bahasa Indonesia sehari-hari yang sopan. Satu gagasan per butir.
- JANGAN memakai istilah teknis: interval kepercayaan, IK, bootstrap, seed, signifikan, baseline, Monte Carlo,
  proporsi, parameter, agen. Ganti dengan: "tanpa tindakan apa pun", "hasil dari banyak kali percobaan",
  "keyakinan tinggi/sedang/rendah" (pakai label keyakinan yang sudah ada di DATA), "warga", "tokoh".
- Bulatkan angka: persen tanpa desimal, jam dibulatkan. Pakai HANYA angka yang ada di DATA; jangan mengarang
  angka, nama, tempat, atau kejadian.
- Selalu sebut intervensi dengan namanya (boleh diikuti kodenya dalam kurung), jangan hanya kodenya.
- Bila keyakinan "rendah", katakan terus terang "belum terbukti lebih baik" dan sarankan tetap memantau.
- Jangan menyebut suku, etnis, agama, atau kelompok sebagai pelaku atau sasaran. Bahas wilayah, bukan individu.
- Ini latihan dan perencanaan, bukan ramalan kejadian nyata.
- Format Markdown: satu judul "# ", bagian "## " PERSIS seperti struktur di bawah, daftar "- ", tabel "|".
  Tanpa blok kode, tanpa HTML.

Struktur (pakai judul bagian persis ini):
# <judul singkat yang menyebut isu>
## Ringkasan untuk pimpinan dan tim  (3-5 butir: bila dibiarkan apa yang terjadi; langkah utama; wilayah paling rawan; kapan harus bergerak)
## Bila isu dibiarkan  (berapa banyak warga mendengar, kapan siaga memuncak, lewat kanal apa isu masuk, per tipe wilayah)
## Langkah paling ampuh per tipe wilayah  (tabel: Tipe wilayah | Utamakan | Perkiraan dampak | Keyakinan | Jangan andalkan)
## Jadwal tindakan  (tabel: Kapan (jam sejak isu muncul) | Apa yang dilakukan | Siapa | Lewat apa; turunkan dari jam puncak dan jam memanas di DATA)
## Distrik yang perlu diawasi lebih dulu  (dari putaran contoh tanpa tindakan: distrik yang paling cepat memanas / siaga tertinggi)
## Contoh jalannya kejadian  (ceritakan putaran contoh: tanpa tindakan vs dengan langkah terbaik, dengan angka dari DATA)
## Peran tokoh masyarakat  (apa yang dilakukan tokoh di simulasi; kutip 1-2 contoh pesan bila ada; pesan yang perlu disiapkan)
## Hal yang perlu diingat  (keterbatasan dalam bahasa sederhana, 3-4 butir)"""


def tulis_laporan(fakta: dict, jenis: str, penyedia: str | None = None, fokus: str = "") -> tuple[str, dict]:
    """jenis: 'putaran' (fakta = {'putaran': [...]}), 'mc', atau 'lapangan' (hasil alur otomatis)."""
    p = pilih_penyedia(penyedia)
    data = json.dumps(tanpa_grafik(fakta), ensure_ascii=False, default=str)
    if p == "mock":
        md = _laporan_lapangan_mock(fakta) if jenis == "lapangan" else _laporan_mock(fakta, jenis)
        return md, {"penyedia": "mock", "model": "mock", "biaya_usd": 0}
    if jenis == "lapangan":
        system = SISTEM_LAPANGAN.format(status="; ".join(f"{k} = {v}" for k, v in ARTI_STATUS.items()))
        user = ((f"Fokus/pertanyaan tambahan dari pengguna: {fokus.strip()[:1000]}\n" if fokus.strip() else "")
                + f"\nDATA (JSON):\n{data}")
        md, info = chat(system, user, p, max_tokens=24000)
        if info["terpotong"]:
            md += "\n\n> Catatan: keluaran AI terpotong karena batas token; bagian akhir mungkin tidak lengkap."
        return md, info
    system = SISTEM_LAPORAN.format(status="; ".join(f"{k} = {v}" for k, v in ARTI_STATUS.items()),
                                   banding=" dan antar-intervensi" if jenis == "mc" else "")
    user = (f"Jenis hasil: {'Monte Carlo (banyak putaran per intervensi)' if jenis == 'mc' else 'putaran tunggal dari dashboard'}\n"
            + (f"Fokus/pertanyaan tambahan dari pengguna: {fokus.strip()[:1000]}\n" if fokus.strip() else "")
            + f"\nDATA (JSON):\n{data}")
    md, info = chat(system, user, p, max_tokens=24000)
    if info["terpotong"]:
        md += "\n\n> Catatan: keluaran LLM terpotong karena batas token; bagian akhir mungkin tidak lengkap."
    return md, info


def _laporan_mock(fakta: dict, jenis: str) -> str:
    md = ["# Laporan (mode mock, tanpa LLM)", "",
          "> Pilih penyedia LLM sungguhan untuk laporan naratif. Di bawah ini fakta yang akan dikirim ke LLM.", ""]
    if jenis == "mc":
        md += ["## Rekomendasi otomatis", ""] + [f"- {r}" for r in fakta["rekomendasi_otomatis"]]
    else:
        for r in fakta["putaran"]:
            s = r["semua_wilayah"]
            md += [f"## Intervensi {r['intervensi']['kode']} — {r['intervensi']['nama']}", "",
                   f"- Puncak penyebar {s['puncak_penyebar'] * 100:.2f}% pada jam {s['jam_puncak_penyebar']}",
                   f"- Puncak siaga aktif {s['puncak_siaga'] * 100:.2f}% pada jam {s['jam_puncak_siaga']}",
                   f"- Siaga aktif {s['siaga_jam_per_1000']} orang-jam per 1.000 penduduk",
                   f"- Distrik melewati ambang risiko: {s['distrik_eskalasi']}", ""]
    return "\n".join(md)


def _pct(x) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def _laporan_lapangan_mock(f: dict) -> str:
    """Laporan lapangan tanpa LLM: disusun langsung dari angka, dengan struktur yang sama."""
    kartu = f.get("kartu_tindakan_per_tipe_wilayah") or {}
    sc = f.get("skenario") or {}
    semua = kartu.get("semua") or next(iter(kartu.values()), {})
    u, b = semua.get("utamakan", {}), semua.get("tanpa_intervensi") or {}
    pc = f.get("putaran_contoh") or {}
    runs = pc.get("putaran") or []
    nm = lambda x: f"{x.get('nama', '')} ({x.get('kode', '')})"  # noqa: E731
    dampak = lambda x: (f"beban siaga turun sekitar {abs(x['efek_persen']):.0f}%" if x.get("menurunkan") and x.get("efek_persen")  # noqa: E731
                        is not None else "belum terbukti menurunkan")
    md = [f"# Panduan tindakan: {sc.get('nama', 'isu')}", "",
          "> Mode tanpa AI (mock): teks disusun langsung dari angka simulasi.", "",
          "## Ringkasan untuk pimpinan dan tim", ""]
    if b:
        md.append(f"- Bila dibiarkan, sekitar {_pct(b.get('warga_pernah_dengar'))} warga mendengar isu ini.")
        if b.get("rata2_jam_sampai_memanas") is not None:
            md.append(f"- Rata-rata ada distrik yang mulai memanas sekitar jam ke-{b['rata2_jam_sampai_memanas']:.0f} "
                      "sejak isu muncul.")
    if u:
        md.append(f"- Langkah utama untuk semua wilayah: {nm(u)}, {dampak(u)} (keyakinan {u.get('keyakinan')}).")
    md += ["", "## Bila isu dibiarkan", ""]
    for t, k in kartu.items():
        ti = k.get("tanpa_intervensi") or {}
        if t != "semua" and ti:
            md.append(f"- **{t.capitalize()}**: {_pct(ti.get('warga_pernah_dengar'))} warga mendengar, isu paling banyak "
                      f"masuk lewat {ti.get('kanal_utama') or '–'}; beban siaga {ti.get('beban_siaga_per_1000') or 0:.0f} "
                      "orang-jam per 1.000 penduduk.")
    md += ["", "## Langkah paling ampuh per tipe wilayah", "",
           "| Tipe wilayah | Utamakan | Perkiraan dampak | Keyakinan | Jangan andalkan |", "|---|---|---|---|---|"]
    for t, k in kartu.items():
        md.append(f"| {t.capitalize()} | {nm(k['utamakan'])} | {dampak(k['utamakan'])} | {k['utamakan']['keyakinan']} "
                  f"| {nm(k['paling_lemah'])} |")
    md += ["", "## Jadwal tindakan", "", "| Kapan | Apa yang dilakukan |", "|---|---|",
           "| Jam 0–6 | Pantau grup WA/FB dan siapkan klarifikasi bersama tokoh setempat |",
           f"| Sebelum jam {b.get('rata2_jam_sampai_memanas') or 24:.0f} | Jalankan {nm(u)} di wilayah paling rawan |" if u else None,
           "| Sesudahnya | Terus pantau distrik rawan sampai situasi tenang |", ""]
    if runs:
        a = runs[0]
        md += ["## Distrik yang perlu diawasi lebih dulu", ""]
        md += [f"- {d['distrik']} ({d['kabupaten']}, {d['tipe']}): memanas sekitar jam ke-{d['jam']:.0f}"
               for d in a.get("distrik_eskalasi_terawal", [])[:6]] or ["- Tidak ada distrik yang memanas di putaran contoh."]
        md += ["", "## Contoh jalannya kejadian", ""]
        for r in runs:
            s = r["semua_wilayah"]
            md.append(f"- {r['intervensi']['nama']}: {_pct(s.get('pernah_dengar'))} warga mendengar, puncak siaga sekitar "
                      f"jam ke-{s['jam_puncak_siaga']:.0f}, {s['distrik_eskalasi']} distrik memanas.")
        contoh = (a.get("agen_kunci") or {}).get("contoh") or []
        md += ["", "## Peran tokoh masyarakat", ""]
        md += [f"- {c['agen']} ({c['aksi']}): \"{c['pesan']}\"" for c in contoh[:2]] or ["- Belum ada contoh pesan tokoh."]
    md += ["", "## Hal yang perlu diingat", "",
           "- Warga di simulasi fiktif; angka dipakai untuk membandingkan pilihan, bukan meramal kejadian.",
           "- Periksa ulang bersama tokoh dan aparat setempat sebelum dipakai."]
    return "\n".join(x for x in md if x is not None)
