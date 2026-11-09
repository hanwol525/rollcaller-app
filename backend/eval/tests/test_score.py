"""The scorer. Offline apart from one clearly-marked drift detector."""
from __future__ import annotations

import pytest

from eval.tests._deps import HF_HUB_AVAILABLE, PANPHON_AVAILABLE

pytestmark = pytest.mark.skipif(not PANPHON_AVAILABLE, reason="panphon not installed")

REF = ("ˈalitʃe",)   # ˈalitʃe
VOICED = "alitʃe"
REFUSED_DIGIT = "alit5e"


class TestDroppedCharacters:
    def test_counts_only_what_panphon_cannot_segment(self):
        from eval.score import dropped_characters
        assert dropped_characters("bob") == (0, "")

    def test_spaces_are_excluded(self):
        from eval.score import dropped_characters
        assert dropped_characters("bob bob") == (0, "")

    def test_it_names_the_characters_not_just_a_count(self):
        """A bare count cannot tell a stray digit from a language-switch flag;
        the summary needs to say which."""
        from eval.score import dropped_characters
        count, chars = dropped_characters("bob5")
        assert (count, chars) == (1, "5")

    def test_a_language_switch_flag_drops_its_parens_but_not_its_letters(self):
        """phonemizer's default keep-flags can embed (en) markers that nothing
        in g2p.py or tts.py strips. panphon reads the `e` and `n` as phonemes,
        so a flag adds phantom sounds to the distance as well as noise here --
        which is why flags get their own counted column rather than being
        folded into this one."""
        from eval.score import dropped_characters, segments
        count, chars = dropped_characters("(en)bob")
        assert (count, chars) == (2, "()")
        assert segments("(en)bob") == ["e", "n", "b", "o", "b"]

    @pytest.mark.parametrize("ipa", ["çetin", "ãnselmo", "mikaẽl"])
    def test_a_precomposed_character_panphon_segments_is_not_dropped(self, ipa):
        """seg_regex matches only the decomposed form, so a walk over the
        string as given reports a dropped character the distance in fact
        scored as a whole segment. Every dataset reference is NFC, so this is
        the common case, not the exotic one: 111 occurrences on `dev`."""
        from eval.score import dropped_characters, segments
        from eval.normalise import normalise
        norm = normalise(ipa)
        assert segments(norm), "fixture does not segment at all"
        assert dropped_characters(norm) == (0, "")

    def test_it_normalises_its_own_input_so_a_direct_caller_cannot_bypass_it(self):
        """The same belt-and-braces as tts._reject_reason running prepare_ipa
        on its own input: the two sides must judge the same string however
        the function is reached."""
        from eval.score import dropped_characters
        assert dropped_characters("çetin") == (0, "")

    def test_a_mark_panphon_really_cannot_read_is_still_named(self):
        """Decomposing must not turn the column blind. A grave accent is not
        an IPA diacritic and panphon drops it, so it belongs here."""
        from eval.score import dropped_characters
        from eval.normalise import normalise
        count, chars = dropped_characters(normalise("èb"))
        assert count == 1 and chars == "\u0300"


class TestKokoroFilter:
    def test_symbols_outside_the_vocabulary_disappear(self, vocab):
        """Kokoro drops an unknown phoneme silently, so this deletes a sound
        from the clip rather than degrading it."""
        from eval.score import kokoro_filter
        assert "ħ" not in vocab
        assert kokoro_filter("muħammad", vocab) == "muammad"

    def test_it_applies_the_app_s_own_prepare_ipa_first(self, vocab):
        """Not a reimplementation: prepare_ipa is imported, so the harness
        cannot drift from the real TTS boundary."""
        from eval.score import kokoro_filter
        assert kokoro_filter("/alitʃe/", vocab) == "alitʃe"

    def test_a_ligature_in_the_vocabulary_survives(self, vocab):
        from eval.score import kokoro_filter
        assert "ʤ" in vocab
        assert kokoro_filter("ʤe", vocab) == "ʤe"


class TestMissCeiling:
    @pytest.mark.parametrize("ref", ["mawɡɔʒata", "bob", "ʃɪˈvɔːn"])
    def test_a_total_miss_costs_a_flat_7_25_per_segment(self, ref):
        """The scale's far end, and the reason the error column is not a
        'rate': it runs 0 to 7.25, not 0 to 1."""
        from eval.normalise import normalise
        from eval.score import MISS_CEILING_PER_SEGMENT, miss_ceiling
        assert miss_ceiling(normalise(ref)) == pytest.approx(MISS_CEILING_PER_SEGMENT)


class TestScoreRow:
    def test_the_closest_attested_variant_wins(self, vocab):
        """Amendment A. An engine emits one pronunciation against up to 2.5
        attested ones, so any attested pronunciation counts as correct."""
        from eval.score import score_row
        row = score_row("Alice", "it", "floor", VOICED,
                        ("zzzz", "ˈalitʃe", "qqqq"), vocab)
        assert row.reference_used == "alitʃe"
        assert row.raw_distance == 0

    def test_refusal_is_judged_on_the_unnormalised_output(self, vocab):
        """Normalisation strips tone digits, but Kokoro would read one aloud,
        so the guard must see the string the renderer would see."""
        from eval.normalise import normalise
        from eval.score import score_row
        assert "5" not in normalise(REFUSED_DIGIT)
        row = score_row("Alice", "it", "floor", REFUSED_DIGIT, REF, vocab)
        assert row.refusal is not None and "digit" in row.refusal

    def test_a_refused_row_gets_no_kokoro_figure(self, vocab):
        """The batch renderer gives a refused participant no clip at all, so
        there is no audio to put an upper bound on."""
        from eval.score import score_row
        row = score_row("Alice", "it", "floor", REFUSED_DIGIT, REF, vocab)
        assert row.kokoro_output is None and row.kokoro_per_segment is None
        assert row.raw_per_segment is not None  # raw still measures the engine

    def test_a_voiced_row_gets_both_figures(self, vocab):
        from eval.score import score_row
        row = score_row("Alice", "it", "oracle", VOICED, REF, vocab)
        assert row.refusal is None and row.voiced
        assert row.raw_per_segment == 0 and row.kokoro_per_segment == 0

    def test_empty_output_is_a_refusal_and_produced_nothing(self, vocab):
        from eval.score import score_row
        row = score_row("Alice", "it", "floor", "", REF, vocab)
        assert row.refusal is not None
        assert row.produced_any is False and row.voiced is False
        assert row.kokoro_per_segment is None

    def test_a_language_switch_flag_is_counted_not_stripped(self, vocab):
        """Stripping it would stop measuring the app as it is; language_switch
        is fenced to Brief 04."""
        from eval.score import score_row
        row = score_row("Alice", "it", "floor", "(en)alitʃe", REF, vocab)
        assert row.has_language_switch_flag
        assert "(" in row.output

    def test_a_name_with_no_usable_reference_raises(self, vocab):
        """dataset.group drops these, so reaching the scorer means a run file
        disagrees with its own dataset revision -- worth a loud failure."""
        from eval.score import score_row
        with pytest.raises(ValueError, match="no usable reference"):
            score_row("Alice", "it", "floor", VOICED, ("", "  "), vocab)


class TestTheHeadlineFigureIsPinned:
    """Golden values for error-per-reference-segment and its aggregates.

    Without these the central measured quantity of the harness has no test
    that can tell a correct value from a wrong one: every other assertion
    here compares against 0, None or `is not None`, all of which survive an
    arithmetic change. Measured, then pinned -- a `/` turned `//` at
    score_row's division drops ('it','floor') raw_mean from 0.1696 to 0.0 and
    reports a near-correct engine as flawless, with the rest of this file
    still green.

    miss_ceiling's identical division is already pinned twice (TestMissCeiling
    and the self-check), which is exactly what made the gap easy to miss.
    """

    GOLDEN_ROWS = [
        ("Antonio", "it", "floor", "antonio", ("antonjo",)),
        ("Antonio", "it", "oracle", "antonjo", ("antonjo",)),
        ("Alice", "it", "floor", "alitʃe", ("ˈalitʃe",)),
        ("Alice", "it", "oracle", "alitse", ("ˈalitʃe",)),
    ]

    @pytest.fixture
    def rows(self, vocab):
        from eval.score import score_row
        return [score_row(n, l, e, o, r, vocab)
                for n, l, e, o, r in self.GOLDEN_ROWS]

    def test_one_substitution_in_a_seven_segment_name(self, rows):
        """antonio vs antonjo: one i-for-j substitution, 2.375 over 7."""
        row = rows[0]
        assert row.raw_distance == pytest.approx(2.375)
        assert row.reference_segments == 7
        assert row.raw_per_segment == pytest.approx(2.375 / 7)
        assert row.raw_per_segment == pytest.approx(0.3392857142857143)

    def test_the_mean_is_a_mean_and_not_a_sum_or_a_max(self, rows):
        from eval.score import summarise
        cells = summarise(rows)
        # (0.3392857... + 0.0) / 2
        assert cells[("it", "floor")].raw_mean == pytest.approx(0.16964285714285715)
        # (0.0 + 0.125) / 2
        assert cells[("it", "oracle")].raw_mean == pytest.approx(0.0625)

    def test_the_kokoro_side_mean_is_pinned_too(self, rows):
        """oracle's antonjo is a perfect pronunciation that Kokoro cannot
        voice: `j` is outside the vocabulary, so the clip loses a sound the
        raw figure says is correct. That gap is the column's reason to exist,
        and this is the only test that pins its arithmetic."""
        from eval.score import summarise
        cells = summarise(rows)
        assert cells[("it", "oracle")].raw_mean == pytest.approx(0.0625)
        assert cells[("it", "oracle")].kokoro_mean == pytest.approx(0.5803571428571429)
        assert cells[("it", "floor")].kokoro_mean == pytest.approx(0.16964285714285715)

    def test_the_interval_is_pinned_not_merely_bracketing(self, rows):
        """A seeded bootstrap over two values has an exactly reproducible
        interval; `lo <= mean <= hi` would survive a broken percentile."""
        from eval.score import summarise
        lo, hi = summarise(rows)[("it", "floor")].raw_ci
        assert lo == pytest.approx(0.0)
        assert hi == pytest.approx(0.3392857142857143)

    def test_the_ceiling_is_the_stated_far_end_of_the_scale(self, rows):
        from eval.score import summarise
        cells = summarise(rows)
        assert cells[("it", "floor")].ceiling_mean == pytest.approx(7.25)


class TestTheAsciiGFoldSeparatesTheTwoColumns:
    """The fold's whole value: it stops one defect reading as another.

    Before it, a correct pronunciation typed with ASCII g scored a flat 7.25
    -- the full-miss ceiling -- so "the LLM got the pronunciation wrong" and
    "the LLM typed the wrong codepoint" were the same number.
    """

    def test_the_pronunciation_reads_as_correct(self, vocab):
        from eval.score import score_row
        row = score_row("Magda", "pl", "today", "magda", ("maɡda",), vocab)
        assert row.raw_distance == 0.0
        assert row.raw_per_segment == 0.0

    def test_but_kokoro_still_charges_the_lost_sound(self, vocab):
        """The Kokoro column filters the *raw* output, where ASCII g is
        genuinely outside the 114-set -- so the audio loss is still measured,
        in the column that is about audio."""
        from eval.score import score_row
        row = score_row("Magda", "pl", "today", "magda", ("maɡda",), vocab)
        assert row.kokoro_output == "mada"
        assert row.kokoro_per_segment == pytest.approx(1.45)

    def test_the_correctly_typed_control_loses_nothing_anywhere(self, vocab):
        from eval.score import score_row
        row = score_row("Magda", "pl", "today", "maɡda", ("maɡda",), vocab)
        assert row.raw_per_segment == 0.0
        assert row.kokoro_per_segment == 0.0
        assert row.chars_dropped == 0


class TestBootstrap:
    def test_it_is_deterministic_so_re_scoring_reproduces_the_interval(self):
        """score is pure; an interval that moved between two runs of a pure
        function would be indistinguishable from a real change."""
        from eval.score import bootstrap_ci
        values = [0.1 * i for i in range(40)]
        assert bootstrap_ci(values) == bootstrap_ci(values)

    def test_the_interval_brackets_the_mean(self):
        from eval.score import bootstrap_ci
        values = [1.0, 1.2, 0.8, 1.1, 0.9, 1.3, 0.7, 1.05]
        lo, hi = bootstrap_ci(values)
        assert lo <= sum(values) / len(values) <= hi

    def test_a_single_value_has_a_degenerate_interval(self):
        from eval.score import bootstrap_ci
        assert bootstrap_ci([2.5]) == (2.5, 2.5)

    def test_no_values_gives_no_interval_rather_than_zero(self):
        """Zero would read as 'perfect', which is the opposite of 'unknown'."""
        from eval.score import bootstrap_ci
        assert bootstrap_ci([]) == (None, None)

    def test_a_wider_spread_gives_a_wider_interval(self):
        from eval.score import bootstrap_ci
        tight = bootstrap_ci([1.0] * 20 + [1.01] * 20)
        wide = bootstrap_ci([0.0] * 20 + [2.0] * 20)
        assert (wide[1] - wide[0]) > (tight[1] - tight[0])


class TestSharedSets:
    """Amendment D: the two figures use different, engine-intersected sets.

    Without this, an engine the guard refuses on most of a language shrinks
    that language's comparison set without saying so. Vietnamese does exactly
    that: eSpeak emits tone digits and the guard refuses every one.
    """

    @pytest.fixture
    def cells(self, vocab):
        from eval.score import score_row, summarise
        rows = [
            # Alpha: both voiced -> in both sets.
            score_row("Alpha", "it", "floor", VOICED, REF, vocab),
            score_row("Alpha", "it", "oracle", VOICED, REF, vocab),
            # Beta: floor refused but produced something -> raw set only.
            score_row("Beta", "it", "floor", REFUSED_DIGIT, REF, vocab),
            score_row("Beta", "it", "oracle", VOICED, REF, vocab),
            # Gamma: floor produced nothing -> neither set, for either engine.
            score_row("Gamma", "it", "floor", "", REF, vocab),
            score_row("Gamma", "it", "oracle", VOICED, REF, vocab),
        ]
        return summarise(rows)

    def test_raw_set_keeps_a_refused_name_because_the_engine_still_spoke(self, cells):
        assert cells[("it", "floor")].n_raw_scored == 2      # Alpha, Beta
        assert cells[("it", "oracle")].n_raw_scored == 2

    def test_kokoro_set_keeps_only_names_every_engine_voiced(self, cells):
        assert cells[("it", "floor")].n_kokoro_scored == 1    # Alpha
        assert cells[("it", "oracle")].n_kokoro_scored == 1

    def test_a_well_behaved_engine_is_held_to_the_shared_set_too(self, cells):
        """oracle voiced all three, but is scored on 2 and 1 -- otherwise the
        engines would be compared on different names."""
        oracle = cells[("it", "oracle")]
        assert oracle.n_voiced == 3
        assert (oracle.n_raw_scored, oracle.n_kokoro_scored) == (2, 1)

    def test_the_raw_and_voiced_counts_are_both_reported(self, cells):
        """So a shrunken set is visible rather than silent."""
        floor = cells[("it", "floor")]
        assert (floor.n_names, floor.n_produced_any, floor.n_voiced) == (3, 2, 1)

    def test_refusal_rate_and_reasons_are_both_available(self, cells):
        floor = cells[("it", "floor")]
        assert floor.refusal_rate == pytest.approx(2 / 3)    # digit + empty
        assert len(floor.refusal_reasons) == 2

    def test_an_all_refused_language_yields_no_kokoro_mean_rather_than_zero(self, vocab):
        """Vietnamese/oracle in miniature. 'empty, no comparison possible' is
        the honest answer; 0.0 would read as flawless."""
        from eval.score import score_row, summarise
        rows = [
            score_row("Thi", "vi", "oracle", "tˈi6", ("ti",), vocab),
            score_row("Thi", "vi", "floor", VOICED, ("ti",), vocab),
        ]
        cells = summarise(rows)
        oracle = cells[("vi", "oracle")]
        assert oracle.n_kokoro_scored == 0
        assert oracle.kokoro_mean is None and oracle.kokoro_ci == (None, None)
        assert oracle.n_raw_scored == 1          # raw still measures the engine

    def test_a_name_missing_a_row_for_one_engine_is_excluded(self, vocab):
        """A missing row is not a zero."""
        from eval.score import score_row, summarise
        rows = [
            score_row("Alpha", "it", "floor", VOICED, REF, vocab),
            score_row("Alpha", "it", "oracle", VOICED, REF, vocab),
            score_row("Solo", "it", "floor", VOICED, REF, vocab),
        ]
        cells = summarise(rows)
        assert cells[("it", "floor")].n_raw_scored == 1

    def test_the_ceiling_is_reported_so_the_scale_is_readable(self, cells):
        from eval.score import MISS_CEILING_PER_SEGMENT
        assert cells[("it", "floor")].ceiling_mean == pytest.approx(
            MISS_CEILING_PER_SEGMENT
        )

    def test_dropped_characters_are_aggregated_as_a_histogram(self, vocab):
        from eval.score import score_row, summarise
        rows = [score_row("A", "it", "floor", "(en)alitʃe", REF, vocab)]
        cell = summarise(rows)[("it", "floor")]
        assert cell.dropped_histogram == {"(": 1, ")": 1}
        assert cell.chars_dropped_per_name == 2.0

    def test_anything_normalisation_strips_can_never_reach_this_column(self, vocab):
        """The brief defines the column on the output *after* normalisation,
        so a stray digit or stress mark is gone before it is counted. What is
        left is genuinely unexpected residue -- which is this column's
        diagnostic value: it surfaces symbols nobody anticipated, never ones
        already handled."""
        from eval.score import score_row, summarise
        rows = [score_row("A", "it", "floor", "/ˈalitʃe5./", REF, vocab)]
        cell = summarise(rows)[("it", "floor")]
        assert cell.chars_dropped_per_name == 0.0
        assert cell.dropped_histogram == {}


class TestTheComparisonSetIsPerLanguage:
    """A run file may hold an engine for only some languages.

    `generate --langs vi --engines today` beside a floor/oracle run over all
    eight produces exactly that, and it is the designed use of --langs. With a
    run-wide engine list the shared set empties for every *other* language, so
    Italian's floor-vs-oracle gap -- the comparison the harness exists to make
    -- reads None because `today` ran on Vietnamese.
    """

    @pytest.fixture
    def rows(self, vocab):
        from eval.score import score_row
        return [
            score_row("Alpha", "it", "floor", VOICED, REF, vocab),
            score_row("Alpha", "it", "oracle", "alitse", REF, vocab),
            score_row("Thi", "vi", "floor", VOICED, REF, vocab),
            score_row("Thi", "vi", "oracle", VOICED, REF, vocab),
            # `today` smoke-tested on one language only.
            score_row("Thi", "vi", "today", VOICED, REF, vocab),
        ]

    def test_a_language_an_engine_skipped_keeps_its_other_engines_figures(self, rows):
        from eval.score import summarise
        cells = summarise(rows)
        floor, oracle = cells[("it", "floor")], cells[("it", "oracle")]
        assert floor.n_raw_scored == 1 and oracle.n_raw_scored == 1
        assert floor.raw_mean == pytest.approx(0.0)
        assert oracle.raw_mean == pytest.approx(0.125)
        assert floor.ceiling_mean == pytest.approx(7.25)

    def test_the_absent_engine_simply_has_no_cell_there(self, rows):
        from eval.score import summarise
        cells = summarise(rows)
        assert ("it", "today") not in cells
        assert ("vi", "today") in cells

    def test_each_cell_records_what_it_was_intersected_against(self, rows):
        """So a shrunken denominator can be explained rather than guessed at:
        the Italian cells were compared on two engines, the Vietnamese on
        three, and the summary can say so."""
        from eval.score import summarise
        cells = summarise(rows)
        assert cells[("it", "floor")].engines_compared == ("floor", "oracle")
        assert cells[("vi", "floor")].engines_compared == ("floor", "oracle", "today")

    def test_a_language_where_every_engine_ran_still_intersects_all_of_them(self, rows):
        from eval.score import summarise
        cells = summarise(rows)
        assert cells[("vi", "today")].n_raw_scored == 1
        assert all(cells[("vi", e)].n_raw_scored == 1
                   for e in ("floor", "oracle", "today"))


@pytest.mark.skipif(not HF_HUB_AVAILABLE, reason="huggingface_hub not installed")
class TestPublishedVocabulary:
    def test_the_harness_s_assumptions_about_the_real_vocabulary_hold(self):
        """The offline tests use a synthetic fixture, so nothing else here
        would notice Kokoro republishing its config. Mirrors
        app/tests/test_models.py:484-515, which guards the app's constants the
        same way.
        """
        from eval.kokoro_vocab import fetch
        try:
            vocab, revision = fetch()
        except Exception as e:
            pytest.skip(f"Kokoro config unreachable ({type(e).__name__}: {e})")

        assert len(vocab) == 114, len(vocab)
        # Ligatures are in, so normalisation's expansion loses nothing.
        for ch in "ʤʧʦʣʥʨ":
            assert ch in vocab, ch
        # ...and the three it leaves alone are out, which is why it does.
        for ch in "ʩʪʫ":
            assert ch not in vocab, ch
        # The g pair normalisation folds for, and the one fact the offline
        # fixture mirrors rather than inventing: every ASCII lowercase letter
        # is in the set EXCEPT g, which is U+0261 here.
        assert "\u0261" in vocab
        assert "g" not in vocab
        for ch in "abcdefhijklmnopqrstuvwxyz":
            assert ch in vocab, ch
        # Length is in; half-length is not.
        assert "ː" in vocab and "ˑ" not in vocab
        # Space is in, so word boundaries survive the filter.
        assert " " in vocab
        # Tones and digits are out -- the mechanism behind the Kokoro-side gap.
        for ch in "˥˦˧˨˩" + "0123456789":
            assert ch not in vocab, ch
        # Pharyngeals, the retroflex fricative and the tie bar are out.
        for ch in "ħʕʐʑ͡":
            assert ch not in vocab, ch
        # ...and the vocabulary holds symbols that are not IPA at all.
        from eval.kokoro_vocab import NON_IPA_IN_VOCAB
        assert NON_IPA_IN_VOCAB <= vocab, sorted(NON_IPA_IN_VOCAB - vocab)
