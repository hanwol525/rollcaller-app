"""The engines, and the two things about them that fail silently.

P16's validation ordering and LogWatch's attachment point are both "works
until it doesn't, and then measures nothing" -- so each has a test that fails
if the protection is removed.
"""
from __future__ import annotations

import logging

import pytest

from eval.tests._deps import PHONEMIZER_AVAILABLE

needs_espeak = pytest.mark.skipif(
    not PHONEMIZER_AVAILABLE, reason="phonemizer/espeak-ng not installed"
)


class TestEngineRegistry:
    def test_the_three_engines_are_named_and_only_three(self):
        from eval import engines
        assert set(engines.ENGINES) == {"floor", "oracle", "today"}
        assert engines.ENGINE_NAMES == ("floor", "oracle", "today")

    def test_today_calls_g2p_not_espeak_directly(self, monkeypatch):
        """`today` must go through g2p() so the LLM branch is exercised.

        Calling espeak_ipa directly would make `today` a second copy of
        `floor` -- identical numbers, and no way to tell from the output that
        the LLM was never consulted.
        """
        from app.pronunciation import g2p

        from eval import engines
        seen = []
        monkeypatch.setattr(g2p, "g2p", lambda text: seen.append(text) or "ok")
        monkeypatch.setattr(
            g2p, "espeak_ipa",
            lambda *a, **k: pytest.fail("today bypassed g2p()"))
        assert engines.today("Alice", "it") == "ok"
        assert seen == ["Alice"]

    def test_today_ignores_the_language_it_is_given(self):
        """g2p() takes no `language` parameter (P13). That is the defect being
        measured: today's path has no idea what language a name is."""
        import inspect

        from app.pronunciation import g2p
        assert "language" not in inspect.signature(g2p.g2p).parameters

    def test_an_unknown_engine_is_refused_before_any_work(self):
        from eval import engines
        with pytest.raises(engines.EngineUnavailable, match="unknown engine"):
            engines.run("gemma", [])


@needs_espeak
class TestValidationHappensBeforeWarming:
    """P16. g2p.warm swallows an unsupported language and caches a *sticky*
    None (g2p.py:30-34), so a typo'd code does not raise: it returns "" for
    every name in that language and reads as a 100% refusal rate.
    """

    def test_every_shipped_code_is_supported_by_this_espeak(self):
        from eval import engines
        from eval.dataset import LANGUAGES
        codes = engines.espeak_codes()
        assert set(codes) == set(LANGUAGES)
        assert codes["fr"] == "fr-fr", "eSpeak has no bare `fr` voice (P5)"

    def test_a_bad_code_raises_with_nothing_warmed(self, monkeypatch):
        from app.pronunciation import g2p
        from eval import dataset, engines
        monkeypatch.setitem(dataset.ESPEAK_CODE, "fr", "fr-FRANCE")
        monkeypatch.setattr(g2p, "_backends", {})
        with pytest.raises(engines.EngineUnavailable, match="does not support"):
            engines.warm(("fr",))
        assert g2p._backends == {}, (
            "a backend was built before validation; a sticky None would then "
            "make this language read as 100% refused rather than as an error"
        )

    def test_the_error_names_the_language_and_the_code(self, monkeypatch):
        from eval import dataset, engines
        monkeypatch.setitem(dataset.ESPEAK_CODE, "vi", "vietnamese")
        with pytest.raises(engines.EngineUnavailable) as excinfo:
            engines.espeak_codes(("vi",))
        assert "'vietnamese'" in str(excinfo.value) and "'vi'" in str(excinfo.value)

    def test_floors_own_voice_is_validated_too(self, monkeypatch):
        """floor uses DEFAULT_LANGUAGE, not a dataset code, so it would
        otherwise be the one voice nothing checks."""
        from eval import engines
        monkeypatch.setattr(engines, "DEFAULT_LANGUAGE", "en-NOWHERE")
        with pytest.raises(engines.EngineUnavailable, match="en-NOWHERE"):
            engines.espeak_codes(("it",))


class TestLogWatch:
    def test_it_attaches_to_the_root_not_to_the_phonemizer_logger(self):
        """**The gotcha this guards.** phonemizer's get_logger() runs
        `logger.handlers = []` and BaseBackend.__init__ calls it on *every*
        backend construction, so a handler on the `phonemizer` logger is
        destroyed by the next warm() -- silently, and the word-count-mismatch
        column would then measure nothing at all.
        """
        from eval import engines
        with engines.LogWatch() as watch:
            assert watch in logging.getLogger().handlers
            assert watch not in logging.getLogger("phonemizer").handlers

    def test_it_survives_the_handler_wipe_that_building_a_backend_performs(self):
        from phonemizer.logger import get_logger

        from eval import engines
        with engines.LogWatch() as watch:
            get_logger()          # exactly what BaseBackend.__init__ does
            logging.getLogger("phonemizer").warning("words count mismatch on 1")
            assert any("words count mismatch" in r.getMessage() for r in watch.records)

    def test_it_captures_the_watched_loggers(self):
        from eval import engines
        with engines.LogWatch() as watch:
            logging.getLogger("app.pronunciation.g2p").warning("no IPA for name='x'")
            assert len(watch.records) == 1

    def test_it_ignores_loggers_it_was_not_asked_to_watch(self):
        from eval import engines
        with engines.LogWatch() as watch:
            logging.getLogger("httpx").warning("unrelated")
            assert watch.records == []

    def test_it_detaches_and_restores_the_root_level_on_exit(self):
        from eval import engines
        root = logging.getLogger()
        before = root.level
        with engines.LogWatch() as watch:
            pass
        assert watch not in root.handlers
        assert root.level == before

    def test_drain_empties_so_records_belong_to_one_name_only(self):
        from eval import engines
        with engines.LogWatch() as watch:
            logging.getLogger("phonemizer").warning("first")
            assert len(watch.drain()) == 1
            assert watch.drain() == []


@needs_espeak
class TestTheEnginesCallWhatTheyClaimTo:
    def test_floor_ignores_the_language_it_is_given(self):
        """This is the defect being measured, not a bug here: g2p() falls
        through to espeak_ipa(text) with no language, so every name on the
        roster is read as American English however it is spelled."""
        from eval import engines
        engines.warm(("de",))
        assert engines.floor("Gross", "de") == engines.floor("Gross", "vi")

    def test_oracle_uses_the_mapped_voice_and_differs_from_floor(self):
        from eval import engines
        engines.warm(("de",))
        assert engines.oracle("Gross", "de") != engines.floor("Gross", "de")

    def test_oracle_maps_fr_to_fr_fr_rather_than_failing(self):
        from app.pronunciation import g2p
        from eval import engines
        engines.warm(("fr",))
        assert engines.oracle("Abby", "fr") == g2p.espeak_ipa("Abby", language="fr-fr")

    def test_run_returns_one_output_per_record_in_file_order(self):
        from eval import engines
        from eval.dataset import NameRecord
        engines.warm(("it",))
        records = [NameRecord(n, "it", ("given",), ("x",), "rare", 0.0)
                   for n in ("Adele", "Ademaro", "Abatemarco")]
        out = engines.run("oracle", records, progress_every=0)
        assert [o.name for o in out] == ["Adele", "Ademaro", "Abatemarco"]
        assert all(o.engine == "oracle" and o.output for o in out)

    def test_it_records_the_narration_signal_against_the_right_name(self):
        """phonemizer warns when input and output word counts differ, which is
        how an unreadable script shows up: one word in, a narration of several
        out. Drained per name so it cannot be attributed to the next one."""
        from eval import engines
        from eval.dataset import NameRecord
        engines.warm(("ar",))
        records = [NameRecord(n, "ar", ("given",), ("x",), "rare", 0.0)
                   for n in ("فاطمة", "Adele")]
        out = {o.name: o for o in engines.run("floor", records, progress_every=0)}
        assert out["فاطمة"].word_count_mismatch is True
        assert out["Adele"].word_count_mismatch is False
