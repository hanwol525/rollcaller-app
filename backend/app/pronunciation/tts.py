"""Text-to-speech using Kokoro 82M (CPU).

Loaded lazily. Synthesis feeds IPA via Kokoro's inline pronunciation-override
markup — `[name](/ipa/)` — and falls back to plain text / ASCII-folded text
before failing loud. A broken render raises; it never emits a synthetic tone.
"""
from __future__ import annotations

import io
import logging
import re
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


# ---------------------------------------------------------------------------
# Garbage guard
# ---------------------------------------------------------------------------
# eSpeak narrates instead of pronouncing when it cannot read a name: it spells
# the letters out, names the script, or reads digits aloud. Kokoro would voice
# any of that confidently at a graduation, so none of it may become a
# pronunciation override.
#
# Both sets are a snapshot of what espeak-ng 1.52.0 produces;
# test_garbage_guard_constants_match_espeak re-derives them and pins the
# engine version, so a drift shows up as an engine move rather than as a
# mysterious mismatch.
_DIGIT_RE = re.compile(r"\d")

# The IPA espeak gives for each English letter *name* ("A" -> ˈeɪ, "G" -> dʒˈiː).
_LETTER_NAMES = frozenset({
    "ˈeɪ", "bˈiː", "sˈiː", "dˈiː", "ˈiː", "ˈɛf", "dʒˈiː", "ˈeɪtʃ", "ˈaɪ",
    "dʒˈeɪ", "kˈeɪ", "ˈɛl", "ˈɛm", "ˈɛn", "ˈoʊ", "pˈiː", "kjˈuː", "ˈɑːɹ",
    "ˈɛs", "tˈiː", "jˈuː", "vˈiː", "dˈʌbəljˌuː", "ˈɛks", "wˈaɪ", "zˈiː",
})

# espeak's own word "letter", in both stress variants. It lands token-final
# when espeak names a script it cannot read, e.g. 李雷 phonemises to
# "tʃˈaɪniːzlˌɛɾɚ tʃˈaɪniːzlˌɛɾɚ" — "Chinese letter", twice.
_NARRATION_MARKERS = frozenset({"lˈɛɾɚ", "lˌɛɾɚ"})


def _reject_reason(name: str, ipa: str) -> str | None:
    """Why this IPA must not reach Kokoro, or None when it is usable.

    Returns a sentence rather than a bool so the caller can log *which* rule
    fired — the three failure modes need different fixes.
    """
    found = _DIGIT_RE.search(ipa)
    if found:
        return f"contains the digit {found.group(0)!r}, which Kokoro reads aloud"

    tokens = ipa.split()

    # Letter-name IPA is *correct* when the name contains initials, so the run
    # is only suspicious when it outruns them: "J R R Tolkien" legitimately
    # phonemises to three letter names, while "Nguyễn" phonemises to four and
    # has no initials at all.
    initials = sum(
        1 for word in name.replace("-", " ").replace(".", " ").split()
        if len(word) == 1
    )
    longest = run = 0
    for token in tokens:
        run = run + 1 if token in _LETTER_NAMES else 0
        longest = max(longest, run)
    if longest >= 2 and longest > initials:
        return (
            f"{longest} consecutive letter names against {initials} initials "
            "in the name, so eSpeak spelled it out instead of pronouncing it"
        )

    # Token-final only: a real surname can *start* with the same phonemes --
    # "Letterman" is lˈɛɾɚmən -- and must not be rejected for it.
    for token in tokens:
        if any(token.endswith(marker) for marker in _NARRATION_MARKERS):
            return (
                f"token {token!r} ends in eSpeak's word for \"letter\", so it "
                "named the script instead of pronouncing the name"
            )

    return None


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
      refusal = _reject_reason(text, bare_ipa)
      if refusal:
          log.warning(
              "IPA refused for name=%r: %s. Kokoro will voice the name "
              "directly instead (ipa=%r)", text, refusal, bare_ipa,
          )
          bare_ipa = ""        # falls through to the plain-name attempt below
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