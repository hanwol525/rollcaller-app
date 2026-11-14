"""Argument parsing and run-file naming. No engine, no network."""
from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from eval import __main__ as cli
from eval.dataset import LANGUAGES


class TestArgumentValidation:
    def test_all_expands_to_every_dataset_language(self):
        assert cli._langs("all") == LANGUAGES

    def test_an_unknown_language_is_rejected_with_the_valid_set(self):
        with pytest.raises(argparse.ArgumentTypeError, match="unknown language"):
            cli._langs("it,klingon")

    def test_an_unknown_engine_is_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError, match="unknown engine"):
            cli._engines("floor,allosaurus")

    @pytest.mark.parametrize("value,expected", [
        ("it,it,vi", ("it", "vi")),
        ("vi,it,vi", ("vi", "it")),
        (" it , vi ", ("it", "vi")),
    ])
    def test_repeated_languages_are_de_duplicated_in_order(self, value, expected):
        assert cli._langs(value) == expected

    def test_repeated_engines_are_de_duplicated(self):
        """`--engines floor,floor` would otherwise write two rows for one
        (name, lang, engine) -- the one shape runfile.read refuses, so
        generate would produce a file score could not read."""
        assert cli._engines("floor,floor,oracle") == ("floor", "oracle")

    def test_order_given_is_preserved(self):
        assert cli._engines("oracle,floor") == ("oracle", "floor")


class TestRunPath:
    def args(self, split="dev", langs=("it",), engines=("floor",)):
        return SimpleNamespace(split=split, langs=langs, engines=engines)

    def prov(self, when="2026-11-09T17:03:22+00:00"):
        return SimpleNamespace(generated_at=when)

    def test_the_name_carries_split_languages_engines_and_a_stamp(self, tmp_path,
                                                                  monkeypatch):
        monkeypatch.setattr(cli, "RUNS_DIR", tmp_path)
        path = cli._run_path(self.args(langs=("it", "vi")), self.prov())
        assert path.name == "dev-itvi-floor-20261109T170322.jsonl"

    def test_every_language_collapses_to_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "RUNS_DIR", tmp_path)
        path = cli._run_path(self.args(langs=LANGUAGES), self.prov())
        assert "-all-" in path.name

    def test_two_runs_in_the_same_second_over_different_langs_do_not_collide(
        self, tmp_path, monkeypatch
    ):
        """The stamp is second-resolution, so without the languages in the
        name the second run would silently replace the first."""
        monkeypatch.setattr(cli, "RUNS_DIR", tmp_path)
        a = cli._run_path(self.args(langs=("it",)), self.prov())
        b = cli._run_path(self.args(langs=("vi",)), self.prov())
        assert a != b

    def test_an_existing_file_is_suffixed_rather_than_overwritten(self, tmp_path,
                                                                  monkeypatch):
        """A run file is the only record of a generate, and for `today` the
        only record of money spent."""
        monkeypatch.setattr(cli, "RUNS_DIR", tmp_path)
        first = cli._run_path(self.args(), self.prov())
        first.write_text("existing", encoding="utf-8")
        second = cli._run_path(self.args(), self.prov())
        assert second != first
        assert second.name.endswith("-2.jsonl")
        assert first.read_text(encoding="utf-8") == "existing"

    def test_it_keeps_suffixing_past_the_second_collision(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "RUNS_DIR", tmp_path)
        for expected in ("", "-2", "-3"):
            path = cli._run_path(self.args(), self.prov())
            assert path.stem.endswith(expected) or not expected
            path.write_text("x", encoding="utf-8")


class TestTheRunHeaderIsSelfDescribing:
    """A run file has to say what produced it without inference.

    `espeak_voices` is recoverable from `espeak_warm_seconds`' key order, but
    positionally only -- and which voice spoke a name is the provenance a
    pronunciation baseline turns on.
    """

    @pytest.fixture
    def header(self, tmp_path, monkeypatch):
        """A header built by cmd_generate's own code path and round-tripped
        through a run file in tmp_path -- not a real run from the gitignored
        runs/ directory, which is empty on a fresh clone."""
        from phonemizer.backend import EspeakBackend

        from eval import engines, runfile
        from eval.dataset import espeak_code
        # Every voice the corpus needs, plus the floor's, so validation
        # passes without asking espeak-ng.
        voices = {espeak_code(l): l for l in LANGUAGES} | {"en-us": "en"}
        monkeypatch.setattr(EspeakBackend, "supported_languages",
                            staticmethod(lambda: voices))
        args = SimpleNamespace(split="dev", langs=LANGUAGES, engines=("floor",),
                               limit=1, extra_payload=None)
        prov = SimpleNamespace(as_dict=lambda: {})
        codes = engines.espeak_codes(LANGUAGES)
        built = cli._run_header(args, codes, {c: 0.1 for c in codes.values()},
                                prov, 0)
        return runfile.read(runfile.write(tmp_path / "run.jsonl", built, [])).header

    def test_the_voice_used_for_each_language_is_recorded_as_data(self, header):
        from eval.dataset import ESPEAK_CODE
        assert header["espeak_voices"]["fr"] == ESPEAK_CODE["fr"] == "fr-fr"
        assert set(header["espeak_voices"]) == set(header["langs"])

    def test_every_other_language_maps_to_its_own_code(self, header):
        voices = header["espeak_voices"]
        assert [l for l, v in voices.items() if l != v] == ["fr"]


class TestTheProbeCommand:
    def test_levers_and_extra_payload_together_are_refused(self, capsys):
        """The first attempt in a --levers sweep is the baseline every lever
        is judged against. Folding --extra-payload into it would label a
        modified request 'as-shipped' -- the exact mislabel the command
        exists to prevent."""
        from eval.__main__ import main
        assert main(["probe", "--levers",
                     "--extra-payload", '{"reasoning":{"enabled":false}}']) == 2
        assert "one or the other" in capsys.readouterr().err

    @pytest.mark.parametrize("reasons,sent", [(False, 1), (True, 5)])
    def test_levers_are_only_sent_when_the_baseline_reasons(
        self, monkeypatch, reasons, sent
    ):
        """Each lever is a paid request. If as-shipped shows no reasoning,
        thinking-off is the same configuration and there is nothing to try."""
        from eval import llm
        from eval.__main__ import main
        calls = []

        def fake_probe(extra_payload=None, name=None):
            calls.append(extra_payload)
            return SimpleNamespace(
                reasons_by_default=reasons if extra_payload is None else False,
                render=lambda: "")

        monkeypatch.setattr(llm, "require_configured",
                            lambda: {"model_id": "m", "model_host": "h"})
        monkeypatch.setattr(llm, "probe", fake_probe)
        assert main(["probe", "--levers"]) == 0
        assert len(calls) == sent
        assert calls[0] is None

    def test_extra_payload_must_be_a_json_object(self):
        from eval.__main__ import main
        for bad in ("[1,2]", "not json", '"a string"', "7"):
            with pytest.raises(SystemExit):
                main(["probe", "--extra-payload", bad])

    def test_probing_without_credentials_exits_two_not_zero(
        self, capsys, monkeypatch
    ):
        """It must not look like a successful probe that found nothing."""
        from app.config import settings

        from eval.__main__ import main
        monkeypatch.setattr(settings, "gemma_api_key", None)
        assert main(["probe"]) == 2
        assert "CANNOT PROBE" in capsys.readouterr().err


class TestGenerateRefusesAnUnpayableRun:
    def test_today_without_credentials_exits_before_warming(
        self, capsys, monkeypatch
    ):
        """Warming eight eSpeak backends costs ~20s. The precondition has to
        fire first, or an unrunnable `today` wastes that every time."""
        from app.config import settings

        from eval.__main__ import main
        monkeypatch.setattr(settings, "gemma_base_url", None)
        assert main(["generate", "--split", "dev", "--engines", "today",
                     "--limit", "1"]) == 2
        captured = capsys.readouterr()
        assert "TODAY IS NOT RUNNABLE" in captured.err
        assert "warmed" not in captured.out, (
            "backends were warmed before the precondition fired")

    def test_the_espeak_engines_are_refused_on_a_pool(self):
        """g2p._espeak_lock serialises phonemize(), so a pool buys nothing --
        and without the lock espeak returns another name's phonemes on most
        concurrent calls (g2p.py:40-60)."""
        from eval import engines
        with pytest.raises(engines.EngineUnavailable, match="refusing to run"):
            engines.run("floor", [], workers=4)

    def test_extra_payload_is_refused_for_an_engine_that_sends_no_request(self):
        from eval import engines
        with pytest.raises(engines.EngineUnavailable, match="makes\nno request|no request"):
            engines.run("oracle", [], extra_payload={"reasoning": {}})
