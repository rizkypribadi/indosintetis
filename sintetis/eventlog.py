"""FR-12 Log peristiwa (Parquet) untuk replay dan analisis, plus manifes replikasi (seed, versi, hash)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import STATUS
from .config import OUT_DIR, versi_model
from .engine import RunResult


def save_run(res: RunResult, world, name: str | None = None, out_dir: str | Path | None = None) -> Path:
    m = res.manifest
    name = name or f"{m['skenario']}_{m['intervensi']}_s{m['seed']}_{datetime.now():%Y%m%d-%H%M%S}"
    d = Path(out_dir or OUT_DIR / "runs") / name
    d.mkdir(parents=True, exist_ok=True)
    if res.transitions is not None:
        res.transitions.to_parquet(d / "peristiwa.parquet", index=False)
    T, D, _ = res.timeseries.shape
    ts = pd.DataFrame(res.timeseries.reshape(T * D, 5), columns=list(STATUS))
    ts.insert(0, "distrik", np.tile(np.arange(D), T))
    ts.insert(0, "langkah", np.repeat(np.arange(T), D))
    ts["risiko"] = res.risk.reshape(-1)
    ts.to_parquet(d / "deret_waktu.parquet", index=False)
    (d / "manifest.json").write_text(json.dumps({**m, "dunia": world.summary(), "versi_model": versi_model()},
                                                ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    (d / "peristiwa_naratif.json").write_text(json.dumps(res.events, ensure_ascii=False, default=str), encoding="utf-8")
    with open(d / "llm_log.jsonl", "w", encoding="utf-8") as f:
        for row in res.llm_log:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return d


def load_run(path: str | Path) -> dict:
    d = Path(path)
    return {
        "manifest": json.loads((d / "manifest.json").read_text(encoding="utf-8")),
        "peristiwa": pd.read_parquet(d / "peristiwa.parquet") if (d / "peristiwa.parquet").exists() else None,
        "deret_waktu": pd.read_parquet(d / "deret_waktu.parquet"),
        "naratif": json.loads((d / "peristiwa_naratif.json").read_text(encoding="utf-8")),
    }


def replay_states(peristiwa: pd.DataFrame, n: int, steps: int):
    """Generator status semua agen per langkah, direkonstruksi dari log transisi."""
    status = np.zeros(n, np.int8)
    by_step = {int(k): g for k, g in peristiwa.groupby("langkah")}
    yield 0, status
    for t in range(1, steps + 1):
        g = by_step.get(t)
        if g is not None:
            status[g["agen"].to_numpy()] = g["ke"].to_numpy()
        yield t, status
