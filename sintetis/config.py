"""Pemuatan berkas konfigurasi YAML (wilayah, parameter, skenario, intervensi)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SCENARIO_DIR = ROOT / "scenarios"
# SINTETIS_DATA_DIR: arahkan ke folder lain (mis. folder kosong di tes → geografi sintetis).
DATA_DIR = Path(os.environ.get("SINTETIS_DATA_DIR") or ROOT / "data")
OUT_DIR = ROOT / "out"

HARI = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
# Kelompok penduduk yang bisa diberi pengali kerentanan lewat isu.faktor_identitas (lihat engine.py).
FAKTOR_IDENTITAS = ("orang_tua_anak_sekolah", "perempuan", "laki_laki", "pemuda", "lansia", "pengguna_hp")
JENIS_GRUP = ("sekolah", "keluarga", "jemaat", "marga", "info")


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_region(path: str | Path | None = None) -> dict[str, Any]:
    return load_yaml(path or CONFIG_DIR / "papua.yaml")


def load_params(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    params = load_yaml(path or CONFIG_DIR / "parameter.yaml")
    for key, value in (overrides or {}).items():
        _set_dotted(params, key, value)
    return params


def load_scenario(path: str | Path | None = None) -> dict[str, Any]:
    sc = load_yaml(path or SCENARIO_DIR / "isu_sentani.yaml")
    validate_scenario(sc)
    return sc


def load_interventions(path: str | Path | None = None) -> dict[str, Any]:
    return load_yaml(path or SCENARIO_DIR / "intervensi.yaml")


def validate_scenario(sc: dict[str, Any]) -> None:
    """Validasi ringan editor skenario (FR-05). Melempar ValueError dengan pesan berbahasa Indonesia."""
    for key in ("nama", "mulai", "isu", "tanam"):
        if key not in sc:
            raise ValueError(f"Skenario tidak punya kunci wajib '{key}'")
    if sc["mulai"].get("hari") not in HARI:
        raise ValueError(f"mulai.hari harus salah satu dari {HARI}")
    if not 0 <= int(sc["mulai"].get("jam", 0)) <= 23:
        raise ValueError("mulai.jam harus 0..23")
    if not 0 <= float(sc["isu"].get("intensitas_emosi", 0.5)) <= 1:
        raise ValueError("isu.intensitas_emosi harus 0..1")
    if not str(sc["isu"].get("teks", "")).strip():
        raise ValueError("isu.teks tidak boleh kosong")
    for k, v in (sc["isu"].get("faktor_identitas") or {}).items():
        if k not in FAKTOR_IDENTITAS:
            raise ValueError(f"isu.faktor_identitas '{k}' tidak dikenal; pilih dari {FAKTOR_IDENTITAS}")
        if not 0 < float(v) <= 5:
            raise ValueError(f"isu.faktor_identitas.{k} harus 0..5")
    if not sc["tanam"]:
        raise ValueError("Skenario butuh minimal satu titik tanam")
    for t in sc["tanam"]:
        if t.get("kanal") not in ("wa", "fb", "tatap_muka"):
            raise ValueError("tanam.kanal harus wa | fb | tatap_muka")
        if t.get("jenis_grup", "keluarga") not in JENIS_GRUP:
            raise ValueError(f"tanam.jenis_grup harus salah satu dari {JENIS_GRUP}")
    validate_provokator(sc.get("provokator"))
    jd = sc.get("jeda_deteksi_jam")
    if jd is not None and not 0 <= float(jd) <= 72:
        raise ValueError("jeda_deteksi_jam harus 0..72 (jam dari isu muncul sampai diketahui petugas)")
    for x in sc.get("tanpa_sinyal") or []:
        if not isinstance(x, dict) or "kabupaten" not in x:
            raise ValueError("tanpa_sinyal: daftar {kabupaten, distrik (opsional)}")
    sc.setdefault("langkah_menit", 30)
    sc.setdefault("durasi_jam", 48)


def jeda_deteksi(sc: dict, params: dict, tipe: str | None = None) -> float:
    """F5: jam dari isu pertama beredar sampai diketahui petugas. Skenario > nilai bawaan per tipe wilayah."""
    if sc.get("jeda_deteksi_jam") is not None:
        return float(sc["jeda_deteksi_jam"])
    bawaan = params.get("jeda_deteksi_jam", 0)
    if isinstance(bawaan, dict):
        return float(bawaan.get(tipe, bawaan.get("bawaan", 0)) if tipe else bawaan.get("bawaan", 0))
    return float(bawaan or 0)


# ---------------------------------------------------------------------------------------------- F6 versi model
BERKAS_MODEL = ("parameter.yaml", "papua.yaml", "kalibrasi_rujukan.yaml", "kalibrasi_aktif.yaml")
TANDA_SIMULASI = "SIMULASI — BUKAN PRAKIRAAN"


def status_validasi() -> list[str]:
    """Status validasi singkat yang dicetak di setiap keluaran lapangan."""
    out = []
    aktif = CONFIG_DIR / "kalibrasi_aktif.yaml"
    if aktif.exists():
        a = load_yaml(aktif) or {}
        out.append(f"parameter dikalibrasi terhadap {a.get('jumlah_kasus', '?')} kasus ({a.get('posterior', '?')})")
    else:
        out.append("parameter belum dikalibrasi")
    out.append("persona sudah ditinjau" if (CONFIG_DIR / "persona_ditinjau.yaml").exists() else "persona belum ditinjau")
    out.append("geografi " + ("data terbuka" if (DATA_DIR / "kampung.csv").exists() else "sintetis"))
    return out


def kalibrasi_aktif() -> str | None:
    """Nama posterior kalibrasi aktif (config/kalibrasi_aktif.yaml, ditulis `kalibrasi --kasus`), atau None."""
    p = CONFIG_DIR / "kalibrasi_aktif.yaml"
    return (load_yaml(p) or {}).get("posterior") if p.exists() else None


def versi_model(posterior: str | None = None) -> dict:
    """Identitas model untuk kartu, pustaka, PDF: hash konfigurasi model + versi paket + posterior.

    `id` hanya berubah bila parameter, wilayah, rujukan/kalibrasi aktif, posterior, atau versi paket berubah,
    sehingga pustaka tidak menjadi usang setiap kali satu baris kode diperbaiki. Hash isi kode tetap
    dicatat terpisah di `kode`.
    """
    from . import __version__

    isi = {}
    for nama in BERKAS_MODEL:
        p = CONFIG_DIR / nama
        isi[nama] = hashlib.sha256(p.read_bytes()).hexdigest()[:10] if p.exists() else None
    if posterior is None and (CONFIG_DIR / "kalibrasi_aktif.yaml").exists():
        posterior = (load_yaml(CONFIG_DIR / "kalibrasi_aktif.yaml") or {}).get("posterior")
    h = stable_hash([isi, __version__, posterior])[:10]
    sv = status_validasi()
    return {"id": f"m{__version__.replace('.', '')}-{h}", "paket": __version__, "kode": code_version(),
            "berkas": isi, "posterior": posterior, "status_validasi": sv,
            "teks": f"model {__version__} · {h} · " + "; ".join(sv)}


def validate_provokator(specs: Any) -> None:
    """Blok opsional `provokator:` (lihat sintetis/provocateur.py)."""
    if specs is None:
        return
    if not isinstance(specs, list):
        raise ValueError("provokator harus berupa daftar")
    for s in specs:
        if s.get("jenis") not in ("buzzer", "lokal"):
            raise ValueError("provokator.jenis harus buzzer | lokal")
        if s["jenis"] == "buzzer":
            if s.get("kanal", "fb") not in ("fb", "wa"):
                raise ValueError("provokator buzzer: kanal harus fb | wa")
            if s.get("kanal") == "wa" and s.get("jenis_grup", "keluarga") not in ("sekolah", "keluarga", "jemaat", "marga"):
                raise ValueError("provokator buzzer WA: jenis_grup harus sekolah | keluarga | jemaat | marga")
            if not 1 <= int(s.get("akun", 10)) <= 500:
                raise ValueError("provokator.akun harus 1..500")
            if not 1 <= int(s.get("grup_per_akun", 3)) <= 50:
                raise ValueError("provokator.grup_per_akun harus 1..50")
            if not 0 < float(s.get("intensitas", 2.0)) <= 20:
                raise ValueError("provokator.intensitas harus 0..20")
        elif not 1 <= int(s.get("jumlah", 3)) <= 200:
            raise ValueError("provokator.jumlah harus 1..200")
        m, e = float(s.get("mulai_jam", 0)), s.get("selesai_jam")
        if m < 0 or (e is not None and float(e) <= m):
            raise ValueError("provokator: mulai_jam >= 0 dan selesai_jam > mulai_jam")
        if s.get("distrik") and not s.get("kabupaten"):
            raise ValueError("provokator: distrik butuh kabupaten")
        cp = s.get("contoh_pesan")
        if cp is not None and (not isinstance(cp, list) or len(cp) > 10 or not all(isinstance(x, str) for x in cp)):
            raise ValueError("provokator.contoh_pesan harus daftar teks (maks. 10)")
        if s.get("contoh_serangan") is not None and not isinstance(s["contoh_serangan"], str):
            raise ValueError("provokator.contoh_serangan harus teks")


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def code_version() -> str:
    """Versi kode untuk replikasi: versi paket + hash isi berkas sumber."""
    from . import __version__

    h = hashlib.sha256()
    for p in sorted((ROOT / "sintetis").rglob("*.py")):
        h.update(p.read_bytes())
    return f"{__version__}+{h.hexdigest()[:10]}"


def _set_dotted(d: dict[str, Any], dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value
