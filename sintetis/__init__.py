"""Indonesia Sintetis — laboratorium simulasi penyebaran hoaks, Fase 1: Provinsi Papua."""

__version__ = "0.1.0"

STATUS = ("S", "E", "I", "Z", "A")
S, E, I, Z, A = range(5)

TIPE_WILAYAH = ("kota", "pesisir", "pedalaman", "kepulauan")

# Kode kanal dipakai di log peristiwa dan visual.
KANAL = ("wa", "fb", "tatap_muka", "radio", "intervensi", "bersikap", "tanam", "agen_kunci")
K_WA, K_FB, K_F2F, K_RADIO, K_INTERVENSI, K_BERSIKAP, K_TANAM, K_KUNCI = range(8)
