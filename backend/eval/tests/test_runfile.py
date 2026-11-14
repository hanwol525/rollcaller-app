"""The on-disk contracts. Pure: no engine, no network, no panphon."""
from __future__ import annotations

import json

import pytest

from eval import runfile


# A complete Provenance payload, so read_scores can rebuild the dataclass.
_PROV = {
    "generated_at": "2026-11-10T00:00:00+00:00", "git_commit": "abc",
    "git_dirty": False, "espeak_version": "1.52.0", "espeak_library": "/l.dylib",
    "espeak_warning": None, "dataset_repo": "r", "dataset_revision": "d" * 40,
    "split": "dev", "kokoro_repo": "k", "kokoro_revision": "f3ff3571",
    "panphon_version": "0.22.2", "wordfreq_version": "3.1.1",
    "huggingface_hub_version": "1.23.0", "python_version": "3.11.0",
    "platform": "Darwin x86_64", "model_id": None, "model_host": None,
    "temperature": None, "ruleset_version": "ruleset-2",
    "scored_at": "2026-11-10T00:01:00+00:00", "score_warning": None,
}


def row(name="Alice", lang="it", engine="floor", output="alitʃe", **kw):
    return runfile.RunRow(
        name=name, lang=lang, engine=engine, output=output,
        references=kw.get("references", ("ˈalitʃe", "aˈlitʃe")),
        bucket=kw.get("bucket", "rare"), zipf=kw.get("zipf", 0.0),
        kinds=kw.get("kinds", ("given",)),
        word_count_mismatch=kw.get("word_count_mismatch", False),
        log_messages=kw.get("log_messages", ()),
    )


class TestRoundTrip:
    def test_a_row_survives_write_then_read_unchanged(self, tmp_path):
        original = row(word_count_mismatch=True, log_messages=("words count mismatch",))
        path = runfile.write(tmp_path / "r.jsonl", {"split": "dev"}, [original])
        back = runfile.read(path)
        assert back.rows == (original,)

    def test_every_attested_reference_is_kept_not_just_the_winner(self, tmp_path):
        """Amendment A takes the closest variant, and that rule has already
        changed once. A run file that stored only the winner would have to be
        regenerated to change it again -- which for `today` costs money."""
        path = runfile.write(tmp_path / "r.jsonl", {"split": "dev"},
                             [row(references=("a", "b", "c"))])
        assert runfile.read(path).rows[0].references == ("a", "b", "c")

    def test_non_ascii_is_not_escaped_so_the_file_stays_readable(self, tmp_path):
        path = runfile.write(tmp_path / "r.jsonl", {"split": "dev"}, [row()])
        assert "alitʃe" in path.read_text(encoding="utf-8")

    def test_the_header_is_the_first_line_and_carries_provenance(self, tmp_path):
        path = runfile.write(tmp_path / "r.jsonl",
                             {"split": "dev", "provenance": {"git_commit": "abc"}},
                             [row()])
        first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert first["kind"] == "header"
        assert first["format"] == runfile.FORMAT_VERSION
        assert first["provenance"]["git_commit"] == "abc"

    def test_engines_and_langs_are_reported_in_file_order(self, tmp_path):
        path = runfile.write(tmp_path / "r.jsonl", {"split": "dev"}, [
            row(engine="floor", lang="it"), row(engine="oracle", lang="it"),
            row(engine="floor", lang="pl"),
        ])
        back = runfile.read(path)
        assert back.engines == ("floor", "oracle")
        assert back.langs == ("it", "pl")


class TestItRefusesIncoherentFiles:
    """Every one of these would otherwise become a plausible wrong number."""

    def test_a_duplicate_name_lang_engine_row_is_refused(self, tmp_path):
        """summarise decides shared-set membership from a last-wins dict but
        draws values from every matching row, so a duplicate is counted twice
        into the mean and once into the set. Nothing this harness writes
        produces one; concatenating two run files by hand does."""
        path = runfile.write(tmp_path / "r.jsonl", {"split": "dev"}, [row(), row()])
        with pytest.raises(runfile.RunFileError, match="repeats"):
            runfile.read(path)

    def test_the_same_name_under_a_different_engine_is_fine(self, tmp_path):
        path = runfile.write(tmp_path / "r.jsonl", {"split": "dev"},
                             [row(engine="floor"), row(engine="oracle")])
        assert len(runfile.read(path).rows) == 2

    def test_the_same_name_in_a_different_language_is_fine(self, tmp_path):
        """A name can legitimately appear under two languages: the dataset is
        keyed by how the name is pronounced, not by its origin."""
        path = runfile.write(tmp_path / "r.jsonl", {"split": "dev"},
                             [row(lang="it"), row(lang="es")])
        assert len(runfile.read(path).rows) == 2

    def test_two_concatenated_run_files_are_refused(self, tmp_path):
        a = runfile.write(tmp_path / "a.jsonl", {"split": "dev"}, [row()])
        b = runfile.write(tmp_path / "b.jsonl", {"split": "test"}, [row(lang="pl")])
        joined = tmp_path / "joined.jsonl"
        joined.write_text(a.read_text(encoding="utf-8") + b.read_text(encoding="utf-8"),
                          encoding="utf-8")
        with pytest.raises(runfile.RunFileError, match="second header"):
            runfile.read(joined)

    def test_a_row_before_the_header_is_refused(self, tmp_path):
        path = tmp_path / "r.jsonl"
        path.write_text(json.dumps({"kind": "row", "name": "x"}) + "\n", encoding="utf-8")
        with pytest.raises(runfile.RunFileError, match="before any header"):
            runfile.read(path)

    def test_a_file_with_no_header_is_refused(self, tmp_path):
        path = tmp_path / "r.jsonl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(runfile.RunFileError, match="no header"):
            runfile.read(path)

    def test_a_future_format_version_is_refused_rather_than_guessed_at(self, tmp_path):
        path = tmp_path / "r.jsonl"
        path.write_text(json.dumps({"kind": "header", "format": 99}) + "\n",
                        encoding="utf-8")
        with pytest.raises(runfile.RunFileError, match="format"):
            runfile.read(path)

    def test_a_non_json_line_names_its_line_number(self, tmp_path):
        path = tmp_path / "r.jsonl"
        path.write_text(json.dumps(
            {"kind": "header", "format": runfile.FORMAT_VERSION})
            + "\nnot json\n",
                        encoding="utf-8")
        with pytest.raises(runfile.RunFileError, match=":2"):
            runfile.read(path)

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(runfile.RunFileError, match="no run file"):
            runfile.read(tmp_path / "absent.jsonl")

    def test_blank_lines_are_tolerated(self, tmp_path):
        path = runfile.write(tmp_path / "r.jsonl", {"split": "dev"}, [row()])
        path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
        assert len(runfile.read(path).rows) == 1


class TestScoresRoundTrip:
    """Every Cell field must survive, and the test must notice a new one.

    This went wrong once: `n_over_ceiling`, `raw_max`, `raw_max_name` and
    `lang_per_name_best` were added to `Cell` and not to a hand-written
    serializer, so the renderer silently read their defaults and printed "0"
    and "—" for figures that had been computed correctly. The serializer now
    derives its field list from the dataclass; this holds it to that.
    """

    @pytest.fixture
    def cells(self):
        from collections import Counter

        from eval.score import Cell
        return {("vi", "floor"): Cell(
            lang="vi", engine="floor", engines_compared=("floor", "oracle"),
            n_names=37, n_produced_any=37, n_voiced=33, n_raw_scored=37,
            n_kokoro_scored=18,
            refusal_reasons=Counter({"2 consecutive letter names": 2}),
            raw_mean=8.933, raw_ci=(4.961, 13.555), kokoro_mean=3.563,
            kokoro_ci=(1.374, 6.78), ceiling_mean=7.25, raw_max=55.625,
            raw_max_name="Hường", n_over_ceiling=10,
            chars_dropped_per_name=0.297, dropped_histogram=Counter({"ɚ": 11}),
            n_language_switch_flags=0, n_word_count_mismatch=10,
            lang_per_name_best=0.723,
        )}

    def test_no_cell_field_is_silently_dropped(self, tmp_path, cells):
        from dataclasses import fields

        path = runfile.write_scores(tmp_path / "s.json", {}, {}, cells)
        import json as _json
        written = _json.loads(path.read_text(encoding="utf-8"))["cells"][0]
        missing = {f.name for f in fields(next(iter(cells.values())))} - set(written)
        assert not missing, f"these Cell fields never reach the file: {missing}"

    def test_every_value_survives_the_round_trip(self, tmp_path, cells):
        prov = {"split": "dev"}
        original = next(iter(cells.values()))
        path = runfile.write_scores(tmp_path / "s.json", _PROV, prov, cells)
        _, header, back = runfile.read_scores(path)
        assert header == prov
        assert back[("vi", "floor")] == original

    def test_counters_and_tuples_keep_their_types(self, tmp_path, cells):
        from collections import Counter

        path = runfile.write_scores(tmp_path / "s.json", _PROV, {}, cells)
        cell = runfile.read_scores(path)[2][("vi", "floor")]
        assert isinstance(cell.refusal_reasons, Counter)
        assert isinstance(cell.dropped_histogram, Counter)
        assert isinstance(cell.raw_ci, tuple) and isinstance(cell.kokoro_ci, tuple)
        assert isinstance(cell.engines_compared, tuple)

    def test_non_ascii_in_a_worst_name_survives(self, tmp_path, cells):
        path = runfile.write_scores(tmp_path / "s.json", _PROV, {}, cells)
        assert runfile.read_scores(path)[2][("vi", "floor")].raw_max_name == "Hường"

    def test_a_newer_file_with_an_unknown_field_is_refused_not_guessed_at(
        self, tmp_path, cells
    ):
        import json as _json
        path = runfile.write_scores(tmp_path / "s.json", _PROV, {}, cells)
        payload = _json.loads(path.read_text(encoding="utf-8"))
        payload["cells"][0]["some_future_column"] = 1
        path.write_text(_json.dumps(payload), encoding="utf-8")
        with pytest.raises(runfile.RunFileError, match="newer harness"):
            runfile.read_scores(path)

    def test_a_future_scores_format_is_refused(self, tmp_path, cells):
        import json as _json
        path = runfile.write_scores(tmp_path / "s.json", _PROV, {}, cells)
        payload = _json.loads(path.read_text(encoding="utf-8"))
        payload["format"] = 99
        path.write_text(_json.dumps(payload), encoding="utf-8")
        with pytest.raises(runfile.RunFileError, match="scores format"):
            runfile.read_scores(path)

    def test_a_missing_scores_file_says_so(self, tmp_path):
        with pytest.raises(runfile.RunFileError, match="no scores file"):
            runfile.read_scores(tmp_path / "absent.json")


class TestATodayRowMustCarryABranch:
    """Unit 4's done-criterion, enforced where it cannot be skipped.

    A version number would not have caught this: the failure mode is a
    *current* writer that recorded nothing, which happens when
    `gemma.gemma_ipa` was patched instead of the binding `g2p()` reads
    (`g2p.py:16`). Scoring that file would report the eSpeak floor as the
    model's output, with no error anywhere.
    """

    def _write(self, tmp_path, **row_overrides):
        path = tmp_path / "r.jsonl"
        row = {
            "kind": "row", "name": "Alice", "lang": "it", "engine": "today",
            "output": "ˈalitʃe", "references": ["ˈalitʃe"], "bucket": "rare",
            "zipf": 1.0, "kinds": ["given"], "word_count_mismatch": False,
            "log_messages": [], "branch": "llm_accepted", "branch_reason": None,
        }
        row.update(row_overrides)
        path.write_text(
            json.dumps({"kind": "header", "format": runfile.FORMAT_VERSION})
            + "\n" + json.dumps(row, ensure_ascii=False) + "\n",
            encoding="utf-8")
        return path

    def test_a_today_row_without_a_branch_is_refused(self, tmp_path):
        path = self._write(tmp_path, branch=None)
        with pytest.raises(runfile.RunFileError, match="no branch"):
            runfile.read(path)

    def test_the_refusal_names_the_patch_target_mistake(self, tmp_path):
        """The error has to say *why*, because the cause is one specific
        wrong monkeypatch and nothing else about the output looks wrong."""
        path = self._write(tmp_path, branch=None)
        with pytest.raises(runfile.RunFileError) as excinfo:
            runfile.read(path)
        assert "app.pronunciation.g2p.gemma_ipa" in str(excinfo.value)

    def test_an_unknown_branch_is_refused(self, tmp_path):
        path = self._write(tmp_path, branch="llm_probably_fine")
        with pytest.raises(runfile.RunFileError, match="llm_probably_fine"):
            runfile.read(path)

    def test_an_espeak_row_needs_no_branch(self, tmp_path):
        """`floor` makes no LLM call, so a missing branch is correct there --
        the check must not fire on it."""
        path = self._write(tmp_path, engine="floor", branch=None)
        assert runfile.read(path).rows[0].branch is None

    def test_a_valid_today_row_round_trips_with_its_reason(self, tmp_path):
        path = self._write(
            tmp_path, branch="llm_rejected", branch_reason="reads as prose")
        row = runfile.read(path).rows[0]
        assert row.branch == "llm_rejected"
        assert row.branch_reason == "reads as prose"
