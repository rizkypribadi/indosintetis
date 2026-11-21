"""Perakitan dunia sintetis: geografi + populasi + jaringan + agen kunci, dengan cache disk."""
from __future__ import annotations

import pickle
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import DATA_DIR, ROOT, load_params, load_region, stable_hash
from .keyagents import KeyAgents, select_key_agents
from .network import Network, build_network
from .population import Geography, Population, build_geography, build_population

CACHE_DIR = ROOT / ".cache"


@dataclass
class World:
    geo: Geography
    pop: Population
    net: Network
    key: KeyAgents
    region: dict
    params: dict
    seed: int
    build_seconds: float = 0.0

    def summary(self) -> dict:
        pop, geo, net = self.pop, self.geo, self.net
        kab = []
        for k, nama in enumerate(geo.kab_nama):
            m = pop.kab == k
            vk = geo.kampung_kab == k
            kab.append({
                "kabupaten": nama, "agen": int(m.sum()),
                "distrik": int((geo.distrik_kab == k).sum()), "kampung": int(vk.sum()),
                "kampung_4g": round(float((geo.kampung_sinyal[vk] >= 1).mean()), 2),
                "pakai_wa": round(float(pop.hp[m].mean()), 2),
                "agen_kunci": int((pop.kab[self.key.agen] == k).sum()),
            })
        return {"agen": pop.n, "kelompok": net.G, "keanggotaan": int(net.M.nnz), "agen_kunci": self.key.k,
                "sumber_geografi": geo.sumber, "detik_bangun": round(self.build_seconds, 1), "kabupaten": kab}


def build_world(n: int = 100_000, seed: int = 42, region: dict | None = None, params: dict | None = None,
                key_count: int | None = None, use_cache: bool = True) -> World:
    """Bangun dunia secara deterministik dari (n, seed, konfigurasi). Seed sama → dunia identik."""
    region = region or load_region()
    params = params or load_params()
    struct_params = {k: params[k] for k in ("kepercayaan", "agen_kunci")}
    csv = DATA_DIR / "kampung.csv"      # data Podes ikut menentukan dunia: ganti berkas → dunia dibangun ulang
    csv_hash = stable_hash(csv.read_text(encoding="utf-8", errors="ignore")) if csv.exists() else None
    key = stable_hash([n, seed, region, struct_params, key_count, _source_hash(), csv_hash])
    path = CACHE_DIR / f"dunia_{n}_{seed}_{key}.pkl"
    if use_cache and path.exists():
        with open(path, "rb") as f:
            world: World = pickle.load(f)
        world.params = params  # parameter dinamika boleh berubah tanpa membangun ulang
        return world

    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    geo = build_geography(region, np.random.default_rng([seed, 1]))
    pop = build_population(geo, region, n, np.random.default_rng([seed, 2]))
    net = build_network(pop, geo, region, params, np.random.default_rng([seed, 3]))
    ka = select_key_agents(pop, geo, net, params, np.random.default_rng([seed, 4]), key_count)
    world = World(geo=geo, pop=pop, net=net, key=ka, region=region, params=params, seed=seed,
                  build_seconds=time.perf_counter() - t0)
    del rng
    if use_cache:
        CACHE_DIR.mkdir(exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(world, f, protocol=pickle.HIGHEST_PROTOCOL)
    return world


def _source_hash() -> str:
    import hashlib

    h = hashlib.sha256()
    for name in ("population.py", "network.py", "keyagents.py", "world.py"):
        h.update((Path(__file__).parent / name).read_bytes())
    return h.hexdigest()[:8]
