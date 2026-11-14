"""The paired comparison, and why it is not the marginal one."""
import pytest

from eval.paired import PairedDiff, paired_differences, render


class _Row:
    """Enough of a RowScore for `paired_differences`."""

    def __init__(self, lang, name, engine, raw, produced_any=True):
        self.lang = lang
        self.name = name
        self.engine = engine
        self.raw_per_segment = raw
        self.produced_any = produced_any


def rows(*triples, lang="xx"):
    return [_Row(lang, n, e, r) for n, e, r in triples]


class TestOnlyNamesBothEnginesAnsweredCount:
    def test_a_name_only_one_engine_answered_is_dropped(self):
        out = paired_differences(
            rows(("a", "oracle", 1.0), ("a", "today", 0.5),
                 ("b", "oracle", 9.0)),
            "oracle", "today",
        )
        assert out["xx"].n == 1
        assert out["xx"].mean == pytest.approx(0.5)

    def test_an_unscoreable_row_is_dropped_not_counted_as_zero(self):
        """`raw_per_segment is None` means no comparison, not a tie."""
        out = paired_differences(
            rows(("a", "oracle", 1.0), ("a", "today", 0.5),
                 ("b", "oracle", None), ("b", "today", 0.5)),
            "oracle", "today",
        )
        assert out["xx"].n == 1

    def test_a_blank_output_is_dropped_though_it_carries_a_score(self):
        """score_row gives an empty output the full-miss cost rather than
        None, so `produced_any` is the only sign nothing was said. Pairing it
        would charge the other engine's win to a name one never answered --
        and summarise leaves such names out of the shared set."""
        scored = rows(("a", "oracle", 1.0), ("a", "today", 0.5),
                      ("b", "today", 0.5))
        scored.append(_Row("xx", "b", "oracle", 7.25, produced_any=False))
        out = paired_differences(scored, "oracle", "today")
        assert out["xx"].n == 1
        assert out["xx"].mean == pytest.approx(0.5)

    def test_a_blank_output_really_does_carry_a_score(self):
        """Pins the premise of the test above against the real scorer."""
        pytest.importorskip("panphon")
        from eval.score import score_row
        row = score_row(name="Anna", lang="it", engine="floor", output="",
                        references=("ˈanna",), vocab=frozenset())
        assert row.produced_any is False
        assert row.raw_per_segment is not None

    def test_a_third_engine_does_not_shrink_the_pair(self):
        """Amendment D intersects every engine in the file; this must not,
        or adding `today` to a run would silently change the floor-oracle
        comparison it is being compared against.
        """
        out = paired_differences(
            rows(("a", "floor", 2.0), ("a", "oracle", 1.0),
                 ("b", "floor", 2.0), ("b", "oracle", 1.0),
                 ("b", "today", 0.5)),
            "floor", "oracle",
        )
        assert out["xx"].n == 2

    def test_a_language_with_neither_engine_is_absent_not_empty(self):
        out = paired_differences(
            rows(("a", "floor", 1.0), ("a", "oracle", 0.5)),
            "oracle", "today",
        )
        assert out == {}


class TestTheSignConventionIsStated:
    def test_a_positive_mean_means_the_second_engine_is_better(self):
        out = paired_differences(
            rows(("a", "oracle", 1.0), ("a", "today", 0.2)),
            "oracle", "today",
        )
        assert out["xx"].mean > 0
        assert out["xx"].verdict == "today better"

    def test_a_negative_mean_means_the_first_engine_is_better(self):
        out = paired_differences(
            rows(("a", "oracle", 0.2), ("a", "today", 1.0)),
            "oracle", "today",
        )
        assert out["xx"].verdict == "oracle better"

    def test_an_interval_spanning_zero_is_no_difference(self):
        d = PairedDiff("xx", "oracle", "today", 10, 0.01, -0.2, 0.3)
        assert d.verdict == "no difference"

    def test_a_missing_interval_says_so_rather_than_guessing(self):
        d = PairedDiff("xx", "oracle", "today", 0, 0.0, None, None)
        assert d.verdict == "no comparison possible"


class TestRenderIsPasteableAndReadable:
    def test_a_negative_bound_is_not_rendered_as_a_double_dash(self):
        """`-0.797–-0.505` is what an en-dash separator produces, and every
        paired figure in this project's own summary is negative.
        """
        out = render({"de": PairedDiff("de", "oracle", "today", 170,
                                       -0.648, -0.797, -0.505)})
        assert "[-0.797, -0.505]" in out
        assert "–-" not in out

    def test_it_names_which_direction_is_better(self):
        out = render({"de": PairedDiff("de", "oracle", "today", 170,
                                       -0.648, -0.797, -0.505)})
        assert "`today` is better" in out

    def test_an_empty_comparison_says_so_rather_than_printing_a_table(self):
        assert "no paired comparison possible" in render({})

    def test_the_recipe_is_in_the_output_not_only_in_the_docs(self):
        """The figure this replaces was wrong partly because its recipe was
        recorded nowhere.
        """
        out = render({"de": PairedDiff("de", "oracle", "today", 170,
                                       -0.648, -0.797, -0.505)})
        assert "1000 resamples" in out and "seed 20261105" in out
