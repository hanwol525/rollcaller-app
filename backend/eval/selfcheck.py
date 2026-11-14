"""Known pairs the scorer must get right, run before any scoring.

A scorer is a measuring instrument, and the failure that matters is the quiet
one: a normalisation rule that stops firing turns "the engines agree" into
"the engines are 8.75 apart" with no error anywhere. So scoring refuses to
start until these pass.

**Two tiers, and the second is not optional.** Only four normalisation rules
change a distance -- tie bar U+0361, ligature expansion, the ASCII ``g``
lookalike, Chao tone letters. panphon already ignores stress marks, syllable
dots and digits, so a broken rule for any of *those* moves no distance at all
and a distance-only self-check would sail straight past it. The rule-level
checks assert on the normalised string instead, which is the only place that
breakage is visible.

Unicode form needs both tiers for a third reason: NFD moves no distance
either, because panphon decomposes internally, but it decides whether
``dropped_characters`` tells the truth. That is checked against ``segments``
directly rather than through a distance.
"""
from __future__ import annotations

from eval.normalise import LIGATURES, LOOKALIKES, normalise
from eval.score import (
    MISS_CEILING_PER_SEGMENT,
    distance,
    dropped_characters,
    miss_ceiling,
    score_row,
    segments,
)

ZERO_TOLERANCE = 1e-9


class SelfCheckFailed(RuntimeError):
    """Raised instead of returning numbers nobody should trust."""


def _dist(a: str, b: str) -> float:
    return distance(normalise(a), normalise(b))


def _check_identical_after_normalisation() -> list[str]:
    """Pairs that denote the same sounds must score exactly 0."""
    pairs = [
        ("ʤ", "dʒ", "ligature ʤ vs dʒ (panphon segments ʤ as nothing)"),
        ("ʧ", "tʃ", "ligature ʧ vs tʃ"),
        ("ʦ", "ts", "ligature ʦ vs ts"),
        ("ʣ", "dz", "ligature ʣ vs dz"),
        ("ʥ", "dʑ", "ligature ʥ vs dʑ (Polish dź)"),
        ("ʨ", "tɕ", "ligature ʨ vs tɕ (Polish ć)"),
        ("maɡda", "magda", "ASCII g vs ɡ U+0261 (panphon reads only U+0261)"),
        ("ɡroːs", "groːs", "ASCII g mid-word, against a real dataset reference"),
        ("çetin", "c\u0327etin", "precomposed ç vs decomposed c+U+0327"),
        ("ãnselmo", "a\u0303nselmo", "precomposed ã vs decomposed a+U+0303"),
        ("t͡ʃ", "tʃ", "tie bar above U+0361 (one segment vs two)"),
        ("t͜ʃ", "tʃ", "tie bar below U+035C"),
        ("ˈbob", "bob", "primary stress only"),
        ("bˌob", "bob", "secondary stress only"),
        ("ba.bo", "babo", "syllable separator"),
        ("/ˈælɪs/", "ælɪs", "dictionary slashes and stress"),
        ("[ælɪs]", "ælɪs", "bracket wrappers"),
        ("ŋwˈiɛ5n", "ŋwiɛn", "Vietnamese tone digit"),
        ("ta˥˩", "ta", "Chao tone letters (panphon DOES segment these)"),
    ]
    failures = []
    for a, b, label in pairs:
        got = _dist(a, b)
        if abs(got) > ZERO_TOLERANCE:
            failures.append(f"{label}: {a!r} vs {b!r} scored {got}, expected 0")
    return failures


def _check_real_differences_still_register() -> list[str]:
    """A normaliser that folded everything would score 0 on everything."""
    failures = []
    got = _dist("ʒ", "ʐ")          # ʒ vs ʐ
    if not (0 < got < 1):
        failures.append(f"ʒ vs ʐ scored {got}, expected above 0 and below 1")
    got = _dist("aː", "a")              # length is kept on purpose
    if got <= 0:
        failures.append(
            f"aː vs a scored {got}; length marks are KEPT, so this must be > 0"
        )
    got = _dist("bob", "")
    if got <= 0:
        failures.append(f"an empty string vs 'bob' scored {got}, expected > 0")
    return failures


def _check_rules_actually_fire() -> list[str]:
    """String-level. The only tier that can catch a broken no-op rule."""
    cases = [
        ("/a/", "a", "wrappers are stripped"),
        ("[a]", "a", "brackets are stripped"),
        ("ˈaˌb", "ab", "stress marks are stripped"),
        ("a.b", "ab", "syllable dots are stripped"),
        ("t͡ʃ", "tʃ", "tie bar U+0361 is stripped"),
        ("t͜ʃ", "tʃ", "tie bar U+035C is stripped"),
        ("a5b", "ab", "digits are stripped"),
        ("a˥˦˧˨˩b", "ab", "Chao tone letters are stripped"),
        ("aː", "aː", "length mark ː is KEPT"),
        ("aˑ", "aˑ", "half-length ˑ is KEPT"),
        ("  a   b  ", "a b", "whitespace is collapsed"),
        ("çetin", "c\u0327etin", "NFD: ç decomposes to c + U+0327"),
        ("g", "ɡ", "ASCII g folds to U+0261"),
    ]
    failures = []
    for raw, expected, label in cases:
        got = normalise(raw)
        if got != expected:
            failures.append(f"{label}: normalise({raw!r}) == {got!r}, expected {expected!r}")
    for source, target in {**LIGATURES, **LOOKALIKES}.items():
        got = normalise(source)
        if got != target:
            failures.append(
                f"{source!r} folded to {got!r}, expected {target!r}"
            )
    for raw in ("/ˈælɪs/", "ʤu", "ta˥", "aː"):
        once = normalise(raw)
        if normalise(once) != once:
            failures.append(f"normalise is not idempotent on {raw!r}")
    return failures


def _check_dropped_characters_agree_with_the_distance() -> list[str]:
    """The column claims "what the distance could not see". Hold it to that.

    A precomposed character panphon segments must not be reported as dropped,
    and one it genuinely cannot read must be. Neither case moves a distance,
    so this is the only tier that can catch an un-decomposed walk.
    """
    failures = []
    for ipa, label in (("çetin", "precomposed ç (panphon segments it)"),
                       ("ãnselmo", "precomposed ã"),
                       ("mawɡɔʒata", "plain IPA, nothing to drop")):
        norm = normalise(ipa)
        count, chars = dropped_characters(norm)
        if count:
            failures.append(
                f"{label}: dropped_characters({norm!r}) reported {count} "
                f"dropped ({chars!r}), but the distance scored every one of "
                "them as part of a segment"
            )
    # The converse: something panphon really cannot read must still be named.
    count, chars = dropped_characters(normalise("a(en)b"))
    if "(" not in chars or ")" not in chars:
        failures.append(
            f"a language-switch flag's parentheses were not reported as "
            f"dropped; got {count} ({chars!r})"
        )
    # And the column must be consistent with the segmenter on the same string.
    norm = normalise("çetin")
    if not segments(norm):
        failures.append(f"{norm!r} segments to nothing; NFD folding is broken")
    return failures


def _check_scale_has_not_moved() -> list[str]:
    """The summaries state a 0-to-7.25 scale. Catch a panphon change."""
    failures = []
    for ref in ("mawɡɔʒata", "ʃɪˈvɔːn", "bob"):
        got = miss_ceiling(normalise(ref))
        if abs(got - MISS_CEILING_PER_SEGMENT) > 1e-6:
            failures.append(
                f"miss ceiling for {ref!r} is {got} per segment, not "
                f"{MISS_CEILING_PER_SEGMENT}: panphon's scale moved, so every "
                "summary's stated range is wrong"
            )
    return failures


def _check_empty_output_is_a_refusal(vocab) -> list[str]:
    """"Empty output is a refusal, never a distance."

    It must not reach a reported error figure. ``produced_any`` False keeps it
    out of the shared raw set (Amendment D), which is where that is enforced.
    """
    failures = []
    row = score_row("Alice", "it", "floor", "", ("ˈalitʃe",), vocab)
    if row.refusal is None:
        failures.append("empty output was not refused")
    if row.produced_any:
        failures.append("empty output reported produced_any=True")
    if row.kokoro_per_segment is not None:
        failures.append("empty output produced a Kokoro-side figure")

    from eval.score import summarise
    good = score_row("Alice", "it", "oracle", "ˈalitʃe",
                     ("ˈalitʃe",), vocab)
    cells = summarise([row, good])
    if cells[("it", "floor")].n_raw_scored:
        failures.append(
            "an empty output was counted into the shared raw set; it must be "
            "excluded, because every engine has to have produced something "
            "for the comparison to mean anything"
        )
    return failures


def run(vocab) -> None:
    """Run every check. Raise rather than let scoring continue."""
    failures: list[str] = []
    failures += _check_rules_actually_fire()
    failures += _check_identical_after_normalisation()
    failures += _check_real_differences_still_register()
    failures += _check_dropped_characters_agree_with_the_distance()
    failures += _check_scale_has_not_moved()
    failures += _check_empty_output_is_a_refusal(vocab)
    if failures:
        raise SelfCheckFailed(
            f"{len(failures)} self-check failure(s); refusing to score:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
