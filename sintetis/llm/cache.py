"""FR-08 Cache keputusan agen kunci (SQLite) dan pembekuan tabel keputusan sebelum Monte Carlo."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..config import OUT_DIR


class DecisionCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or OUT_DIR / "cache_keputusan.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS keputusan (
              kunci TEXT PRIMARY KEY, model TEXT, prompt_hash TEXT, persona_id TEXT,
              konteks TEXT, keputusan TEXT, raw TEXT, dibuat REAL,
              token_masuk INTEGER, token_keluar INTEGER, token_cache INTEGER
            );
            CREATE TABLE IF NOT EXISTS beku (
              nama TEXT, kunci TEXT, keputusan TEXT, PRIMARY KEY (nama, kunci)
            );
            CREATE TABLE IF NOT EXISTS beku_meta (nama TEXT PRIMARY KEY, dibuat REAL, info TEXT);
            """
        )

    def get(self, kunci: str) -> dict | None:
        row = self.db.execute("SELECT keputusan, raw, model FROM keputusan WHERE kunci=?", (kunci,)).fetchone()
        if not row:
            return None
        return {"keputusan": json.loads(row[0]), "raw": row[1], "model": row[2]}

    def put(self, kunci: str, model: str, prompt_hash: str, persona_id: str, konteks: dict,
            keputusan: dict, raw: str, usage: dict | None = None) -> None:
        u = usage or {}
        self.db.execute(
            "INSERT OR REPLACE INTO keputusan VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (kunci, model, prompt_hash, persona_id, json.dumps(konteks, ensure_ascii=False),
             json.dumps(keputusan, ensure_ascii=False), raw, time.time(),
             u.get("prompt_tokens", 0), u.get("completion_tokens", 0), u.get("cache_hit_tokens", 0)),
        )

    def commit(self) -> None:
        self.db.commit()

    def freeze(self, nama: str, info: dict | None = None) -> int:
        """Salin semua keputusan yang ada ke tabel beku bernama `nama`. Mengembalikan jumlah entri."""
        self.db.execute("DELETE FROM beku WHERE nama=?", (nama,))
        self.db.execute("INSERT INTO beku SELECT ?, kunci, keputusan FROM keputusan", (nama,))
        self.db.execute("INSERT OR REPLACE INTO beku_meta VALUES (?,?,?)",
                        (nama, time.time(), json.dumps(info or {}, ensure_ascii=False)))
        self.db.commit()
        return self.db.execute("SELECT COUNT(*) FROM beku WHERE nama=?", (nama,)).fetchone()[0]

    def load_frozen(self, nama: str) -> dict[str, dict]:
        rows = self.db.execute("SELECT kunci, keputusan FROM beku WHERE nama=?", (nama,)).fetchall()
        if not rows:
            raise KeyError(f"Tabel keputusan beku '{nama}' tidak ada. Jalankan perintah 'bekukan' dulu.")
        return {k: json.loads(v) for k, v in rows}

    def stats(self) -> dict:
        n, ti, to, tc = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(token_masuk),0), COALESCE(SUM(token_keluar),0), COALESCE(SUM(token_cache),0) FROM keputusan"
        ).fetchone()
        beku = self.db.execute("SELECT nama, dibuat FROM beku_meta").fetchall()
        return {"entri": n, "token_masuk": ti, "token_keluar": to, "token_cache": tc, "beku": [b[0] for b in beku]}
