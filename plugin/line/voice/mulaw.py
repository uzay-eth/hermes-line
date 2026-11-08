"""G.711 µ-law helpers (standard library only; ``audioop`` is gone from Python 3.13).

Call audio is never transcoded on its way between Twilio and the voice model: both
sides speak ``audio/x-mulaw`` 8 kHz mono, so payloads pass through as they are. These
helpers exist for the three places that must look at or make audio locally:

* the energy VAD of the cheap hold path (``decode`` + ``rms``),
* in-band DTMF tones (``dtmf_tones``), so keys can be pressed without ending the stream,
* test fixtures (``encode``, ``tone``, ``silence``).
"""
from __future__ import annotations

import base64
import math
from typing import Iterable, Iterator, List

SAMPLE_RATE = 8000
BYTES_PER_MS = SAMPLE_RATE // 1000      # one µ-law byte per sample
FRAME_MS = 20                           # Twilio sends 20 ms media frames
FRAME_BYTES = FRAME_MS * BYTES_PER_MS   # 160 bytes
SILENCE_BYTE = 0xFF                     # µ-law code for (near) zero

_BIAS = 0x84
_CLIP = 32635


def _decode_byte(u: int) -> int:
    u = ~u & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return -sample if sign else sample


DECODE_TABLE: List[int] = [_decode_byte(i) for i in range(256)]


def encode_sample(sample: int) -> int:
    """16-bit linear PCM sample -> µ-law byte (ITU-T G.711)."""
    sign = 0
    if sample < 0:
        sample = -sample
        sign = 0x80
    sample = min(sample, _CLIP) + _BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def encode(samples: Iterable[int]) -> bytes:
    return bytes(encode_sample(int(s)) for s in samples)


def decode(data: bytes) -> List[int]:
    table = DECODE_TABLE
    return [table[b] for b in data]


def rms(data: bytes) -> float:
    """Root mean square of µ-law audio, in 16-bit linear units (0 for empty input)."""
    if not data:
        return 0.0
    table = DECODE_TABLE
    total = 0
    for b in data:
        v = table[b]
        total += v * v
    return math.sqrt(total / len(data))


def silence(ms: int) -> bytes:
    return bytes([SILENCE_BYTE]) * (max(0, int(ms)) * BYTES_PER_MS)


def tone(freqs: Iterable[float], ms: int, amplitude: float = 0.35) -> bytes:
    """A sum of sine waves, each at ``amplitude`` of full scale, as µ-law."""
    freqs = list(freqs)
    n = max(0, int(ms)) * BYTES_PER_MS
    peak = 32767 * amplitude
    out = bytearray(n)
    for i in range(n):
        t = i / SAMPLE_RATE
        out[i] = encode_sample(int(sum(peak * math.sin(2 * math.pi * f * t) for f in freqs)))
    return bytes(out)


# ITU-T Q.23 DTMF frequency pairs (row, column).
DTMF_FREQS = {
    "1": (697, 1209), "2": (697, 1336), "3": (697, 1477), "A": (697, 1633),
    "4": (770, 1209), "5": (770, 1336), "6": (770, 1477), "B": (770, 1633),
    "7": (852, 1209), "8": (852, 1336), "9": (852, 1477), "C": (852, 1633),
    "*": (941, 1209), "0": (941, 1336), "#": (941, 1477), "D": (941, 1633),
}


def dtmf_tones(digits: str, *, tone_ms: int = 120, gap_ms: int = 80, pause_ms: int = 500) -> bytes:
    """In-band DTMF for ``0-9 * #`` with ``w`` = a half-second pause (Twilio ``sendDigits`` syntax).

    Q.24 asks for tones of at least 40 ms with 40 ms gaps; 120/80 ms leaves margin for
    jitter buffers. Raises ``ValueError`` for anything else, so a caller cannot smuggle
    letters or separators into a tone train.
    """
    out = bytearray()
    for ch in digits:
        if ch == "w":
            out += silence(pause_ms)
            continue
        pair = DTMF_FREQS.get(ch)
        if pair is None or ch in "ABCD":
            raise ValueError("DTMF digits must be 0-9, *, # or w")
        out += tone(pair, tone_ms, amplitude=0.3)
        out += silence(gap_ms)
    return bytes(out)


def frames(data: bytes, frame_bytes: int = FRAME_BYTES) -> Iterator[bytes]:
    for i in range(0, len(data), frame_bytes):
        yield data[i:i + frame_bytes]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(payload: str) -> bytes:
    return base64.b64decode(payload, validate=True)


def goertzel(samples: List[int], freq: float, rate: int = SAMPLE_RATE) -> float:
    """Signal power at ``freq`` (used by tests to check generated DTMF)."""
    n = len(samples)
    if not n:
        return 0.0
    k = int(0.5 + n * freq / rate)
    w = 2 * math.pi * k / n
    coeff = 2 * math.cos(w)
    s1 = s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2, s1 = s1, s0
    return s1 * s1 + s2 * s2 - coeff * s1 * s2
