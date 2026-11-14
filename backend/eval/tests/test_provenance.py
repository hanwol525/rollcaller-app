"""The run header. Offline: nothing here reaches the network."""
from __future__ import annotations

import pytest

from eval import kokoro_vocab, provenance
from eval.provenance import EXPECTED_ESPEAK, Provenance, collect

BARE = dict(
    generated_at="2026-11-05T00:00:00+00:00",
    git_commit="a" * 40, git_dirty=False,
    espeak_version="1.52.0", espeak_library="/usr/local/lib/libespeak-ng.dylib",
    espeak_warning=None,
    dataset_repo="saraelhark/say-my-name", dataset_revision="c7d27b1e" + "f" * 32,
    split="dev", kokoro_repo="hexgrad/Kokoro-82M", kokoro_revision="f3ff3571",
    panphon_version="0.22.2", wordfreq_version="3.1.1",
    huggingface_hub_version="1.23.0", python_version="3.11.0", platform="Darwin x86_64",
)


@pytest.fixture
def no_network(monkeypatch):
    monkeypatch.setattr(kokoro_vocab, "fetch", lambda cache_dir=None: (frozenset("ab"), "deadbeef"))


class TestRender:
    def test_every_recorded_field_reaches_the_header(self):
        text = Provenance(**BARE).render()
        for value in ("1.52.0", "c7d27b1e", "0.22.2", "3.1.1", "1.23.0", "3.11.0", "f3ff3571"):
            assert value in text, value

    def test_a_dirty_tree_is_called_out(self):
        """A baseline measured against uncommitted edits is not reproducible
        from its commit, so it has to say so on its face."""
        assert "DIRTY" in Provenance(**{**BARE, "git_dirty": True}).render()
        assert "DIRTY" not in Provenance(**BARE).render()

    def test_normalisation_is_marked_pending_until_score_time(self):
        assert "set at score time" in Provenance(**BARE).render()
        assert "ruleset-3" in Provenance(**BARE, ruleset_version="ruleset-3").render()

    def test_espeak_only_engines_say_so_rather_than_showing_a_blank(self):
        text = Provenance(**BARE).render()
        assert "no model; eSpeak-only engines" in text

    def test_a_model_row_renders_its_host_and_temperature(self):
        text = Provenance(
            **BARE, model_id="google/gemma-4-31b-it",
            model_host="https://openrouter.ai/api/v1", temperature=0,
        ).render()
        assert "google/gemma-4-31b-it" in text and "openrouter.ai" in text
        assert "temperature" in text and "| 0" not in text

    def test_an_espeak_warning_is_surfaced_as_a_blockquote(self):
        text = Provenance(**{**BARE, "espeak_warning": "espeak-ng is (1, 51, 0)"}).render()
        assert "⚠️" in text and "(1, 51, 0)" in text


class TestEspeakProbe:
    def test_the_expected_version_is_the_one_the_app_suite_pins(self):
        """app/tests/test_models.py asserts (1, 52, 0) and derives the garbage
        guard's letter names under it. Drifting apart silently would let a
        baseline claim comparability it does not have."""
        assert EXPECTED_ESPEAK == (1, 52, 0)

    def test_a_different_version_produces_a_warning_not_an_exception(self, monkeypatch):
        import phonemizer.backend.espeak.wrapper as w
        monkeypatch.setattr(w.EspeakWrapper, "version", property(lambda self: (1, 51, 0)))
        version, _library, warning = provenance._espeak()
        assert version == "1.51.0"
        assert warning and "not (1, 52, 0)" in warning

    def test_an_unprobeable_espeak_is_recorded_rather_than_raised(self, monkeypatch):
        import phonemizer.backend.espeak.wrapper as w
        monkeypatch.setattr(
            w.EspeakWrapper, "__init__",
            lambda self: (_ for _ in ()).throw(RuntimeError("no library")),
        )
        version, library, warning = provenance._espeak()
        assert version == "unavailable" and "RuntimeError" in library and warning


class TestGitIsDecodedExplicitly:
    """`text=True` alone decodes with the locale's preferred encoding.

    That is not cosmetic. `git_dirty` is `bool()` of `git status --porcelain`,
    whose output carries paths; on a host whose locale is a single-byte code
    page a non-ASCII path can fail to decode, `_git`'s bare `except` turns
    that into `""`, and the header then reports a **clean tree while the tree
    is dirty** -- a provenance record that lies in the safe-looking direction.
    """

    def test_the_subprocess_call_names_an_encoding(self, monkeypatch):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)

            class R:
                stdout = ""
            return R()

        monkeypatch.setattr(provenance.subprocess, "run", fake_run)
        provenance._git("status", "--porcelain")

        assert seen.get("encoding") == "utf-8", (
            "text=True without encoding= decodes with the locale's encoding"
        )

    def test_an_undecodable_byte_costs_one_character_not_the_whole_field(
        self, monkeypatch
    ):
        """With `errors='strict'` this returns `""` and `git_dirty` goes
        False on a dirty tree. With `errors='replace'` the field survives.
        """
        real_run = provenance.subprocess.run

        def fake_run(argv, **kwargs):
            # What a cp1252 host does to a UTF-8 path byte it cannot map.
            raw = b" M caf\xc3\xa9.md\n"
            class R:
                stdout = raw.decode(kwargs["encoding"], errors=kwargs["errors"])
            return R()

        monkeypatch.setattr(provenance.subprocess, "run", fake_run)
        out = provenance._git("status", "--porcelain")
        assert out, "a dirty tree must not decode to an empty string"
        assert bool(out) is True
        provenance.subprocess.run = real_run


class TestCollect:
    def test_git_is_only_ever_asked_read_only_questions(self, monkeypatch, no_network):
        """Han owns the git history; CLAUDE.md permits only read-only verbs.
        Provenance is the one place in the harness that shells out to git, so
        this is where that rule is cheap to enforce."""
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(tuple(argv))
            class R:
                stdout = ""
            return R()

        monkeypatch.setattr(provenance.subprocess, "run", fake_run)
        collect(split="dev")

        assert calls, "expected provenance to ask git something"
        permitted = {"status", "diff", "log", "show", "grep", "ls-files",
                     "rev-list", "merge-base", "check-ignore"}
        for argv in calls:
            assert argv[0] == "git"
            assert argv[1] in permitted, f"forbidden git verb: {argv[1]}"

    def test_an_unreachable_kokoro_config_is_recorded_not_raised(self, monkeypatch):
        """A dry run that only counts rows must not need the network. The run
        that actually needs the vocabulary fails where it uses it."""
        def boom(cache_dir=None):
            raise OSError("offline")
        monkeypatch.setattr(kokoro_vocab, "fetch", boom)
        prov = collect(split="dev")
        assert "unresolved" in prov.kokoro_revision and "OSError" in prov.kokoro_revision

    def test_the_dataset_revision_is_pinned_not_floating(self, no_network):
        """A moving corpus would make two baselines incomparable while looking
        identical. Contrast app/tests/test_models.py:506, which fetches
        Kokoro's config with no revision and so tracks main."""
        from eval.dataset import DATASET_REVISION
        assert len(DATASET_REVISION) == 40
        assert collect(split="dev").dataset_revision == DATASET_REVISION


class TestMergeForScoring:
    """Each field must stay with the stage that decides it.

    The two-stage split exists so scoring happens later and possibly
    elsewhere, so a flat copy of the run file's record credits the generate
    machine's panphon, Python and platform with numbers the scoring process
    computed -- and panphon is imported by score.py alone.
    """

    @pytest.fixture
    def header(self):
        return {
            "generated_at": "2020-01-01T00:00:00+00:00",
            "split": "dev",
            "espeak_version": "1.49.0",
            "espeak_library": "/old/libespeak-ng.dylib",
            "espeak_warning": "espeak-ng is (1, 49, 0), not (1, 52, 0)",
            "dataset_repo": "saraelhark/say-my-name",
            "dataset_revision": "c7d27b1e" * 5,
            "wordfreq_version": "2.0.0",
            "model_id": "google/gemma-4-31b-it",
            "model_host": "https://openrouter.ai/api/v1",
            "temperature": 0.0,
            "panphon_version": "0.0.1-from-the-generate-machine",
            "python_version": "3.8.0",
            "platform": "Linux aarch64",
            "git_commit": "a" * 40,
            "git_dirty": False,
            "kokoro_repo": "hexgrad/Kokoro-82M",
            "kokoro_revision": "deadbeef",
            "huggingface_hub_version": "0.1.0",
            "ruleset_version": None,
        }

    def test_generate_stage_fields_come_from_the_run_file(self, header, no_network):
        merged = provenance.merge_for_scoring(header, "ruleset-2")
        assert merged.espeak_version == "1.49.0"
        assert merged.espeak_library == "/old/libespeak-ng.dylib"
        assert merged.espeak_warning == header["espeak_warning"]
        assert merged.wordfreq_version == "2.0.0"
        assert merged.dataset_revision == header["dataset_revision"]
        assert merged.generated_at == "2020-01-01T00:00:00+00:00"
        assert merged.model_id == "google/gemma-4-31b-it"
        assert merged.temperature == 0.0

    def test_panphon_comes_from_the_scoring_process_not_the_run_file(
        self, header, no_network
    ):
        """The one that matters most: score.py is the only importer of
        panphon, so the run file's value has no referent at all."""
        merged = provenance.merge_for_scoring(header, "ruleset-2")
        assert merged.panphon_version != header["panphon_version"]
        assert merged.panphon_version == provenance._version("panphon")

    def test_the_other_score_stage_fields_are_local_too(self, header, no_network):
        merged = provenance.merge_for_scoring(header, "ruleset-2")
        local = provenance.collect(split="dev")
        assert merged.python_version == local.python_version
        assert merged.platform == local.platform
        assert merged.git_commit == local.git_commit

    def test_the_ruleset_and_a_scored_timestamp_are_recorded(self, header, no_network):
        merged = provenance.merge_for_scoring(header, "ruleset-2")
        assert merged.ruleset_version == "ruleset-2"
        assert merged.scored_at and merged.scored_at != merged.generated_at

    def test_a_harness_that_moved_between_stages_is_warned_about(
        self, header, monkeypatch, no_network
    ):
        monkeypatch.setattr(provenance, "_git",
                            lambda *a: "b" * 40 if a[0] == "log" else "")
        merged = provenance.merge_for_scoring(header, "ruleset-2")
        assert merged.score_warning
        assert "aaaaaaaa" in merged.score_warning
        assert "bbbbbbbb" in merged.score_warning

    def test_no_warning_when_the_harness_did_not_move(self, header, monkeypatch,
                                                      no_network):
        monkeypatch.setattr(provenance, "_git",
                            lambda *a: "a" * 40 if a[0] == "log" else "")
        assert provenance.merge_for_scoring(header, "ruleset-2").score_warning is None

    def test_both_stages_are_labelled_in_the_rendered_header(self, header, no_network):
        rendered = provenance.merge_for_scoring(header, "ruleset-2").render()
        assert "*Generating:*" in rendered and "*Scoring:*" in rendered
        generating, scoring = rendered.split("*Scoring:*")
        assert "espeak-ng" in generating and "wordfreq" in generating
        assert "panphon" in scoring and "normalisation" in scoring
        assert "kokoro vocab" in scoring
