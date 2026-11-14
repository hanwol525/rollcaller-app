"""Render scored cells as Markdown.

Every number here is **authenticity against Wiktionary, not correctness**: a
reference is what one Wiktionary contributor wrote, and a name can be
pronounced more than one defensible way. So the figures compare engines
against a fixed yardstick; they do not grade pronunciations.

The labels below are not decoration. Each one is a measured property of this
corpus that makes a number mean less than it looks:

* Arabic rows are in **native script**, which no roster realistically
  contains, so `ar` measures transliteration-by-eSpeak, not a real ceremony.
* Polish is 56% of the corpus and its Wiktionary IPA is largely
  template-generated from spelling, so a very high Polish score is evidence
  about a transliteration rule, not about names.
* Frequency buckets are a weak signal: every name here is on Wiktionary by
  construction and `wordfreq` returns 0.0 for anything absent from running
  text, so `rare` mostly means `not in running text`.
* The error figure is **not a rate and is unbounded above**. A complete miss
  -- saying nothing -- costs a flat ~7.25 per reference segment, and that is
  what the `ceiling` column reports. It is a reference point, **not a
  maximum**: an output *longer* than the reference costs more, because every
  extra segment is an insertion. A figure *above* the ceiling therefore means the
  engine said far too much; the rendered report counts those names for the
  run in hand rather than quoting a remembered one.
"""
from __future__ import annotations

from collections import Counter

POLISH_CARD_QUOTE = (
    "Polish IPA on Wiktionary is largely template-generated from spelling "
    "(1.01 variants per name, against 2.47 for Spanish). Treat very high "
    "Polish scores as evidence about a transliteration rule, not about names."
)

EMPTY = "empty — no comparison possible"


# Accepted first, then the three distinct ways of not answering. Ordered so a
# reader scanning the table sees the one number that matters leftmost.
BRANCH_ORDER = ("llm_accepted", "llm_rejected", "llm_empty", "llm_error")


def _num(value, places=3, dash="—"):
    return dash if value is None else f"{value:.{places}f}"


def _ci(interval, places=3):
    lo, hi = interval
    if lo is None or hi is None:
        return "—"
    return f"{lo:.{places}f}–{hi:.{places}f}"


def _worst_example(cells: dict) -> str:
    """A worked example of an above-ceiling cell, drawn from *this* run.

    Derived rather than written down: an earlier version hard-coded the dev
    split's `ar`/`floor` figures into this paragraph, so rendering any other
    run printed numbers absent from -- and on the `test` split, contradicted
    by -- its own tables.
    """
    over = [c for c in cells.values()
            if c.n_over_ceiling and c.raw_max is not None
            and c.ceiling_mean is not None]
    if not over:
        return "No name in this run exceeds its own ceiling."
    worst = max(over, key=lambda c: c.raw_max)
    total = sum(c.n_over_ceiling for c in over)
    return (
        f"In this run {total} name-and-engine row(s) exceed their own "
        f"ceiling — a name counted once per engine that over-produces for "
        f"it, since `n_over_ceiling` is per cell. The worst is "
        f"`{worst.raw_max_name}` under `{worst.lang}`/`{worst.engine}` at "
        f"{worst.raw_max:.3f} against a ceiling of {worst.ceiling_mean:.2f} — "
        f"{worst.raw_max / worst.ceiling_mean:.1f}x the cost of saying nothing."
    )


def _table(headers, rows):
    bad = [i for i, r in enumerate(rows) if len(r) != len(headers)]
    if bad:
        # Loud rather than an IndexError out of a comprehension: a row that
        # has drifted from its header means a column was added in one place
        # and not the other, and the table would be silently misaligned.
        raise ValueError(
            f"row(s) {bad} have {[len(rows[i]) for i in bad]} cells against "
            f"{len(headers)} headers {headers}"
        )
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    out = ["| " + " | ".join(str(h).ljust(w) for h, w in zip(headers, widths)) + " |",
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out += ["| " + " | ".join(str(c).ljust(w) for c, w in zip(row, widths)) + " |"
            for row in rows]
    return "\n".join(out)


def _branch_section(cells: dict, langs) -> list[str]:
    """Where `today`'s answers actually came from.

    **This section decides whether the error figures above are readable at
    all.** ``g2p()`` is ``gemma_ipa(text) or espeak_ipa(text)``
    (``g2p.py:112``), so every LLM failure silently becomes the eSpeak floor.
    A `today` column that is mostly ``llm_error`` therefore scores almost
    exactly like ``floor`` while looking like a measurement of the model --
    and "the LLM is no better than the floor" and "the LLM was never reached"
    are opposite findings with identical numbers. Only the branch mix
    separates them.
    """
    with_branches = {k: c for k, c in cells.items() if c.branches}
    if not with_branches:
        return []

    out = ["## Where `today`'s answers came from", "",
           "One branch per call, decided by the recording wrapper's return",
           "value rather than by a log line (Amendment B): success logs",
           "nothing, so the return value is the only authoritative evidence.",
           ""]
    rows, totals = [], Counter()
    for (lang, engine), cell in sorted(with_branches.items()):
        total = sum(cell.branches.values())
        totals.update(cell.branches)
        rows.append([
            lang, engine, total,
            *(f"{cell.branches.get(b, 0)}"
              f" ({cell.branches.get(b, 0) / total:.0%})" if total else "—"
              for b in BRANCH_ORDER),
        ])
    out += [_table(["lang", "engine", "calls", *BRANCH_ORDER], rows), ""]

    grand = sum(totals.values())
    accepted = totals.get("llm_accepted", 0)
    out += [
        f"Across every language, **{accepted} of {grand} calls "
        f"({accepted / grand:.1%}) returned IPA the sanitizer accepted**. "
        f"The other {grand - accepted} fell through to the eSpeak floor, so "
        "those rows measure `floor`, not the model.",
        "",
    ]
    if accepted < grand:
        out += ["Why each non-accepted call failed:", ""]
        reasons = Counter()
        for cell in with_branches.values():
            reasons.update(cell.branch_reasons)
        for reason, n in reasons.most_common(12):
            out += [f"- `{n:5}`  {reason}"]
        out += [""]
    if grand and accepted / grand < 0.5:
        out += [
            "> ⚠️ Fewer than half of the calls were accepted, so `today`'s "
            "error figure is mostly the eSpeak floor with an LLM's label on "
            "it. Read it as a measurement of the **client**, not of the "
            "model, and do not compare it with `oracle` as though it were a "
            "model result.",
            "",
        ]
    return out


def render(prov, cells: dict, header: dict, title: str = "Pronunciation baseline") -> str:
    """Markdown for one run. `cells` is score.summarise's output."""
    langs = sorted({lang for lang, _ in cells})
    engines = sorted({engine for _, engine in cells})
    out = [f"# {title} — `{prov.split}`", ""]
    out += [prov.render(), ""]

    n_names = header.get("n_names")
    out += [
        "## What was run", "",
        f"- engines: {', '.join(engines)}",
        f"- languages: {', '.join(langs)}",
        f"- names: {n_names}" if n_names is not None else "",
        f"- limit per language: {header.get('limit') or 'none'}",
        "",
        "Every figure is **authenticity against Wiktionary, not correctness**.",
        "",
    ]

    # ---- coverage and refusal ------------------------------------------
    out += ["## Coverage and refusal", "",
            "`produced` is any output at all; `voiced` is output the app's own",
            "`orchestrator.ipa_refusal` would let through to Kokoro. The gap is",
            "names the organizer would see named in a Prep Clips failure report.",
            ""]
    rows = []
    for lang in langs:
        for engine in engines:
            cell = cells.get((lang, engine))
            if cell is None:
                continue
            rows.append([
                lang, engine, cell.n_names, cell.n_produced_any, cell.n_voiced,
                sum(cell.refusal_reasons.values()),
                _num(cell.refusal_rate, 3),
                cell.n_language_switch_flags,
                cell.n_word_count_mismatch,
            ])
    out += [_table(["lang", "engine", "names", "produced", "voiced", "refused",
                    "refusal rate", "lang-switch flags", "word-count mismatch"], rows), ""]

    out += _branch_section(cells, langs)

    # ---- error, on the shared sets -------------------------------------
    out += ["## Error per reference segment", "",
            "**Not a rate, and `ceiling` is not a maximum.** `ceiling` is the cost",
            "of saying *nothing* — a flat ~7.25 per reference segment, measured.",
            "An output *longer* than the reference exceeds it without bound,",
            "because every extra segment is an insertion. So **a figure above",
            "`ceiling` is itself a diagnostic**: it means the engine said far too",
            "much, which in this corpus is always eSpeak narrating a script it",
            "cannot read rather than pronouncing a name.",
            "",
            _worst_example(cells),
            "",
            "Computed on names **every engine under comparison produced any",
            "output for** (Amendment D), so the engines are compared on identical",
            "names; the Kokoro-side figure uses the narrower set every engine",
            "actually voiced. Both set sizes are printed, so a shrunken comparison",
            "is visible rather than silent.",
            ""]
    rows = []
    for lang in langs:
        for engine in engines:
            cell = cells.get((lang, engine))
            if cell is None:
                continue
            over = cell.n_over_ceiling > 0
            rows.append([
                EMPTY if cell.n_raw_scored == 0 else cell.n_raw_scored,
                lang, engine,
                _num(cell.raw_mean),
                _ci(cell.raw_ci), _num(cell.ceiling_mean, 2),
                f"{cell.n_over_ceiling} ⚠️" if over else "0",
                EMPTY if cell.n_kokoro_scored == 0 else cell.n_kokoro_scored,
                _num(cell.kokoro_mean), _ci(cell.kokoro_ci),
                _num(cell.chars_dropped_per_name, 3),
            ])
    rows = [[r[1], r[2], r[0], *r[3:]] for r in rows]
    out += [_table(["lang", "engine", "in raw set", "error/seg", "95% CI",
                    "ceiling", "over ceiling", "in voiced set",
                    "Kokoro error/seg", "95% CI", "chars dropped/name"], rows), ""]
    if any("⚠️" in str(r[6]) for r in rows):
        out += ["> ⚠️ `over ceiling` counts the **names** whose own error exceeds the",
                "> say-nothing ceiling, so a cell can look unremarkable on its mean and",
                "> still hold names far above it. Those names are over-production, not",
                "> mispronunciation.", ""]

    # a cell intersected against fewer engines than the run holds
    partial = sorted({
        (lang, cell.engines_compared) for (lang, _), cell in cells.items()
        if len(cell.engines_compared) < len(engines)
    })
    if partial:
        out += ["> ⚠️ Some languages were intersected against fewer engines than the",
                "> run contains, so their sets are not comparable with the rest:", ""]
        out += [f"> - `{lang}`: {', '.join(eng)}" for lang, eng in partial]
        out += [""]

    # ---- headroom: the number Brief 04 is about -------------------------
    if "floor" in engines and "oracle" in engines:
        out += ["## Headroom — `oracle` minus `floor`", "",
                "`oracle` is eSpeak handed the dataset's own `lang` label, so this is",
                "what always trusting that label would buy with **this** eSpeak.",
                "",
                "**It is a reference point, not an upper bound.** The dataset card",
                "defines `lang` as the Wiktionary pronunciation section a row was mined",
                "from — not the name's origin, and not necessarily the best voice for",
                "it. So a language identifier that answers `en` for a name sitting in",
                "the French section can beat `oracle` using the same phonemizer. The",
                "`per-name best` column is the real bound available from the engines in",
                "this run: the mean, over the shared set, of whichever engine scored",
                "each name best.",
                ""]
        rows = []
        for lang in langs:
            floor, oracle = cells.get((lang, "floor")), cells.get((lang, "oracle"))
            if not floor or not oracle:
                continue
            if floor.raw_mean is None or oracle.raw_mean is None:
                rows.append([lang, _num(floor.raw_mean), _num(oracle.raw_mean),
                             EMPTY, "—", _num(floor.lang_per_name_best), "—"])
                continue
            gain = floor.raw_mean - oracle.raw_mean
            pct = f"{100 * gain / floor.raw_mean:.0f}%" if floor.raw_mean else "—"
            best = floor.lang_per_name_best
            beats = ("—" if best is None
                     else _num(max(0.0, oracle.raw_mean - best)))
            rows.append([lang, _num(floor.raw_mean), _num(oracle.raw_mean),
                         _num(gain), pct, _num(best), beats])
        out += [_table(["lang", "floor error/seg", "oracle error/seg",
                        "headroom", "relative", "per-name best",
                        "beats oracle by"], rows), ""]
        if any(r[6] not in ("—", "0.000") for r in rows):
            out += ["> A non-zero `beats oracle by` is the measured amount by which a",
                    "> per-name language choice outperforms the dataset's own label, so",
                    "> `headroom` understates what Brief 04 could achieve there.", ""]

    # ---- diagnostics ----------------------------------------------------
    reasons = [(lang, engine, cells[(lang, engine)].refusal_reasons)
               for lang in langs for engine in engines
               if (lang, engine) in cells and cells[(lang, engine)].refusal_reasons]
    if reasons:
        out += ["## Why names were refused", "",
                "The app's own guard, so this is the organizer's experience rather",
                "than an approximation of it.", ""]
        for lang, engine, counter in reasons:
            out += [f"**`{lang}` / `{engine}`**", ""]
            out += [f"- {count} × {reason}" for reason, count in counter.most_common()]
            out += [""]

    dropped = [(lang, engine, cells[(lang, engine)].dropped_histogram)
               for lang in langs for engine in engines
               if (lang, engine) in cells and cells[(lang, engine)].dropped_histogram]
    if dropped:
        out += ["## Characters panphon could not read", "",
                "After normalisation, so these are genuinely unreadable rather than",
                "notation. A symbol here is invisible to the distance, which is why",
                "it gets its own column instead of being folded into the error.",
                "",
                "**Measured asymmetry — the error figure is *blind* to these, not",
                "lenient, and the dominant direction is to over-charge.** `ɚ`, `ᵻ`, `(`",
                "and `)` are all *in* Kokoro's 114-symbol vocabulary but *not*",
                "segmentable by panphon, so they reach audio while being invisible to",
                "the distance. Invisible is not free, and which way it cuts depends on",
                "whether the character sits opposite a reference segment:",
                "",
                "- **Standing in for a segment, it costs the maximum.** The reference",
                "  segment it should have matched is left unmatched, so the distance",
                "  charges a full 7.25 deletion where a wrong-but-readable phoneme",
                "  would have cost a fraction of it. German is this case almost every",
                "  time: en-us",
                "  eSpeak writes `-er` as `ɚ` and the German reference writes `ɐ`, a",
                "  near-perfect match scored as a total miss (`ˈɛkɚt` vs `ˈɛkɐt`:",
                "  1.8125 per segment, where a readable-but-wrong vowel such as `ɪ`",
                "  would have cost 0.1875). So wherever this column is non-empty for",
                "  `floor`, the floor is **punished** and the headroom **overstated**.",
                "  How much is not derivable here: this renderer sees only cells, so a",
                "  count of affected names would be a number it cannot measure. Re-score",
                "  the rows if you need one.",
                "- **Appended where the reference has nothing, it is free.** That is the",
                "  minority case, and it is what happens inside narration, where losing",
                "  one segment out of thirty-four helps.",
                "- **A language-switch flag over-charges outright.** The parentheses are",
                "  invisible but the letters inside them are not: `(en)ˈabi(fr)` segments",
                "  as `e n a b i f r`, so the flag adds phantom phonemes at full",
                "  insertion cost. `tts.prepare_ipa` and the vocabulary filter pass the",
                "  whole flag through untouched and `orchestrator.ipa_refusal` does not",
                "  refuse it, so it reaches audio too — but most of what it costs in",
                "  *this* table is markup, not mispronunciation.",
                "",
                "So read a cell holding these symbols as **an upper bound on the",
                "engine's error, loosest where the output length is about right**. The",
                "direction is not uniform, which is why these characters are counted",
                "here instead of being folded into the error.", ""]
        for lang, engine, counter in dropped:
            shown = ", ".join(f"`{c}`×{n}" for c, n in counter.most_common(12))
            out += [f"- **`{lang}`/`{engine}`**: {shown}"]
        out += [""]

    # ---- the labels, always ---------------------------------------------
    out += ["## Reading these numbers", ""]
    out += ["- **Authenticity, not correctness.** A reference is one Wiktionary",
            "  contributor's transcription; several pronunciations of a name can be",
            "  right, so the scorer takes the closest attested variant and these",
            "  figures rank engines against a fixed yardstick rather than grading",
            "  pronunciations.",
            "- **Not a rate, and unbounded above.** Saying *nothing* costs a flat",
            "  ~7.25 per reference segment, which is the `ceiling` column; saying too",
            "  *much* costs more, without limit, because every extra segment is an",
            "  insertion. So `ceiling` is a reference point and not a maximum, and",
            "  `over ceiling` counts the names that pass it. Dividing by reference",
            "  length also makes the same single error dearer in a short name, which",
            "  is why engines are compared **within** a language and never across.",
            "- **Frequency buckets are a weak signal.** Every name here is on",
            "  Wiktionary by construction and `wordfreq` returns 0.0 for a token",
            "  absent from running text, so `rare` mostly means `not in running",
            "  text`, not `rare as a name`."]
    if "ar" in langs:
        out += ["- **`ar` is in native script, not roster-realistic.** No organizer",
                "  types Arabic script into a roster, so `ar` measures what eSpeak",
                "  does with a script rather than what a ceremony would face."]
    if "pl" in langs:
        one = sorted(engines)[0]
        total = sum(c.n_names for (_, eng), c in cells.items() if eng == one)
        pl = sum(c.n_names for (lang, eng), c in cells.items()
                 if lang == "pl" and eng == one)
        share = f" ({100 * pl / total:.0f}% of this run's names)" if total else ""
        out += [f"- **`pl` dominates the corpus{share}.** Per the dataset card: "
                f"\"{POLISH_CARD_QUOTE}\""]
    out += [""]
    return "\n".join(line for line in out if line is not None)
