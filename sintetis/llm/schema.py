"""Kontrak keluaran LLM agen kunci (PRD §10) dan pemetaannya ke status ABM."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import E, I, A, Z


class Keputusan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aksi: Literal["teruskan", "abaikan", "luruskan", "tanya_dulu"]
    kanal: Literal["wa", "mimbar_ibadah", "tatap_muka", "tidak_ada"]
    keyakinan: float = Field(ge=0.0, le=1.0)
    ajakan_jaga_kampung: bool
    pesan_tambahan: str = Field(default="", max_length=280)
    alasan_singkat: str = Field(default="", max_length=400)


@dataclass
class Decision:
    keputusan: Keputusan
    sumber: str                      # llm | cache | beku | abm | tanam
    raw: str = ""
    model: str = ""
    error: str = ""
    meta: dict = field(default_factory=dict)

    def status(self) -> int:
        """Pemetaan PRD: teruskan→I (A bila ajakan), luruskan/abaikan→Z, tanya_dulu→tetap E."""
        k = self.keputusan
        if k.aksi == "teruskan":
            return A if k.ajakan_jaga_kampung else I
        if k.aksi in ("luruskan", "abaikan"):
            return Z
        return E


JSON_SCHEMA_TEXT = """{
  "aksi": "teruskan | abaikan | luruskan | tanya_dulu",
  "kanal": "wa | mimbar_ibadah | tatap_muka | tidak_ada",
  "keyakinan": 0.0,
  "ajakan_jaga_kampung": false,
  "pesan_tambahan": "maks. 280 karakter",
  "alasan_singkat": "..."
}"""
