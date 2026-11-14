"""The normalisation rules, and which of them actually move a number."""
from __future__ import annotations

import unicodedata

import pytest

from eval.normalise import (CHAO_TONES, LENGTH_MARKS, LIGATURES, LOOKALIKES,
                            RULESET_VERSION, STRESS, TIE_BARS, UNICODE_FORM,
                            WRAPPERS, normalise)
from eval.tests._deps import PANPHON_AVAILABLE


class TestRules:
    @pytest.mark.parametrize("raw,expected", [
        ("/a/", "a"), ("[a]", "a"), ("/ˈælɪs/", "ælɪs"),
    ])
    def test_wrappers_come_off(self, raw, expected):
        """IPAConfirmRequest.ipa is an unvalidated str, so a participant
        confirming /ˈælɪs/ in dictionary notation puts slashes in the row."""
        assert normalise(raw) == expected

    @pytest.mark.parametrize("mark", list(STRESS))
    def test_stress_marks_come_off(self, mark):
        assert normalise(f"a{mark}b") == "ab"

    def test_syllable_dots_come_off(self):
        assert normalise("ba.bo") == "babo"

    @pytest.mark.parametrize("bar", list(TIE_BARS))
    def test_both_tie_bars_come_off(self, bar):
        assert normalise(f"t{bar}ʃ") == "tʃ"

    @pytest.mark.parametrize("ligature,expansion", sorted(LIGATURES.items()))
    def test_ligature_affricates_expand(self, ligature, expansion):
        assert normalise(ligature) == expansion

    @pytest.mark.parametrize("source,target", sorted(LOOKALIKES.items()))
    def test_codepoint_lookalikes_fold(self, source, target):
        """Same sound, different codepoint. panphon reads only one of them."""
        assert normalise(source) == target

    def test_ascii_g_folds_mid_word(self):
        """The reachable case: an LLM types the ASCII letter inside a name."""
        assert normalise("groːs") == normalise("ɡroːs")
        assert normalise("maɡda") == normalise("magda")

    @pytest.mark.parametrize("precomposed", ["ç", "ã", "õ", "ĩ", "ũ", "ẽ"])
    def test_precomposed_characters_are_decomposed(self, precomposed):
        """Every dataset reference is NFC; panphon segments only NFD."""
        decomposed = unicodedata.normalize("NFD", precomposed)
        assert len(decomposed) > 1, "fixture is not actually decomposable"
        assert normalise(precomposed) == decomposed
        assert normalise(precomposed) == normalise(decomposed)

    def test_non_decomposable_precomposed_letters_are_left_alone(self):
        """æ ø œ have no decomposition and panphon reads them as they are."""
        for ch in "æøœ":
            assert normalise(ch) == ch

    def test_the_unicode_form_is_the_one_panphon_uses(self):
        """Not a free choice: panphon.FeatureTable.normalize is NFD, and the
        whole point is to read the form panphon reads."""
        assert UNICODE_FORM == "NFD"

    @pytest.mark.parametrize("mark", list(LENGTH_MARKS))
    def test_length_marks_are_kept(self, mark):
        """Vowel length is part of a pronunciation: aː is a different segment
        from a, not a notational variant of it."""
        assert normalise(f"a{mark}") == f"a{mark}"

    @pytest.mark.parametrize("tone", list(CHAO_TONES))
    def test_chao_tone_letters_come_off(self, tone):
        assert normalise(f"ta{tone}") == "ta"

    @pytest.mark.parametrize("digit", list("0123456789"))
    def test_tone_digits_come_off(self, digit):
        assert normalise(f"ta{digit}") == "ta"

    def test_whitespace_is_collapsed(self):
        assert normalise("  a   b  ") == "a b"

    def test_empty_stays_empty(self):
        assert normalise("") == ""


class TestProperties:
    @pytest.mark.parametrize("raw", [
        "/ˈælɪs/", "ʤu", "t͡ʃi", "ta˥",
        "aː", "ŋwˈiɛ5n", "", "  ",
    ])
    def test_idempotent(self, raw):
        once = normalise(raw)
        assert normalise(once) == once

    def test_a_ligature_expansion_is_not_then_itself_stripped(self):
        """Order matters: expanding ʧ to tʃ must not have its parts removed."""
        assert normalise("ʧ") == "tʃ"
        assert normalise("ʦ") == "ts"

    def test_no_substitution_is_disturbed_by_the_unicode_step(self):
        """NFD runs first, so a key or expansion it altered would silently
        change which rule fires. None of them decompose -- this pins that."""
        for text in (*LIGATURES, *LIGATURES.values(),
                     *LOOKALIKES, *LOOKALIKES.values(), *LENGTH_MARKS):
            assert unicodedata.normalize(UNICODE_FORM, text) == text

    def test_the_ruleset_is_versioned(self):
        """Recorded in provenance, so two summaries that normalised
        differently cannot be mistaken for comparable."""
        assert RULESET_VERSION and isinstance(RULESET_VERSION, str)


@pytest.mark.skipif(not PANPHON_AVAILABLE, reason="panphon not installed")
class TestWhichRulesMoveTheNumber:
    """Measured, not assumed: only four rules change a distance.

    This matters because it decides what the self-check has to assert. A
    distance-only self-check cannot see a broken stress, dot or digit rule --
    hence selfcheck's second, string-level tier.
    """

    def _d(self, a, b):
        from eval.score import distance
        return distance(a, b)

    @pytest.mark.parametrize("a,b", [
        ("t͡ʃ", "tʃ"),        # tie bar above: one segment vs two
        ("ʤ", "dʒ"),               # ligature: panphon segments ʤ as nothing
        ("ʥ", "dʑ"),               # ...and ʥ, which Kokoro can voice
        ("ʨ", "tɕ"),               # ...and ʨ
        ("groːs", "ɡroːs"),         # ASCII g: a flat 7.25 full-miss
        ("ta˥", "ta"),                  # Chao letters ARE segmented
    ])
    def test_load_bearing_rules_change_the_raw_distance(self, a, b):
        assert self._d(a, b) > 0
        assert self._d(normalise(a), normalise(b)) == 0

    @pytest.mark.parametrize("a,b", [
        ("ˈbob", "bob"),                # panphon already ignores stress
        ("ba.bo", "babo"),                   # ...and syllable dots
        ("ta5", "ta"),                       # ...and digits
        ("t͜ʃ", "tʃ"),        # tie bar BELOW already segments apart
        ("çetin", "c\u0327etin"),            # panphon decomposes internally
    ])
    def test_the_other_rules_are_no_ops_for_distance(self, a, b):
        """They are still applied, because they would otherwise be counted as
        characters dropped -- noise in a column meant to be diagnostic."""
        assert self._d(a, b) == 0
        assert self._d(normalise(a), normalise(b)) == 0
