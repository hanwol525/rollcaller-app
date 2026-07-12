"""Gemma 4 g2p fallback (prep-clips only, never live ceremony path).

Calls an OpenAI-compatible chat completions endpoint (Fireworks today; the
partner's self-hosted AMD/vLLM Gemma later) to get an IPA pronunciation for a
personal name. Returns clean, Kokoro-ready IPA on success, or ``None`` on any
failure — ``None`` is the signal to fall back to eSpeak.

Config is environment-only (``GEMMA_BASE_URL`` / ``GEMMA_MODEL`` /
``GEMMA_API_KEY``). If any of the three is unset, :func:`gemma_ipa` returns
``None`` immediately so the app runs and demos before the AMD box exists.
"""
from __future__ import annotations

import re

from app.config import settings


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You are a phonetic transcription engine. Given a personal name, output "
    "only its pronunciation in the International Phonetic Alphabet. Output "
    "only IPA characters — no slashes, no brackets, no explanation, no "
    "language name, no extra text. Give a single best pronunciation."
)

# Few-shot pairs anchor the output shape (bare IPA, no wrappers). The exact
# transcriptions are approximate — the goal is to show "name -> bare IPA".
_FEWSHOT: list[dict[str, str]] = [
    {"role": "user", "content": "Siobhan"},
    {"role": "assistant", "content": "ʃɪˈvɔːn"},
    {"role": "user", "content": "Dmitri"},
    {"role": "assistant", "content": "ˈdmɪtri"},
    {"role": "user", "content": "Saoirse"},
    {"role": "assistant", "content": "ˈsɜːrʃə"},
]


# ---------------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------------
# Build the "think" wrapper tags programmatically (chr-based) so they never
# appear as literal angle-bracket tags in source, which some tooling strips.
_LT = chr(60)   # <
_GT = chr(62)   # >
_THINK_OPEN = _LT + "think" + _GT
_THINK_CLOSE = _LT + "/think" + _GT
_THINK_RE = re.compile(
    re.escape(_THINK_OPEN) + r".*?" + re.escape(_THINK_CLOSE),
    re.DOTALL | re.IGNORECASE,
)
_FENCE_RE = re.compile(r"```[^\n]*")  # strip ```lang fences (opening + closing)
_SLASH_RE = re.compile(r"/([^/\n]+)/")
_BRACKET_RE = re.compile(r"\[([^\]\n]+)\]")
_LABEL_RE = re.compile(r"^\s*[A-Za-z]{1,20}:\s*", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[.,?!;:]")
# Common English words that should never appear in bare IPA output. IPA uses
# non-ASCII vowels, so ASCII-only word patterns are prose.
_PROSE_RE = re.compile(
    r"\b(the|for|this|output|pronunciation|language|name|ipa|phonetic|"
    r"alphabet|given|its|are|you|your|please|following|would|should|"
    r"only|slashes|brackets|explanation|characters|single|best|with|"
    r"from|that|which|not|but|have|has|will|can|about|into|after|"
    r"before|personal|international|transcription|engine|is|of|to|"
    r"in|it|as|be|an|or|no|so|do|go|he|we|me|my|us|am|if|up|at|by)\b",
    re.IGNORECASE,
)

_MAX_IPA_LEN = 80  # implausibly long for a single name's IPA

def _strip_leaked_graphemes(text: str) -> str:
    """Remove source-spelling letters that leak into Gemma's IPA.

    Gemma sometimes emits a raw grapheme instead of a phoneme — Polish `ł`,
    an acute-accented `á` — which Kokoro can't parse and mangles. Map the
    precomposed offenders to their nearest phoneme, then strip combining
    diacritics (spelling accents, nasal tildes, tie bars) Kokoro's English
    voice can't render. Real IPA symbols (ʃ ʒ ɔ ɐ ʐ …) are single codepoints
    with no combining marks, so they pass through untouched.
    """
    import unicodedata
    text = text.replace("ł", "w").replace("Ł", "w")   # Polish ł = /w/
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))

def _sanitize_ipa(raw: str) -> str | None:
    """Clean model output to bare IPA, or ``None`` if implausible.

    Steps:
      1. Strip reasoning/thinking blocks (defensive -- thinking should be
         off, but strip if one appears).
      2. Strip markdown code fences and standalone backticks.
      3. Extract IPA from ``/.../`` or ``[...]`` wrappers if present (may be
         embedded in prose like "The IPA is /SHA-IVAWN/").
      4. Strip a leading label like "IPA:" or "Pronunciation:".
      5. Strip quotes, take the first non-empty line, strip whitespace.
      6. Plausibility check -> reject to ``None``.

    Does **not** delete ASCII letters -- half the IPA inventory is ASCII
    (p t k m n s f b d g l w j h z v r). The guard is prompt discipline +
    wrapper stripping + the plausibility check, not character-class deletion.
    """
    if not raw:
        return None

    text = raw

    # 1. Strip thinking blocks (defensive -- thinking should be off).
    text = _THINK_RE.sub("", text)

    # 2. Strip markdown code fences, then standalone backticks.
    text = _FENCE_RE.sub("", text)
    text = text.replace("`", "")

    # 3. Extract IPA from /.../ or [...] if present (may be embedded in prose).
    m = _SLASH_RE.search(text)
    if not m:
        m = _BRACKET_RE.search(text)
    if m:
        text = m.group(1)

    # 4. Strip a leading label like "IPA:" or "Pronunciation:".
    text = _LABEL_RE.sub("", text, count=1)

    # 5. Strip quotes, take first non-empty line, strip whitespace.
    text = text.strip().strip('"').strip("'").strip()
    first_line = ""
    for line in text.splitlines():
        line = line.strip().strip('"').strip("'").strip()
        if line:
            first_line = line
            break
    text = first_line

    text = _strip_leaked_graphemes(text)

    # 6. Plausibility check -> reject to None.
    if not text:
        return None
    if len(text) > _MAX_IPA_LEN:
        return None
    # ASCII sentence punctuation anywhere -> prose contamination.
    if _PUNCT_RE.search(text):
        return None
    # Embedded common English words -> prose contamination.
    if _PROSE_RE.search(text):
        return None

    return text


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
def gemma_ipa(name: str) -> str | None:
    """Get IPA for a personal name via Gemma 4, or ``None`` on any failure.

    ``None`` is the signal to fall back to eSpeak. It is never raised -- not
    on missing config, not on network error, not on timeout, not on empty or
    implausible output.
    """
    base_url = settings.gemma_base_url
    model = settings.gemma_model
    api_key = settings.gemma_api_key
    # If any of the three is unset -> None immediately (-> eSpeak floor).
    if not base_url or not model or not api_key:
        return None

    url = base_url.rstrip("/") + "/chat/completions"
    messages: list[dict[str, str]] = (
        [{"role": "system", "content": _SYSTEM}]
        + _FEWSHOT
        + [{"role": "user", "content": name}]
    )
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,  # deterministic -- prep re-runs are stable
        "max_tokens": 64,  # IPA for a name is short
        "stream": False,
        # Gemma 4 thinking off. Non-Gemma OpenAI-compatible servers ignore
        # unknown fields, so this is safe to always include.
        "thinking": "low",
        # "reasoning_effort": "low",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        import httpx
    except ImportError:
      return None

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception:
        # Any failure (network, timeout, JSON parse, missing field) -> None.
        return None

    if not content:
        return None

    bare = _sanitize_ipa(content)
    if bare is None:
        return None
    # Wrap in the same /.../ bracketing convention the eSpeak path produces
    # (see _fallback / espeak_ipa), so gemma_ipa is a literal drop-in.
    return f"/{bare}/"