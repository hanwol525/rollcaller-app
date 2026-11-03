"""Text-to-speech using Kokoro 82M (CPU).

Loaded lazily. Synthesis feeds IPA via Kokoro's inline pronunciation-override
markup — `[name](/ipa/)` — and falls back to plain text / ASCII-folded text
before failing loud. A broken render raises; it never emits a synthetic tone.
"""
from __future__ import annotations

import io
import logging
import unicodedata

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

# Kokoro 82M outputs at a fixed 24000 Hz sample rate.
_KOKORO_SAMPLE_RATE = 24000

# ---------------------------------------------------------------------------
# Warm instance
# ---------------------------------------------------------------------------
_pipeline = None  # type: ignore[var-annotated]


def warm() -> None:
    """Pre-load the Kokoro TTS pipeline. Called once at app startup."""
    global _pipeline
    try:
        from kokoro import KPipeline  # type: ignore[import-not-found]
        _pipeline = KPipeline(lang_code="a")  # American English
    except Exception as e:
        log.warning("Kokoro warm failed: %s", e)
        _pipeline = None


def _ascii_fold(s: str) -> str:
    """Strip diacritics to an ASCII-only form (last-ditch espeak-friendly)."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").strip()


# Kokoro maps phonemes with `self.vocab.get(p)` and filters out the misses, so
# a symbol outside its 114-entry vocabulary is dropped **silently** -- it
# deletes a sound from the clip rather than approximating it. These two sets
# are the part of that vocabulary which matters when folding IPA, read from
# the model's config.json. test_kokoro_constants_match_published_vocabulary
# guards them against drift, because that config is fetched at runtime.
#
# Precomposed letters Kokoro accepts whole. NFD would split each into a base
# plus a combining mark the vocabulary lacks -- that is how `ç` became `c`.
_KOKORO_PRECOMPOSED = frozenset("çæøœ")
# The only combining mark in the vocabulary: nasalisation.
_KOKORO_COMBINING = frozenset("\u0303")


def _normalise_for_kokoro(ipa: str) -> str:
    """Fold IPA to symbols Kokoro's vocabulary can actually voice.

    Keeps anything Kokoro accepts whole; otherwise decomposes and keeps the
    base plus any combining mark it accepts. So `ç` and a nasal tilde survive,
    while an acute accent is folded to its base vowel instead of deleting
    that vowel from the audio.
    """
    out: list[str] = []
    for ch in ipa:
        if ch in _KOKORO_PRECOMPOSED:
            out.append(ch)
            continue
        for part in unicodedata.normalize("NFD", ch):
            if not unicodedata.combining(part) or part in _KOKORO_COMBINING:
                out.append(part)
    return "".join(out)


def _kokoro_wav(source: str) -> bytes | None:
    """Run one Kokoro pass on `source`; return WAV bytes, or None if no audio."""
    parts = []
    for _gs, _ps, audio in _pipeline(source, voice="af_heart"):
        a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
        parts.append(a)
    if not parts:
        return None
    audio = np.concatenate(parts)
    buf = io.BytesIO()
    sf.write(buf, audio, _KOKORO_SAMPLE_RATE, format="WAV")
    return buf.getvalue()


def synthesize(text: str, ipa: str | None = None) -> bytes:
    """Synthesize speech for a name, preferring an IPA pronunciation override.

    Attempts, in order:
      1. `[{name}](/{ipa}/)` — faithful IPA via Kokoro's inline markup (if ipa),
      2. `{name}`            — plain text, Kokoro's own G2P,
      3. ASCII-folded `{name}` — diacritics stripped (only if it differs).

    Returns WAV bytes from the first attempt that yields audio. If none do,
    raises RuntimeError — never emits a silent tone.
    """
    global _pipeline
    if _pipeline is None:
        warm()                     # lazy-load Kokoro on first use
    if _pipeline is None:
        raise RuntimeError(
            "Kokoro pipeline not loaded — cannot synthesize (check venv / model weights)."
        )

    attempts: list[str] = []
    if ipa:
      bare_ipa = ipa.strip().strip("/[]").strip()  # defensive: stored IPA is bare since Unit 4
      renderable = _normalise_for_kokoro(bare_ipa)
      if renderable != bare_ipa:
          log.warning(
              "IPA folded to Kokoro's vocabulary for name=%r: %r -> %r",
              text, bare_ipa, renderable,
          )
      bare_ipa = renderable
      bare_ipa = bare_ipa.lstrip("ˈˌ")
      bare_ipa = bare_ipa.replace(" ˈ", " ").replace(" ˌ", " ")
      if bare_ipa:
          attempts.append(f"[{text}](/{bare_ipa}/)")  # faithful IPA — override pronunciation
    attempts.append(text)  # plain name — Kokoro's own G2P
    folded = _ascii_fold(text)
    if folded and folded != text:
        attempts.append(folded)  # last-ditch, diacritics stripped

    last_err: Exception | None = None
    for source in attempts:
        try:
            wav = _kokoro_wav(source)
            if wav:
                return wav
        except Exception as e:
            last_err = e
            log.warning("Kokoro attempt failed (source=%r): %s", source, e)

    # No silent tone. Fail loud so a broken render is visible.
    raise RuntimeError(f"Kokoro produced no audio for name={text!r} ipa={ipa!r}") from last_err