"""Paired comparison between two engines, on the names they both answered.

`score` bootstraps each cell on its own, which gives a **marginal** interval:
it answers "how precisely do we know this engine's mean?" Two marginal
intervals that overlap do not mean the engines are indistinguishable, because
both engines answered the *same* names and the pairing carries information
the marginals throw away.

That is not a hypothetical here. On the `test` split `ar`/`oracle` reads
0.590 [0.389, 0.827] and `ar`/`today` 0.596 [0.422, 0.798] -- almost total
overlap -- while the paired difference is -0.006 [-0.250, 0.262], which says
the same thing far more tightly and for the right reason.

This module exists because three numbers in the first baseline summary were
computed by hand in a throwaway script and typed into the document, and one
of them was wrong by the time it was read (a `de` upper bound of 1.44 against
a true 1.423). A figure the repository can recompute is a figure that cannot
drift.

Pure: it re-scores a run file and never calls an engine.
"""
from __future__ import annotations

from dataclasses import dataclass

from eval.score import BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED, bootstrap_ci


@dataclass
class PairedDiff:
    """``engine_a`` minus ``engine_b``, averaged over the shared names.

    A positive mean means `a` has the *higher* error, i.e. **`b` is better**.
    """

    lang: str
    engine_a: str
    engine_b: str
    n: int
    mean: float
    lo: float | None
    hi: float | None

    @property
    def verdict(self) -> str:
        """Which engine wins, at the bootstrap's confidence level."""
        if self.lo is None or self.hi is None:
            return "no comparison possible"
        if self.lo > 0:
            return f"{self.engine_b} better"
        if self.hi < 0:
            return f"{self.engine_a} better"
        return "no difference"


def paired_differences(
    scored,
    engine_a: str,
    engine_b: str,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, PairedDiff]:
    """Per language, bootstrap the per-name ``a - b`` raw-error difference.

    ``scored`` is any iterable of ``RowScore``. A name contributes only where
    *both* engines produced a scoreable output for it, which is the same
    shared-set rule Amendment D applies to the cells -- restricted to the two
    engines being compared rather than to every engine in the file, so adding
    a third engine to a run cannot silently shrink this comparison.
    """
    by_lang: dict[str, dict[str, dict[str, float]]] = {}
    for row in scored:
        if row.engine not in (engine_a, engine_b):
            continue
        # An empty output still carries a raw_per_segment -- the full-miss
        # cost -- so `is None` alone would pair a name one engine never
        # answered. summarise excludes those from the shared set too.
        if not row.produced_any or row.raw_per_segment is None:
            continue
        by_lang.setdefault(row.lang, {}).setdefault(row.name, {})[
            row.engine
        ] = row.raw_per_segment

    out: dict[str, PairedDiff] = {}
    for lang, names in sorted(by_lang.items()):
        diffs = [
            per[engine_a] - per[engine_b]
            for per in names.values()
            if engine_a in per and engine_b in per
        ]
        if not diffs:
            continue
        lo, hi = bootstrap_ci(diffs, resamples=resamples, seed=seed)
        out[lang] = PairedDiff(
            lang=lang,
            engine_a=engine_a,
            engine_b=engine_b,
            n=len(diffs),
            mean=sum(diffs) / len(diffs),
            lo=lo,
            hi=hi,
        )
    return out


def render(diffs: dict[str, PairedDiff]) -> str:
    """A Markdown table, for pasting into a summary unedited."""
    if not diffs:
        return "No language has both engines; no paired comparison possible."
    any_diff = next(iter(diffs.values()))
    a, b = any_diff.engine_a, any_diff.engine_b
    lines = [
        f"Paired per-name difference, `{a}` minus `{b}`. A positive mean "
        f"means `{a}` has the higher error, i.e. **`{b}` is better**. "
        f"Percentile bootstrap over names, {BOOTSTRAP_RESAMPLES} resamples, "
        f"seed {BOOTSTRAP_SEED}.",
        "",
        f"| lang | n | mean ({a}−{b}) | 95% CI | verdict |",
        "|---|---|---|---|---|",
    ]
    for lang, d in sorted(diffs.items()):
        # Comma-separated rather than en-dashed: a negative bound would
        # otherwise render as `-0.797–-0.505`.
        ci = (f"[{d.lo:.3f}, {d.hi:.3f}]"
              if d.lo is not None and d.hi is not None else "—")
        lines.append(f"| {lang} | {d.n} | {d.mean:+.3f} | {ci} | {d.verdict} |")
    return "\n".join(lines)
