"""The renderer. Pure: builds Cells by hand, needs no engine and no panphon.

Most of these assert that a *label* is present. That is deliberate: every one
is a measured property of this corpus that makes a number mean less than it
looks, and a summary that drops one invites exactly the over-reading the brief
exists to prevent.
"""
from __future__ import annotations

from collections import Counter

import pytest

from eval import report
from eval.provenance import Provenance
from eval.score import Cell

HEADER = {"split": "dev", "n_names": 100, "limit": None}


def prov(**kw):
    base = dict(
        generated_at="2026-11-09T00:00:00+00:00", git_commit="abc123",
        git_dirty=False, espeak_version="1.52.0", espeak_library="/lib/x.dylib",
        espeak_warning=None, dataset_repo="saraelhark/say-my-name",
        dataset_revision="c7d27b1e" * 5, split="dev",
        kokoro_repo="hexgrad/Kokoro-82M", kokoro_revision="f3ff3571",
        panphon_version="0.22.2", wordfreq_version="3.1.1",
        huggingface_hub_version="1.23.0", python_version="3.11.0",
        platform="Darwin x86_64", ruleset_version="ruleset-2",
    )
    return Provenance(**{**base, **kw})


def cell(lang, engine, **kw):
    defaults = dict(
        n_names=10, n_produced_any=10, n_voiced=10, n_raw_scored=10,
        n_kokoro_scored=10, raw_mean=1.0, raw_ci=(0.8, 1.2), kokoro_mean=1.1,
        kokoro_ci=(0.9, 1.3), ceiling_mean=7.25, chars_dropped_per_name=0.0,
        engines_compared=("floor", "oracle"), raw_max=2.0,
        raw_max_name="Worst", n_over_ceiling=0, lang_per_name_best=1.0,
    )
    return Cell(lang=lang, engine=engine, **{**defaults, **kw})


def render(cells, header=None, **pkw):
    return report.render(prov(**pkw), cells, header or HEADER)


def error_row(out: str, lang: str, engine: str) -> list[str]:
    """The `lang`/`engine` row of the error table, as stripped cells.

    Parsed rather than sliced: an earlier version of this helper took the
    first 40 characters after a marker and passed only because the column
    widths happened to put the value just outside it.
    """
    section = out.split("## Error per reference segment")[1].split("## ")[0]
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) > 2 and cells[0] == lang and cells[1] == engine:
            return cells
    raise AssertionError(f"no error-table row for {lang}/{engine} in:\n{section}")


class TestTheMandatedLabels:
    def test_the_scale_is_stated_as_not_a_rate(self):
        out = render({("it", "floor"): cell("it", "floor")})
        assert "not a rate" in out.lower()
        assert "7.25" in out

    def test_authenticity_not_correctness_is_stated(self):
        out = render({("it", "floor"): cell("it", "floor")})
        assert "uthenticity" in out and "not correctness" in out

    def test_the_frequency_buckets_are_called_a_weak_signal(self):
        out = render({("it", "floor"): cell("it", "floor")})
        assert "weak signal" in out

    def test_arabic_is_labelled_native_script_only_when_present(self):
        with_ar = render({("ar", "floor"): cell("ar", "floor")})
        without = render({("it", "floor"): cell("it", "floor")})
        assert "native script" in with_ar
        assert "native script" not in without

    def test_polish_carries_the_dataset_cards_own_words_verbatim(self):
        """P2: quote it in every summary. A paraphrase would lose that this is
        the *dataset's* caveat about itself, not the harness's opinion."""
        out = render({("pl", "floor"): cell("pl", "floor")})
        assert report.POLISH_CARD_QUOTE in out
        assert "template-generated from spelling" in out

    def test_polish_is_not_labelled_when_absent(self):
        out = render({("it", "floor"): cell("it", "floor")})
        assert "template-generated" not in out

    def test_the_provenance_header_is_included_whole(self):
        out = render({("it", "floor"): cell("it", "floor")})
        assert "## Provenance" in out
        assert "ruleset-2" in out and "1.52.0" in out and "f3ff3571" in out

    def test_an_espeak_version_warning_reaches_the_summary(self):
        out = render({("it", "floor"): cell("it", "floor")},
                     espeak_warning="espeak-ng is (1, 51, 0), not (1, 52, 0)")
        assert "1, 51, 0" in out


class TestAboveCeilingIsCountedPerName:
    """`ceiling` is the cost of saying *nothing*, not a maximum: an output
    longer than the reference exceeds it without bound.

    The count is per **name**, not per cell mean, because a cell can look
    unremarkable on its mean and still hold names far above the ceiling.
    Measured on dev: 103 of 5,014 rows exceed theirs, spread over 7 of 16
    cells, but only 2 cells have a *mean* above it — so a mean-driven marker
    left 36 over-ceiling names in cells it presented as fine.
    """

    def test_the_over_ceiling_names_are_counted_and_marked(self):
        out = render({("de", "floor"): cell("de", "floor", raw_mean=1.903,
                                            n_over_ceiling=1)})
        assert "1 ⚠️" in out
        assert "counts the **names**" in out

    def test_a_cell_whose_mean_is_fine_is_still_marked_if_a_name_is_not(self):
        """The whole reason the marker is row-level."""
        out = render({("de", "floor"): cell("de", "floor", raw_mean=1.903,
                                            n_over_ceiling=1)})
        assert "unremarkable on its mean" in out

    def test_a_cell_with_no_over_ceiling_name_is_not_marked(self):
        out = render({("it", "floor"): cell("it", "floor", raw_mean=1.0)})
        assert "⚠️" not in out

    def test_the_ceiling_is_never_described_as_a_maximum_or_a_far_end(self):
        """Both claims were rendered unconditionally and were false against
        the table in the same document."""
        out = render({("it", "floor"): cell("it", "floor")})
        assert "not a maximum" in out
        assert "far end" not in out
        assert "scale is 0 to" not in out

    def test_the_worked_example_is_derived_from_this_runs_own_cells(self):
        """It used to hard-code the dev split's ar/floor figures, so every
        other run printed numbers absent from its own tables -- and the test
        split printed 48.8 above a row reading 49.439."""
        out = render({("ar", "floor"): cell("ar", "floor", raw_mean=48.8,
                                            raw_max=74.3, raw_max_name="إيثار",
                                            n_over_ceiling=57)})
        assert "`إيثار`" in out and "74.300" in out and "57 name(s)" in out
        assert "48.8 against a ceiling" not in out

    def test_it_says_so_plainly_when_nothing_exceeds_its_ceiling(self):
        out = render({("it", "floor"): cell("it", "floor")})
        assert "No name in this run exceeds its own ceiling." in out


class TestEmptySetsSaySoRatherThanPrintingANumber:
    def test_an_empty_raw_set_prints_the_sentinel(self):
        out = render({("vi", "floor"): cell("vi", "floor", n_raw_scored=0,
                                           raw_mean=None, raw_ci=(None, None),
                                           ceiling_mean=None)})
        assert report.EMPTY in out

    def test_an_empty_voiced_set_prints_the_sentinel(self):
        out = render({("vi", "floor"): cell("vi", "floor", n_kokoro_scored=0,
                                            kokoro_mean=None,
                                            kokoro_ci=(None, None))})
        assert report.EMPTY in out

    def test_a_missing_mean_renders_as_a_dash_not_a_zero(self):
        """Zero would read as a perfect engine."""
        out = render({("vi", "floor"): cell("vi", "floor", raw_mean=None,
                                            raw_ci=(None, None))})
        row = error_row(out, "vi", "floor")
        assert row[3] == "—", f"error/seg was {row[3]!r}, expected a dash"
        assert row[4] == "—", f"the interval was {row[4]!r}, expected a dash"


class TestTheErrorTableColumnOrder:
    def test_the_columns_are_where_the_header_says(self):
        """The parser above and every reader depend on this order."""
        out = render({("it", "floor"): cell("it", "floor", raw_mean=1.0,
                                            raw_ci=(0.8, 1.2), n_raw_scored=10)})
        row = error_row(out, "it", "floor")
        assert row[:6] == ["it", "floor", "10", "1.000", "0.800–1.200", "7.25"]


class TestHeadroom:
    def test_it_reports_oracle_minus_floor_per_language(self):
        out = render({("it", "floor"): cell("it", "floor", raw_mean=2.0),
                      ("it", "oracle"): cell("it", "oracle", raw_mean=0.5)})
        assert "## Headroom" in out
        assert "1.500" in out and "75%" in out

    def test_it_is_omitted_when_only_one_engine_ran(self):
        out = render({("it", "floor"): cell("it", "floor")})
        assert "## Headroom" not in out

    def test_it_says_so_rather_than_subtracting_a_missing_mean(self):
        out = render({("it", "floor"): cell("it", "floor", raw_mean=None),
                      ("it", "oracle"): cell("it", "oracle", raw_mean=0.5)})
        assert report.EMPTY in out

    def test_oracle_is_labelled_a_reference_point_and_not_an_upper_bound(self):
        """It is not a bound: `lang` is the Wiktionary section a row was mined
        from, so an identifier answering `en` for a name in the French section
        beats oracle with the same phonemizer -- measured in 5 of 8 dev
        languages, `fr` by 19.3% of its headroom."""
        out = render({("it", "floor"): cell("it", "floor", raw_mean=2.0),
                      ("it", "oracle"): cell("it", "oracle", raw_mean=0.5)})
        assert "not an upper bound" in out
        assert "no implementation can beat it" not in out

    def test_the_per_name_best_column_gives_the_real_bound(self):
        out = render({("fr", "floor"): cell("fr", "floor", raw_mean=2.794,
                                            lang_per_name_best=0.311),
                      ("fr", "oracle"): cell("fr", "oracle", raw_mean=0.712,
                                             lang_per_name_best=0.311)})
        assert "per-name best" in out
        assert "0.311" in out
        assert "0.401" in out          # oracle 0.712 - best 0.311
        assert "understates what Brief 04 could achieve" in out

    def test_beats_oracle_reads_zero_when_oracle_is_already_best(self):
        out = render({("pl", "floor"): cell("pl", "floor", raw_mean=2.401,
                                            lang_per_name_best=0.180),
                      ("pl", "oracle"): cell("pl", "oracle", raw_mean=0.180,
                                             lang_per_name_best=0.180)})
        assert "understates what Brief 04 could achieve" not in out


class TestDiagnostics:
    def test_refusal_reasons_are_listed_with_counts(self):
        out = render({("vi", "oracle"): cell(
            "vi", "oracle",
            refusal_reasons=Counter({"contains the digit '2'": 7}))})
        assert "7 × contains the digit '2'" in out

    def test_the_dropped_character_histogram_names_the_characters(self):
        out = render({("de", "floor"): cell(
            "de", "floor", dropped_histogram=Counter({"ɚ": 52, "ᵻ": 2}))})
        assert "`ɚ`×52" in out and "`ᵻ`×2" in out

    def test_the_kokoro_asymmetry_states_the_direction_correctly(self):
        """ɚ ᵻ ( ) are in Kokoro's vocabulary but invisible to panphon. The
        report used to call that a downward bias -- "they cost the engine
        nothing". Backwards: an unsegmentable character standing opposite a
        reference segment leaves that segment unmatched, so it costs a full
        7.25 deletion where a wrong-but-readable phoneme would cost ~0.25.
        Measured: `ˈɛkɚt` vs `ˈɛkɐt` scores 1.812, not 0.000, and 47 of 50
        affected de/floor names improve when ɚ is made readable -- so the
        floor is punished and the headroom overstated.
        """
        out = render({("de", "floor"): cell(
            "de", "floor", dropped_histogram=Counter({"ɚ": 52}))})
        assert "biases the error downward" not in out
        assert "cost the engine nothing" not in out
        assert "blind" in out and "over-charge" in out
        assert "headroom **overstated**" in out

    def test_it_explains_that_a_flag_over_charges_rather_than_being_free(self):
        out = render({("fr", "oracle"): cell(
            "fr", "oracle", dropped_histogram=Counter({"(": 38, ")": 38}))})
        assert "phantom phonemes" in out
        assert "markup, not mispronunciation" in out

    def test_a_cell_intersected_against_fewer_engines_is_called_out(self):
        out = render({("it", "floor"): cell("it", "floor"),
                      ("it", "oracle"): cell("it", "oracle"),
                      ("vi", "floor"): cell("vi", "floor",
                                            engines_compared=("floor",))})
        assert "fewer engines" in out
        assert "`vi`: floor" in out

    def test_no_callout_when_every_language_saw_every_engine(self):
        out = render({("it", "floor"): cell("it", "floor"),
                      ("it", "oracle"): cell("it", "oracle")})
        assert "fewer engines" not in out


class TestShape:
    def test_every_cell_gets_a_row_in_both_tables(self):
        cells = {(l, e): cell(l, e) for l in ("it", "pl") for e in ("floor", "oracle")}
        out = render(cells)
        coverage, error = out.split("## Error per reference segment")
        for lang in ("it", "pl"):
            assert coverage.count(f"| {lang}") == 2
            assert error.split("## Headroom")[0].count(f"| {lang}") == 2

    def test_it_renders_with_no_cells_at_all_rather_than_raising(self):
        assert "## Provenance" in render({})
