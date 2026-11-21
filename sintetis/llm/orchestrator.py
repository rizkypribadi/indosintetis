"""FR-07 Orkestrator agen kunci LLM (DeepSeek / Ollama / mock) dengan validasi JSON, coba ulang,
cache SQLite, mode beku, dan pelacak biaya (FR-14)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field

import numpy as np
from pydantic import ValidationError

from ..config import CONFIG_DIR, ROOT, load_yaml, stable_hash
from .cache import DecisionCache
from .prompt import PERSONA_KEYS, prompt_hash, system_prompt, user_prompt
from .rules import rule_decision
from .schema import Decision, Keputusan


@dataclass
class DecisionRequest:
    ki: int                      # indeks agen kunci
    persona: dict
    konteks: dict                # dikirim ke LLM
    kunci_konteks: dict          # versi diskret untuk kunci cache
    petunjuk: dict = field(default_factory=dict)   # hanya untuk aturan ABM, tidak dikirim ke LLM

    @property
    def konteks_aturan(self) -> dict:
        return {**self.konteks, **self.petunjuk}


@dataclass
class CostTracker:
    tarif: dict
    batas_usd: float
    panggilan: int = 0
    gagal_validasi: int = 0
    token_cache_hit: int = 0
    token_cache_miss: int = 0
    token_keluar: int = 0

    def add(self, usage: dict) -> None:
        self.panggilan += 1
        hit = int(usage.get("cache_hit_tokens", 0))
        self.token_cache_hit += hit
        self.token_cache_miss += int(usage.get("prompt_tokens", 0)) - hit
        self.token_keluar += int(usage.get("completion_tokens", 0))

    @property
    def usd(self) -> float:
        t = self.tarif
        return (self.token_cache_hit * t["input_cache_hit"] + self.token_cache_miss * t["input_cache_miss"]
                + self.token_keluar * t["output"]) / 1e6

    @property
    def habis(self) -> bool:
        return self.usd >= self.batas_usd

    def as_dict(self) -> dict:
        return {"panggilan": self.panggilan, "gagal_validasi": self.gagal_validasi,
                "token_cache_hit": self.token_cache_hit, "token_cache_miss": self.token_cache_miss,
                "token_keluar": self.token_keluar, "biaya_usd": round(self.usd, 4), "batas_usd": self.batas_usd}


class Decider:
    mode = "abm"
    model = "aturan-abm"

    def __init__(self, intensitas: float = 0.9):
        self.intensitas = intensitas
        self.hitung = {"llm": 0, "cache": 0, "beku": 0, "abm": 0, "beku_miss": 0}

    def decide_batch(self, reqs: list[DecisionRequest], rng: np.random.Generator) -> list[Decision]:
        out = []
        for r in reqs:
            out.append(Decision(rule_decision(r.persona, r.konteks_aturan, rng, self.intensitas), "abm", model=self.model))
            self.hitung["abm"] += 1
        return out

    def stats(self) -> dict:
        return {"mode": self.mode, "model": self.model, "hitung": dict(self.hitung)}


class LLMDecider(Decider):
    def __init__(self, penyedia: str = "deepseek", cache: DecisionCache | None = None,
                 beku: dict[str, dict] | None = None, intensitas: float = 0.9, cfg: dict | None = None,
                 alasan: str | None = None):
        super().__init__(intensitas)
        self.cfg = cfg or load_yaml(CONFIG_DIR / "llm.yaml")
        pcfg = self.cfg["penyedia"][penyedia]
        self.mode, self.penyedia, self.model = penyedia, penyedia, pcfg["model"]
        self.cache = cache or DecisionCache()
        self.beku = beku
        self.biaya = CostTracker(pcfg["tarif"], float(self.cfg["batas_biaya_usd_per_skenario"]))
        self.prompt_hash = prompt_hash(alasan)
        self.system = system_prompt(alasan)
        self.log: list[dict] = []           # jawaban mentah untuk panel agen (V-09)
        self._client = None
        self.max_tokens = int(pcfg.get("max_token_keluaran", self.cfg.get("max_token_keluaran", 300)))
        if penyedia != "mock" and beku is None:
            from openai import AsyncOpenAI

            env = pcfg.get("env_kunci")
            key = resolve_api_key(env) if env else "ollama"
            if not key:
                raise RuntimeError(f"Kunci API {penyedia} tidak ditemukan ({env} atau 'LLM API Key.txt').")
            self._client_args = {"api_key": key, "base_url": pcfg["base_url"], "timeout": 60}

    def kunci(self, r: DecisionRequest) -> str:
        isi = {k: r.persona.get(k) for k in PERSONA_KEYS}
        return stable_hash([self.model, self.prompt_hash, r.persona["id"], isi, r.kunci_konteks])

    def decide_batch(self, reqs, rng):
        out: list[Decision | None] = [None] * len(reqs)
        pending = []
        for i, r in enumerate(reqs):
            k = self.kunci(r)
            if self.beku is not None:
                if k in self.beku:
                    out[i] = Decision(Keputusan(**self.beku[k]), "beku", model=self.model)
                    self.hitung["beku"] += 1
                else:
                    out[i] = Decision(rule_decision(r.persona, r.konteks_aturan, rng, self.intensitas), "abm",
                                      model="aturan-abm", meta={"beku_miss": True})
                    self.hitung["beku_miss"] += 1
                continue
            hit = self.cache.get(k)
            if hit:
                out[i] = Decision(Keputusan(**hit["keputusan"]), "cache", raw=hit["raw"], model=hit["model"])
                self.hitung["cache"] += 1
            elif self.biaya.habis:
                out[i] = Decision(rule_decision(r.persona, r.konteks_aturan, rng, self.intensitas), "abm",
                                  model="aturan-abm", error="batas biaya tercapai")
                self.hitung["abm"] += 1
            else:
                pending.append((i, k, r))
        if pending:
            results = asyncio.run(self._run_all([(k, r) for _, k, r in pending]))
            for (i, k, r), res in zip(pending, results):
                if res is None or isinstance(res, Exception):
                    err = str(res) if res is not None else "validasi gagal"
                    out[i] = Decision(rule_decision(r.persona, r.konteks_aturan, rng, self.intensitas), "abm",
                                      model="aturan-abm", error=err)
                    self.hitung["abm"] += 1
                else:
                    kep, raw, usage = res
                    self.cache.put(k, self.model, self.prompt_hash, r.persona["id"], r.kunci_konteks,
                                   kep.model_dump(), raw, usage)
                    out[i] = Decision(kep, "llm", raw=raw, model=self.model)
                    self.hitung["llm"] += 1
            self.cache.commit()
        for r, d in zip(reqs, out):
            self.log.append({"ki": r.ki, "persona": r.persona["nama"], "sumber": d.sumber, "raw": d.raw,
                             "keputusan": d.keputusan.model_dump(), "error": d.error})
        return out  # type: ignore[return-value]

    async def _run_all(self, items):
        sem = asyncio.Semaphore(int(self.cfg.get("konkurensi", 8)))
        client = None
        if self.penyedia != "mock":
            from openai import AsyncOpenAI

            client = AsyncOpenAI(**self._client_args)

        async def one(k, r):
            async with sem:
                try:
                    return await self._ask(client, k, r)
                except Exception as e:  # jaringan/API: kembali ke aturan ABM
                    return e

        try:
            return await asyncio.gather(*(one(k, r) for k, r in items))
        finally:
            if client is not None:
                await client.close()

    async def _ask(self, client, k: str, r: DecisionRequest):
        messages = [{"role": "system", "content": self.system},
                    {"role": "user", "content": user_prompt(r.persona, r.konteks)}]
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0}
        for attempt in range(1 + int(self.cfg.get("coba_ulang_validasi", 2))):
            text, usage = await self._complete(client, k, r, messages)
            for key in usage_total:
                usage_total[key] += usage.get(key, 0)
            self.biaya.add(usage)
            try:
                return Keputusan.model_validate_json(_extract_json(text)), text, usage_total
            except (ValidationError, ValueError) as e:
                self.biaya.gagal_validasi += 1
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"JSON tidak valid: {str(e)[:300]}. Ulangi, jawab hanya JSON sesuai skema."}]
        return None

    async def _complete(self, client, k: str, r: DecisionRequest, messages: list[dict]):
        if self.penyedia == "mock":
            seed = int(hashlib.sha256(k.encode()).hexdigest()[:12], 16) + len(messages)
            kep = rule_decision(r.persona, r.konteks_aturan, np.random.default_rng(seed), self.intensitas)
            text = json.dumps(kep.model_dump(), ensure_ascii=False)
            prompt_tok = sum(len(m["content"]) for m in messages) // 4
            return text, {"prompt_tokens": prompt_tok, "completion_tokens": len(text) // 4,
                          "cache_hit_tokens": len(self.system) // 4}
        from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential
        from openai import APIConnectionError, APITimeoutError, RateLimitError, InternalServerError

        async for att in AsyncRetrying(stop=stop_after_attempt(4), wait=wait_exponential(min=1, max=20),
                                       retry=retry_if_exception_type((APIConnectionError, APITimeoutError,
                                                                      RateLimitError, InternalServerError)),
                                       reraise=True):
            with att:
                resp = await client.chat.completions.create(
                    model=self.model, messages=messages, temperature=float(self.cfg.get("suhu", 0.7)),
                    max_tokens=self.max_tokens,
                    response_format={"type": "json_object"},
                )
        u = resp.usage
        usage = {"prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                 "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                 "cache_hit_tokens": (getattr(u, "prompt_cache_hit_tokens", 0)
                                      or getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0)}
        return resp.choices[0].message.content or "", usage

    def stats(self):
        s = super().stats()
        s.update({"biaya": self.biaya.as_dict(), "prompt_hash": self.prompt_hash, "beku": self.beku is not None})
        return s


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("tidak ada objek JSON")
    return text[start:end + 1]


def resolve_api_key(env_name: str | None = "DEEPSEEK_API_KEY") -> str | None:
    """Ambil kunci dari variabel lingkungan, atau dari 'LLM API Key.txt' di akar proyek."""
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].strip()
    path = ROOT / "LLM API Key.txt"
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"sk-[A-Za-z0-9_\-]{16,}", text)
        if m:
            return m.group(0)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            return lines[-1].split()[-1]
    return None


def make_decider(mode: str = "abm", beku_nama: str | None = None, intensitas: float | dict = 0.9,
                 cache: DecisionCache | None = None) -> Decider:
    """mode: abm (tanpa LLM) | mock | deepseek | ollama. Dengan beku_nama: hanya pakai tabel beku.
    `intensitas` boleh berupa dict skenario; intensitas emosi dan alasan_percaya diambil dari isu-nya."""
    alasan = None
    if isinstance(intensitas, dict):
        isu = intensitas["isu"]
        intensitas, alasan = isu.get("intensitas_emosi", 0.9), isu.get("alasan_percaya")
    if mode == "abm":
        return Decider(intensitas)
    beku = (cache or DecisionCache()).load_frozen(beku_nama) if beku_nama else None
    return LLMDecider(mode, cache=cache, beku=beku, intensitas=intensitas, alasan=alasan)
