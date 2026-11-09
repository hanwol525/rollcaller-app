"""The gate. Unit 2's done-criterion, as a test.

Each case breaks exactly one normalisation rule and asserts scoring refuses
to start. Several of them change no distance at all -- the four rules panphon
already ignores, plus both Unicode-form steps -- so only the string-level and
diagnostic tiers catch them, which is the whole reason those tiers exist.
"""
from __future__ import annotations

import pytest

from eval.tests._deps import PANPHON_AVAILABLE

pytestmark = pytest.mark.skipif(not PANPHON_AVAILABLE, reason="panphon not installed")

LIGATURE_DZ = "ʤ"
LIGATURE_DZ_CURL = "ʥ"
ASCII_G = "g"
TIE_BAR_ABOVE = "͡"
CHAO_HIGH = "˥"
STRESS_PRIMARY = "ˈ"
LENGTH = "ː"


@pytest.fixture
def rules():
    """Hand back the rule tables, restored afterwards whatever the test did.

    Mutated in place rather than rebound, because ``normalise()`` reads the
    module-level tables at call time -- which is exactly how a real edit to
    the rules would propagate to every importer.
    """
    from eval import normalise as module
    delete = dict(module._DELETE_TABLE)
    substitutions = dict(module._SUBSTITUTION_TABLE)
    try:
        yield module
    finally:
        module._DELETE_TABLE.clear()
        module._DELETE_TABLE.update(delete)
        module._SUBSTITUTION_TABLE.clear()
        module._SUBSTITUTION_TABLE.update(substitutions)


class TestPasses:
    def test_the_pristine_scorer_passes(self, vocab):
        from eval.selfcheck import run
        run(vocab)   # must not raise

    def test_it_is_safe_to_run_twice(self, vocab):
        from eval.selfcheck import run
        run(vocab)
        run(vocab)


class TestABrokenRuleAbortsScoring:
    @pytest.mark.parametrize("label,char", [
        ("tie bar U+0361", TIE_BAR_ABOVE),
        ("Chao tone letter", CHAO_HIGH),
    ])
    def test_load_bearing_rules(self, rules, vocab, label, char):
        from eval.selfcheck import SelfCheckFailed, run
        rules._DELETE_TABLE.pop(ord(char))
        with pytest.raises(SelfCheckFailed):
            run(vocab)

    @pytest.mark.parametrize("char", [LIGATURE_DZ, LIGATURE_DZ_CURL])
    def test_ligature_expansion(self, rules, vocab, char):
        from eval.selfcheck import SelfCheckFailed, run
        rules._SUBSTITUTION_TABLE.pop(ord(char))
        with pytest.raises(SelfCheckFailed, match=char):
            run(vocab)

    def test_the_ascii_g_fold(self, rules, vocab):
        """The costliest single-character gap in the scheme: panphon reads
        U+0261 and segments ASCII g as nothing, so a correct pronunciation
        typed with the wrong codepoint scores the full 7.25 miss."""
        from eval.selfcheck import SelfCheckFailed, run
        rules._SUBSTITUTION_TABLE.pop(ord(ASCII_G))
        with pytest.raises(SelfCheckFailed, match="ASCII g"):
            run(vocab)

    @pytest.mark.parametrize("label,char", [
        ("stress mark", STRESS_PRIMARY),
        ("tone digit", "5"),
        ("syllable dot", "."),
        ("wrapper slash", "/"),
    ])
    def test_rules_that_change_no_distance_are_still_caught(
        self, rules, vocab, label, char
    ):
        """panphon already ignores all four, so every distance in the
        self-check would stay exactly 0 with the rule gone. Only the
        string-level tier sees this."""
        from eval.selfcheck import SelfCheckFailed, run
        rules._DELETE_TABLE.pop(ord(char))
        with pytest.raises(SelfCheckFailed):
            run(vocab)

    def test_reading_the_wrong_unicode_form_in_normalise_is_caught(
        self, monkeypatch, vocab
    ):
        """NFD moves no distance -- panphon decomposes its own input -- so the
        distance tier is blind to this. The string tier is not."""
        from eval import normalise as module
        from eval.selfcheck import SelfCheckFailed, run
        monkeypatch.setattr(module, "UNICODE_FORM", "NFC")
        with pytest.raises(SelfCheckFailed, match="NFD"):
            run(vocab)

    def test_reading_the_wrong_unicode_form_in_the_drop_walk_is_caught(
        self, monkeypatch, vocab
    ):
        """The second placement, checked independently: seg_regex matches only
        the decomposed form, so a recomposing walk reports characters as
        dropped that the distance scored as whole segments."""
        from eval import score
        from eval.selfcheck import SelfCheckFailed, run
        monkeypatch.setattr(score, "UNICODE_FORM", "NFC")
        with pytest.raises(SelfCheckFailed, match="dropped"):
            run(vocab)

    def test_wrongly_stripping_length_is_caught(self, rules, vocab):
        """The opposite failure: over-folding. A normaliser that folded
        everything would score 0 on everything."""
        from eval.selfcheck import SelfCheckFailed, run
        rules._DELETE_TABLE[ord(LENGTH)] = None
        with pytest.raises(SelfCheckFailed, match="KEPT"):
            run(vocab)

    def test_over_folding_a_real_phoneme_is_caught(self, rules, vocab):
        """The other direction of over-folding: stripping a consonant would
        make every engine look closer to every reference than it is."""
        from eval.selfcheck import SelfCheckFailed, run
        rules._DELETE_TABLE[ord("n")] = None
        with pytest.raises(SelfCheckFailed):
            run(vocab)

    def test_the_failure_names_every_broken_rule_not_just_the_first(
        self, rules, vocab
    ):
        """Fixing one rule at a time against a self-check that reports one
        failure at a time is how a second breakage stays hidden."""
        from eval.selfcheck import SelfCheckFailed, run
        rules._DELETE_TABLE.pop(ord(STRESS_PRIMARY))
        rules._DELETE_TABLE.pop(ord("."))
        with pytest.raises(SelfCheckFailed) as excinfo:
            run(vocab)
        message = str(excinfo.value)
        assert "stress" in message and "syllable dot" in message
        assert message.splitlines()[0].startswith("2 self-check failure")


class TestScaleDrift:
    def test_a_moved_panphon_scale_aborts_scoring(self, monkeypatch, vocab):
        """Every summary states a 0-to-7.25 range. If panphon's feature
        weights changed, that sentence becomes false and the numbers become
        incomparable with any earlier baseline."""
        from eval import score, selfcheck
        monkeypatch.setattr(score, "MISS_CEILING_PER_SEGMENT", 6.0)
        monkeypatch.setattr(selfcheck, "MISS_CEILING_PER_SEGMENT", 6.0)
        with pytest.raises(selfcheck.SelfCheckFailed, match="scale moved"):
            selfcheck.run(vocab)
