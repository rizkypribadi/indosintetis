"""FR-08 Pembekuan tabel keputusan: putaran percontohan dengan LLM hidup mengisi cache, lalu cache
disalin ke tabel beku. Monte Carlo sesudahnya hanya membaca tabel ini (tanpa biaya API tambahan)."""
from __future__ import annotations

from .config import load_interventions, load_params, load_scenario
from .engine import Simulation
from .llm.cache import DecisionCache
from .llm.orchestrator import LLMDecider
from .world import build_world


def freeze_decisions(nama: str, mode: str = "deepseek", n: int = 100_000, seed_dunia: int = 42,
                     pilot: int = 3, kode: str = "ABCDEF", scenario: dict | None = None,
                     interventions: dict | None = None, progress=print) -> dict:
    scenario = scenario or load_scenario()
    interventions = interventions or load_interventions()
    world = build_world(n, seed_dunia, params=load_params())
    cache = DecisionCache()
    decider = LLMDecider(mode, cache=cache, intensitas=scenario["isu"].get("intensitas_emosi", 0.9),
                         alasan=scenario["isu"].get("alasan_percaya"))
    for s in range(pilot):
        for c in kode:
            Simulation(world, scenario, interventions[c], seed=s, decider=decider, log_events=False,
                       kode_intervensi=c).run()
            progress(f"  percontohan seed {s} intervensi {c}: {decider.stats()['hitung']} biaya {decider.biaya.usd:.4f} USD")
    info = {"mode": mode, "model": decider.model, "agen": n, "seed_dunia": seed_dunia, "pilot": pilot,
            "prompt_hash": decider.prompt_hash, "biaya": decider.biaya.as_dict()}
    jumlah = cache.freeze(nama, info)
    return {**info, "entri_beku": jumlah, "hitung": decider.stats()["hitung"]}
