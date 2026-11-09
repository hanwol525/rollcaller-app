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
