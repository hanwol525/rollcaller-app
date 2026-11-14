"""Fold engine output and reference IPA onto common ground before comparing.

Applied to **both** sides, identically. The point is to stop the scorer
punishing an engine for a notational choice -- `ʤ` and `dʒ` are the same
affricate, and a reference that writes one while an engine writes the other is
agreement, not error.

**Unicode form comes first.** panphon normalises to NFD internally before
segmenting (`FeatureTable.normalize` is literally `unicodedata.normalize`), so
a precomposed `ç` scores correctly but is invisible to the segment regex read
directly. Decomposing here makes normalise's *output* the one comparison
form, so the distance, the segment count and the characters-dropped walk all
read the same string; ``score.dropped_characters`` decomposes its own input as
well, so a direct caller cannot bypass it either. Both placements are checked
independently by the self-check, because neither moves a distance. Every
reference in the dataset is NFC, so this fires often: on the `dev` split it is
111 occurrences over 132 references, Spanish hardest (24% of its names) from
its precomposed nasal vowels.

**Four of these rules change the distance; the rest do not.** panphon already
ignores stress marks, syllable dots and digits, so stripping them moves no
number -- they are stripped anyway because they would otherwise be counted in
the characters-dropped column, where they are noise rather than signal. The
load-bearing four, measured under panphon 0.22.2:

* **Tie bar U+0361.** ``t͡ʃ`` is a *single* panphon segment, so against ``tʃ``
  it scores 8.75 -- a near-total miss for an identical sound. Note U+035C (the
  tie bar *below*) already segments apart and scores 0.0, so only the first
  matters; both are stripped so the rule does not depend on which one an
  engine happened to emit.
* **Ligature affricates.** ``ʤ`` segments to **nothing at all** -- panphon
  returns ``[]`` -- so an unexpanded reference scores as if it were empty
  (14.5 against ``dʒ``, which is two insertions at 7.25 each). All six
  ligatures panphon cannot segment and Kokoro *can* voice are folded;
  ``ʩ ʪ ʫ`` are left out because Kokoro cannot voice them either.
* **ASCII ``g``.** The one lookalike that matters, and the only ASCII letter
  on ``gemma.py``'s own list of valid IPA consonants that panphon cannot
  segment: ``ipa_segs("g") == []`` while ``ipa_segs("ɡ") == ["ɡ"]``. So
  ``groːs`` against the dataset's own ``ɡroːs`` scores a flat 7.25 -- the
  full-miss ceiling -- for a correct pronunciation typed with the wrong
  codepoint. Reachable only through an LLM (``gemma._sanitize_ipa`` passes
  ASCII letters through deliberately); eSpeak emits U+0261 and every
  reference in the dataset uses it. Folding it here costs the Kokoro column
  nothing, because that column filters the **raw** output, where ASCII ``g``
  is genuinely outside the 114-symbol vocabulary and is still charged.
* **Chao tone letters.** Unlike digits, ``˥˦˧˨˩`` *are* segmented, so a toned
  reference costs an untoned engine one whole segment per tone. Tones are out
  of scope for this baseline, so they come off both sides.

Length marks stay, because ``aː`` is genuinely a distinct segment from ``a``
(0.5 apart) and vowel length is part of a pronunciation. ``ˑ`` stays for
symmetry although panphon ignores it.

Refusal is judged on the **un**normalised output, so a Vietnamese tone digit
still counts as a refusal even though it is stripped before scoring. That is
deliberate: Kokoro would read the digit aloud.
"""
from __future__ import annotations

import unicodedata

# Bump on any change below. Recorded in provenance so two summaries that
# normalised differently cannot be mistaken for comparable.
# ruleset-2: NFD first; ʥ/ʨ folded; ASCII g folded to U+0261.
RULESET_VERSION = "ruleset-2"

# Step 0 -- Unicode form. NFD, matching what panphon does to its own input.
UNICODE_FORM = "NFD"

# Step 1 -- wrappers, stress, syllable dots.
# Wrappers because IPAConfirmRequest.ipa is an unvalidated str, so dictionary
# notation reaches storage verbatim; stress and dots because panphon ignores
# them and they would otherwise inflate characters-dropped.
WRAPPERS = "/[]"
STRESS = "ˈˌ"           # ˈ ˌ
SYLLABLE_DOT = "."

# Step 2 -- tie bars. U+0361 above (load-bearing), U+035C below (no-op).
TIE_BARS = "͜͡"

# Step 3 -- ligature affricates, which panphon cannot segment at all. Every
# one of these is IN Kokoro's 114-symbol vocabulary, so folding them is purely
# a panphon accommodation and never hides a symbol Kokoro would drop.
LIGATURES = {
    "ʤ": "dʒ",   # ʤ -> dʒ
    "ʧ": "tʃ",   # ʧ -> tʃ
    "ʦ": "ts",        # ʦ -> ts
    "ʣ": "dz",        # ʣ -> dz
    "ʥ": "dʑ",   # ʥ -> dʑ  (Polish dź)
    "ʨ": "tɕ",   # ʨ -> tɕ  (Polish ć)
}

# Step 3b -- codepoint lookalikes. Same sound, different codepoint, and
# panphon reads only one of them. Kept apart from LIGATURES because nothing
# is being expanded: ASCII g IS the voiced velar plosive, just not U+0261.
LOOKALIKES = {
    "g": "ɡ",    # U+0067 -> U+0261
}

# Step 4 -- length marks are KEPT. Listed for the reader, never stripped.
LENGTH_MARKS = "ːˑ"      # ː ˑ

# Step 5 -- tones. Digits are a no-op for distance (panphon drops them) but
# Chao letters are not (panphon segments them).
CHAO_TONES = "˥˦˧˨˩"   # ˥ ˦ ˧ ˨ ˩

_DELETE = WRAPPERS + STRESS + SYLLABLE_DOT + TIE_BARS + CHAO_TONES + "0123456789"
_DELETE_TABLE = {ord(c): None for c in _DELETE}
_SUBSTITUTIONS = {**LIGATURES, **LOOKALIKES}
_SUBSTITUTION_TABLE = {ord(k): v for k, v in _SUBSTITUTIONS.items()}


def normalise(ipa: str) -> str:
    """Fold `ipa` onto the comparison form. Pure and idempotent."""
    if not ipa:
        return ""
    # NFD first, so a precomposed character is in the same form panphon will
    # put it in before anything else inspects the string. No substitution key,
    # no substitution value and no deleted character is touched by NFD, so
    # this cannot disturb the rules below.
    out = unicodedata.normalize(UNICODE_FORM, ipa)
    # Substitutions before deletions: expanding ʧ to tʃ must not then have its
    # parts deleted, and nothing in _DELETE appears in an expansion, so the
    # order is safe either way -- fixed here so it cannot drift.
    out = out.translate(_SUBSTITUTION_TABLE).translate(_DELETE_TABLE)
    # Collapse the whitespace a multi-token engine output arrives with; panphon
    # ignores spaces, but a doubled space would survive into the
    # characters-dropped walk as two separate excluded characters.
    return " ".join(out.split())
