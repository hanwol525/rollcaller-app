"""The name-grouping and bucketing that Amendment A rests on.

Offline: every test here builds its own TSV. Nothing downloads.
"""
from __future__ import annotations

import pytest

from eval import dataset
from eval.dataset import LANGUAGES, NameRecord, bucket_for, espeak_code, group
from eval.tests._deps import PHONEMIZER_AVAILABLE, WORDFREQ_AVAILABLE


def rows(*triples: tuple[str, str, str, str]) -> list[dict[str, str]]:
    return [
        {"name": n, "lang": lg, "kind": k, "ipa": i} for n, lg, k, i in triples
    ]


@pytest.fixture(autouse=True)
def _flat_buckets(monkeypatch):
    """Bucketing is tested on its own; keep grouping tests independent of it."""
    monkeypatch.setattr(dataset, "bucket_for", lambda name, lang: ("rare", 0.0))


class TestGrouping:
    def test_variants_of_one_name_collapse_into_one_record(self):
        """The premise of Amendment A: one output per name, several references.

        Scoring per row would make the same engine output appear twice and be
        wrong at least once, because the variants differ from each other.
        """
        out = group(rows(
            ("Nguyễn", "vi", "surname", "ŋwiən˧˨ʔ"),
            ("Nguyễn", "vi", "surname", "ŋuiɜnˀ˨˩"),
        ))
        assert len(out) == 1
        assert out[0].references == ("ŋwiən˧˨ʔ", "ŋuiɜnˀ˨˩")

    def test_file_order_of_references_is_preserved(self):
        out = group(rows(("A", "it", "given", "b"), ("A", "it", "given", "a")))
        assert out[0].references == ("b", "a")

    def test_same_name_in_two_languages_stays_two_records(self):
        """`lang` is the pronunciation language, so these are different facts."""
        out = group(rows(
            ("Maria", "it", "given", "maˈriːa"),
            ("Maria", "pl", "given", "ˈmarja"),
        ))
        assert {r.lang for r in out} == {"it", "pl"}
        assert all(len(r.references) == 1 for r in out)

    def test_kinds_are_collapsed_and_deduped(self):
        out = group(rows(
            ("Lee", "de", "surname", "liː"),
            ("Lee", "de", "given", "leː"),
            ("Lee", "de", "surname", "lɪ"),
        ))
        assert out[0].kinds == ("given", "surname")
        assert out[0].kind == "given|surname"

    def test_a_row_with_no_ipa_is_dropped_but_its_siblings_survive(self):
        out = group(rows(("A", "it", "given", "a"), ("A", "it", "given", "   ")))
        assert out[0].references == ("a",)

    def test_a_name_with_no_usable_ipa_at_all_is_dropped(self):
        """Nothing to score against, so it must not reach a run file and be
        counted as an engine failure."""
        assert group(rows(("A", "it", "given", ""))) == []

    def test_unwanted_languages_are_excluded(self):
        out = group(rows(("A", "it", "given", "a"), ("B", "pl", "given", "b")), langs=("it",))
        assert [r.name for r in out] == ["A"]

    def test_rows_outside_the_eight_languages_are_ignored(self):
        assert group(rows(("A", "en", "given", "a"))) == []

    def test_whitespace_is_stripped(self):
        out = group(rows((" A ", " it ", " given ", " a ")))
        assert out[0].name == "A" and out[0].references == ("a",)


class TestLimit:
    def test_limit_caps_per_language_not_per_split(self, monkeypatch, tmp_path):
        """A global head of a Polish-dominated corpus would be all Polish and
        say nothing about the other seven languages."""
        tsv = tmp_path / "dev.tsv"
        lines = ["name\tlang\tkind\tipa"]
        for i in range(5):
            lines.append(f"pl{i}\tpl\tgiven\tp{i}")
            lines.append(f"it{i}\tit\tgiven\ti{i}")
        tsv.write_text("\n".join(lines), encoding="utf-8")
        monkeypatch.setattr(dataset, "download", lambda split, cache_dir=None: str(tsv))

        out = dataset.load_split("dev", langs=("pl", "it"), limit=2)
        assert sum(1 for r in out if r.lang == "pl") == 2
        assert sum(1 for r in out if r.lang == "it") == 2


@pytest.mark.skipif(not WORDFREQ_AVAILABLE, reason="wordfreq not installed")
class TestBuckets:
    @pytest.fixture(autouse=True)
    def _unpatch(self, monkeypatch):
        monkeypatch.setattr(dataset, "bucket_for", bucket_for)

    @pytest.mark.parametrize(
        "zipf,expected",
        [(5.0, "common"), (4.0, "common"), (3.99, "mid"), (3.0, "mid"),
         (2.99, "rare"), (0.0, "rare")],
    )
    def test_boundaries(self, monkeypatch, zipf, expected):
        import wordfreq
        monkeypatch.setattr(wordfreq, "zipf_frequency", lambda *a, **k: zipf)
        assert bucket_for("x", "it")[0] == expected

    def test_the_rarest_token_decides(self, monkeypatch):
        """A common given name beside an unattested surname is not a common row."""
        import wordfreq
        scores = {"maria": 5.0, "zrzęda": 1.0}
        monkeypatch.setattr(wordfreq, "zipf_frequency", lambda w, lang: scores[w])
        bucket, zipf = bucket_for("Maria Zrzęda".lower(), "pl")
        assert (bucket, zipf) == ("rare", 1.0)

    def test_hyphenated_names_split_into_tokens(self, monkeypatch):
        import wordfreq
        seen = []
        monkeypatch.setattr(
            wordfreq, "zipf_frequency", lambda w, lang: seen.append(w) or 4.0
        )
        bucket_for("anne-marie", "fr")
        assert seen == ["anne", "marie"]

    def test_all_eight_languages_are_supported(self):
        """P12. wordfreq raising per language would make the bucket column a
        lie for that language rather than an error."""
        from wordfreq import available_languages
        available = set(available_languages())
        assert set(LANGUAGES) <= available, sorted(set(LANGUAGES) - available)


class TestEspeakCodes:
    def test_french_is_remapped_and_the_rest_pass_through(self):
        assert espeak_code("fr") == "fr-fr"
        assert [espeak_code(c) for c in LANGUAGES if c != "fr"] == [
            c for c in LANGUAGES if c != "fr"
        ]

    @pytest.mark.skipif(not PHONEMIZER_AVAILABLE, reason="phonemizer not installed")
    def test_every_mapped_code_is_one_espeak_actually_serves(self):
        """P16: g2p caches a *sticky* None for a language whose backend failed
        to build and never retries it, so an unsupported code would not raise
        -- it would quietly return "" for every name in that language and read
        as a 100% refusal rate."""
        from phonemizer.backend import EspeakBackend
        supported = set(EspeakBackend.supported_languages())
        mapped = {c: espeak_code(c) for c in LANGUAGES}
        missing = {c: v for c, v in mapped.items() if v not in supported}
        assert not missing, missing
