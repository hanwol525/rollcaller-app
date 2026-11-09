"""Scoring. Pure: reads engine output, never calls an engine.

That purity is the reason generate and score are separate commands. A scoring
rule can change after a paid LLM run and cost nothing to re-apply.

Four measured quantities per language and engine, per the brief:

* **Refusal rate** -- what share of names ``orchestrator.ipa_refusal`` would
  refuse. The *same* function the batch renderer calls, so this is the
  organizer's experience, not an approximation of it. Judged on the
  **un**normalised output: tone digits are stripped before scoring but Kokoro
  would read them aloud, so they must still count as refusals.
* **Error per reference segment** -- panphon's weighted feature edit distance
  to the *closest* attested variant, over that variant's segment count.
* **Kokoro-side error** -- the same, after ``tts.prepare_ipa`` and Kokoro's
  114-symbol vocabulary filter are applied to the output. An upper bound on
  audio fidelity, because Kokoro drops an unknown symbol silently: an
  unrenderable phoneme does not degrade a clip, it deletes a sound from it.
* **Characters dropped** -- characters surviving normalisation that panphon
  cannot segment, spaces excluded.

**The error figure is not a rate and does not run 0 to 1.** A complete miss
costs a flat **7.25 per reference segment** (measured: identical for a 5-, 8-
and 9-segment reference), so that is the scale's far end. Worse, dividing by
reference length reintroduces a length sensitivity the raw distance does not
have -- one substitution costs 0.25 and one insertion 7.25 regardless of name
length, so the same single error is ~4x more expensive per segment in a short
name. Across languages that sensitivity does not cancel at all, which is the
measured reason behind the brief's "compare engines within a language only",
and every summary repeats it.

**Within a language it cancels on most names, but not all** -- and the
exception is Amendment A's doing, not a flaw in it. ``best_ref`` is chosen per
engine, so two engines scoring the same name can land on attested variants of
different length and be divided by different denominators. Measured on `dev`:
52 of 2,507 names (2.1%) attest variants of differing normalised segment
count -- Vietnamese 35.1%, Spanish 6.9%, Polish 0.0%. On ``محمد``
(``muħammad`` / ``maħmad`` / ``maħmid``) two engines each making one
substitution of identical cost 0.25 are reported 0.0312 against 0.0417, a
third apart from the denominator alone. ``RowScore.reference_segments``
carries what was used, so the spread is recoverable per cell; Vietnamese is
the one language where it is large enough to mention beside its figure.
"""
from __future__ import annotations

import random
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from eval.normalise import UNICODE_FORM, normalise

# Fixed so re-scoring the same run file reproduces the same intervals. An
# interval that moved between two runs of a *pure* function would be
# indistinguishable from a real change.
BOOTSTRAP_SEED = 20261105
BOOTSTRAP_RESAMPLES = 1000
CONFIDENCE = 0.95

# Measured under panphon 0.22.2: distance("", ref) / len(segs(ref)), constant
# across every reference tried. The self-check asserts it, so a panphon change
# that moved the scale cannot pass unnoticed.
MISS_CEILING_PER_SEGMENT = 7.25

_ft = None
_dist = None


def _tables():
    global _ft, _dist
    if _ft is None:
        import panphon
        from panphon.distance import Distance
        _ft, _dist = panphon.FeatureTable(), Distance()
    return _ft, _dist


def segments(ipa: str) -> list[str]:
    ft, _ = _tables()
    return ft.ipa_segs(ipa)


def distance(a: str, b: str) -> float:
    _, dist = _tables()
    return dist.weighted_feature_edit_distance(a, b)


def dropped_characters(ipa: str) -> tuple[int, str]:
    """Characters panphon cannot segment, spaces excluded.

    Uses the same regex ``ipa_segs`` uses, so "dropped" means exactly "not
    part of any segment the distance could see" rather than a guess from
    length arithmetic -- and it can name the characters, which is what makes
    the column diagnostic. A language-switch flag's ``(`` and ``)`` land here,
    while its ``e`` and ``n`` do *not*: panphon reads those as phonemes, so a
    flag adds phantom sounds to the distance as well as noise to this column.

    Normalises its own input the way ``tts._reject_reason`` runs
    ``prepare_ipa`` on its own, and for the same reason: ``seg_regex`` matches
    only the decomposed form, so reading a precomposed ``ç`` directly reports
    a dropped character the distance in fact scored as a whole segment. The
    returned characters are therefore from the decomposed string.
    """
    ft, _ = _tables()
    ipa = unicodedata.normalize(UNICODE_FORM, ipa)
    if not hasattr(ft, "seg_regex"):  # pragma: no cover - pinned panphon has it
        raise RuntimeError(
            "panphon.FeatureTable has no seg_regex; this harness pins "
            "panphon==0.22.2 and reads segment spans from it. Re-derive "
            "dropped_characters against the installed version rather than "
            "falling back to length arithmetic, which cannot name the "
            "characters."
        )
    covered: set[int] = set()
    for match in ft.seg_regex.finditer(ipa):
        covered.update(range(match.start(), match.end()))
    chars = [c for i, c in enumerate(ipa) if i not in covered and not c.isspace()]
    return len(chars), "".join(chars)


def kokoro_filter(ipa: str, vocab: frozenset[str]) -> str:
    """What Kokoro would actually receive, and keep.

    ``prepare_ipa`` is the app's own boundary transform (strip wrappers, then
    fold to the vocabulary's shapes); the comprehension is ``kokoro/model.py``
    dropping every symbol outside the closed 114-set, silently. Both are
    reproduced here rather than reimplemented: ``prepare_ipa`` is imported.
    """
    from app.pronunciation.tts import prepare_ipa
    return "".join(c for c in prepare_ipa(ipa) if c in vocab)


def miss_ceiling(reference_normalised: str) -> float:
    """Distance from an empty output, per segment: this reference's far end."""
    n = len(segments(reference_normalised))
    if not n:
        return 0.0
    return distance("", reference_normalised) / n


@dataclass(frozen=True)
class RowScore:
    name: str
    lang: str
    engine: str
    output: str
    produced_any: bool
    refusal: str | None
    # Raw side
    reference_used: str | None
    reference_segments: int
    raw_distance: float | None
    raw_per_segment: float | None
    ceiling_per_segment: float | None
    # Kokoro side
    kokoro_output: str | None
    kokoro_distance: float | None
    kokoro_per_segment: float | None
    # Diagnostics
    chars_dropped: int
    dropped_chars: str
    has_language_switch_flag: bool

    @property
    def voiced(self) -> bool:
        return self.produced_any and self.refusal is None


def score_row(
    name: str,
    lang: str,
    engine: str,
    output: str,
    references: tuple[str, ...],
    vocab: frozenset[str],
) -> RowScore:
    """Score one engine's single output against every attested variant.

    The closest variant wins (Amendment A). An engine emits one pronunciation
    and the dataset attests up to 2.5, so scoring against all of them would
    mark it wrong on the ones it could not simultaneously match. Any attested
    pronunciation counts as correct.
    """
    from app.pronunciation import orchestrator

    # Refusal first, and on the raw string: this is the renderer's judgement,
    # and it must not be softened by a normalisation the renderer never runs.
    refusal = orchestrator.ipa_refusal(name, output)
    produced_any = bool(output.strip())
    flagged = "(" in output or ")" in output

    out_norm = normalise(output)
    dropped_count, dropped = dropped_characters(out_norm)

    refs_norm = [r for r in (normalise(ref) for ref in references) if r]
    if not refs_norm:
        # Nothing to score against. dataset.group drops these, so reaching
        # here means a run file disagrees with its own dataset revision.
        raise ValueError(f"no usable reference for {name!r} ({lang})")

    best_ref = min(refs_norm, key=lambda ref: distance(out_norm, ref))
    raw_distance = distance(out_norm, best_ref)
    ref_segments = len(segments(best_ref))
    raw_per_segment = raw_distance / ref_segments if ref_segments else None

    # Kokoro side is measured on what would be *voiced*, so only for an
    # output the guard lets through. A refused name produces no clip at all
    # in the batch renderer, so there is no audio to put a bound on.
    kokoro_output = kokoro_distance = kokoro_per_segment = None
    if refusal is None and produced_any:
        kokoro_output = kokoro_filter(output, vocab)
        kokoro_norm = normalise(kokoro_output)
        kokoro_ref = min(refs_norm, key=lambda ref: distance(kokoro_norm, ref))
        kokoro_distance = distance(kokoro_norm, kokoro_ref)
        kokoro_segments = len(segments(kokoro_ref))
        kokoro_per_segment = (
            kokoro_distance / kokoro_segments if kokoro_segments else None
        )

    return RowScore(
        name=name, lang=lang, engine=engine, output=output,
        produced_any=produced_any, refusal=refusal,
        reference_used=best_ref, reference_segments=ref_segments,
        raw_distance=raw_distance, raw_per_segment=raw_per_segment,
        ceiling_per_segment=miss_ceiling(best_ref),
        kokoro_output=kokoro_output, kokoro_distance=kokoro_distance,
        kokoro_per_segment=kokoro_per_segment,
        chars_dropped=dropped_count, dropped_chars=dropped,
        has_language_switch_flag=flagged,
    )


def bootstrap_ci(
    values: list[float],
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    confidence: float = CONFIDENCE,
) -> tuple[float | None, float | None]:
    """Percentile bootstrap over names. Seeded, so re-scoring reproduces it."""
    clean = [v for v in values if v is not None]
    if not clean:
        return None, None
    if len(clean) == 1:
        return clean[0], clean[0]
    rng = random.Random(seed)
    n = len(clean)
    means = sorted(
        sum(clean[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(resamples)
    )
    tail = (1.0 - confidence) / 2.0
    lo = means[int(tail * resamples)]
    hi = means[min(resamples - 1, int((1.0 - tail) * resamples))]
    return lo, hi


@dataclass
class Cell:
    """One language x engine summary."""

    lang: str
    engine: str
    n_names: int = 0
    # Engines this language's shared sets were intersected against. Not
    # necessarily every engine in the run file -- see summarise.
    engines_compared: tuple[str, ...] = ()
    n_produced_any: int = 0
    n_voiced: int = 0
    n_raw_scored: int = 0
    n_kokoro_scored: int = 0
    refusal_reasons: Counter = field(default_factory=Counter)
    raw_mean: float | None = None
    raw_ci: tuple[float | None, float | None] = (None, None)
    kokoro_mean: float | None = None
    kokoro_ci: tuple[float | None, float | None] = (None, None)
    ceiling_mean: float | None = None
    chars_dropped_per_name: float | None = None
    dropped_histogram: Counter = field(default_factory=Counter)
    n_language_switch_flags: int = 0

    @property
    def refusal_rate(self) -> float | None:
        if not self.n_names:
            return None
        return sum(self.refusal_reasons.values()) / self.n_names


def summarise(rows: list[RowScore]) -> dict[tuple[str, str], Cell]:
    """Aggregate per language and engine, on sets shared across engines.

    Amendment D, and the reason for it: an engine the guard refuses on most of
    a language would otherwise shrink or empty that language's comparison set
    without saying so. Vietnamese does exactly this -- eSpeak emits tone
    digits and the guard refuses every one -- so the two figures use different
    sets on purpose:

    * error per reference segment, on names **every** engine produced *any*
      output for, refused or not. Measures the engine.
    * Kokoro-side error, on names **every** engine actually voiced. Measures
      the announcement.

    Both counts are reported for every cell, so a shrunken set is visible
    rather than silent.

    **The comparison set is per language**, and that is load-bearing. A run
    file may hold an engine for only some languages -- ``generate --langs vi
    --engines today`` next to a floor/oracle run over all eight produces
    exactly that. Intersecting against a run-wide engine list would then empty
    the shared set for every *other* language, so Italian's floor-vs-oracle
    gap would read ``None`` because ``today`` ran on Vietnamese. Each cell
    records the engines it was actually intersected against, so a shrunken
    denominator can be explained rather than guessed at.
    """
    by_lang: dict[str, list[RowScore]] = {}
    for row in rows:
        by_lang.setdefault(row.lang, []).append(row)

    cells: dict[tuple[str, str], Cell] = {}
    for lang, lang_rows in by_lang.items():
        # Engines under comparison *here*, not run-wide.
        engines = sorted({r.engine for r in lang_rows})
        by_name: dict[str, dict[str, RowScore]] = {}
        for row in lang_rows:
            by_name.setdefault(row.name, {})[row.engine] = row

        # A name counts toward a shared set only where every engine under
        # comparison has a row for it; a missing row is not a zero.
        complete = [
            per_engine for per_engine in by_name.values()
            if all(e in per_engine for e in engines)
        ]
        raw_names = {
            next(iter(p.values())).name for p in complete
            if all(p[e].produced_any for e in engines)
        }
        kokoro_names = {
            next(iter(p.values())).name for p in complete
            if all(p[e].voiced for e in engines)
        }

        for engine in engines:
            subset = [r for r in lang_rows if r.engine == engine]
            cell = Cell(lang=lang, engine=engine, n_names=len(subset),
                        engines_compared=tuple(engines))
            cell.n_produced_any = sum(1 for r in subset if r.produced_any)
            cell.n_voiced = sum(1 for r in subset if r.voiced)
            cell.refusal_reasons = Counter(
                r.refusal for r in subset if r.refusal is not None
            )
            cell.n_language_switch_flags = sum(
                1 for r in subset if r.has_language_switch_flag
            )
            cell.chars_dropped_per_name = (
                sum(r.chars_dropped for r in subset) / len(subset)
            )
            for r in subset:
                cell.dropped_histogram.update(r.dropped_chars)

            raw_values = [
                r.raw_per_segment for r in subset
                if r.name in raw_names and r.raw_per_segment is not None
            ]
            cell.n_raw_scored = len(raw_values)
            if raw_values:
                cell.raw_mean = sum(raw_values) / len(raw_values)
                cell.raw_ci = bootstrap_ci(raw_values)
                ceilings = [
                    r.ceiling_per_segment for r in subset
                    if r.name in raw_names and r.ceiling_per_segment is not None
                ]
                cell.ceiling_mean = sum(ceilings) / len(ceilings) if ceilings else None

            kokoro_values = [
                r.kokoro_per_segment for r in subset
                if r.name in kokoro_names and r.kokoro_per_segment is not None
            ]
            cell.n_kokoro_scored = len(kokoro_values)
            if kokoro_values:
                cell.kokoro_mean = sum(kokoro_values) / len(kokoro_values)
                cell.kokoro_ci = bootstrap_ci(kokoro_values)

            cells[(lang, engine)] = cell
    return cells
