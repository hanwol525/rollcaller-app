"""CLI: ``./app/venv/bin/python -m eval generate|score|report`` from ``backend/``.

Two stages on purpose. ``generate`` is the expensive, side-effecting half: it
calls engines, possibly a paid LLM, and writes raw JSONL. ``score`` is pure and
reads only those files, so a scoring rule can change after a paid run without
spending anything again.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CACHE_DIR = _HERE / ".cache"    # gitignored: dataset + hub downloads
RUNS_DIR = _HERE / "runs"       # gitignored: raw per-name outputs
RESULTS_DIR = _HERE / "results"  # committed: summaries

ENGINES = ("floor", "oracle", "today")


def _langs(value: str) -> tuple[str, ...]:
    from eval.dataset import LANGUAGES
    if value == "all":
        return LANGUAGES
    chosen = tuple(v.strip() for v in value.split(",") if v.strip())
    unknown = [c for c in chosen if c not in LANGUAGES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown language code(s) {unknown}; the dataset has {list(LANGUAGES)}"
        )
    return chosen


def _engines(value: str) -> tuple[str, ...]:
    chosen = tuple(v.strip() for v in value.split(",") if v.strip())
    unknown = [c for c in chosen if c not in ENGINES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown engine(s) {unknown}; expected from {list(ENGINES)}"
        )
    return chosen


def cmd_generate(args: argparse.Namespace) -> int:
    from eval import dataset, provenance

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    prov = provenance.collect(split=args.split, cache_dir=str(CACHE_DIR))

    records = dataset.load_split(
        args.split, cache_dir=str(CACHE_DIR), langs=args.langs, limit=args.limit
    )

    if args.dry_run:
        _print_dry_run(prov, records, args)
        return 0

    print(
        "generate: engines are Unit 3 (floor/oracle) and Unit 4 (today).\n"
        "          --dry-run works now and calls no engine.",
        file=sys.stderr,
    )
    return 2


def _print_dry_run(prov, records, args) -> None:
    from collections import Counter

    from eval.dataset import BUCKET_COMMON, BUCKET_MID, BUCKET_RARE

    print(prov.render())
    print()
    print(f"## Dry run — {args.split}, engines {','.join(args.engines)}, no engine called")
    print()

    buckets = (BUCKET_COMMON, BUCKET_MID, BUCKET_RARE)
    header = f"| {'lang':5} | {'names':>6} | {'refs':>6} | {'refs/name':>9} |" + "".join(
        f" {b:>7} |" for b in buckets
    )
    print(header)
    print("|" + "|".join("-" * (len(c) ) for c in header.split("|")[1:-1]) + "|")

    def row(label, subset):
        refs = sum(len(r.references) for r in subset)
        counts = Counter(r.bucket for r in subset)
        per = f"{refs / len(subset):.2f}" if subset else "—"
        line = f"| {label:5} | {len(subset):6} | {refs:6} | {per:>9} |"
        line += "".join(f" {counts.get(b, 0):7} |" for b in buckets)
        print(line)

    for lang in args.langs:
        row(lang, [r for r in records if r.lang == lang])
    print("|" + "|".join("-" * (len(c)) for c in header.split("|")[1:-1]) + "|")
    row("all", records)

    print()
    print("Frequency buckets are a **weak signal**: every name here is on Wiktionary")
    print("by construction, and wordfreq returns 0.0 for a token it has never seen,")
    print("so `rare` mostly means `absent from running text`, not `rare as a name`.")
    if any(r.lang == "ar" for r in records):
        print()
        print("`ar` rows are in **native script, not roster-realistic**.")
    if any(r.lang == "pl" for r in records):
        print()
        print("`pl` dominates the corpus and its Wiktionary IPA is, per the dataset card,")
        print('"largely template-generated from spelling (1.01 variants per name, against')
        print('2.47 for Spanish). Treat very high Polish scores as evidence about a')
        print('transliteration rule, not about names."')


def cmd_score(args: argparse.Namespace) -> int:
    """Pure. Runs the self-check first and refuses to score if it fails."""
    from eval import kokoro_vocab, normalise, selfcheck

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    vocab, revision = kokoro_vocab.fetch(str(CACHE_DIR))
    print(f"kokoro vocabulary: {len(vocab)} symbols @ {revision[:8]}")

    try:
        selfcheck.run(vocab)
    except selfcheck.SelfCheckFailed as e:
        # Deliberately fatal. A scorer whose known pairs have stopped
        # agreeing is still perfectly capable of emitting plausible numbers,
        # and those are worse than none: Brief 04 is meant to be judged
        # against them.
        print(f"\nSELF-CHECK FAILED\n{e}", file=sys.stderr)
        return 1
    print(f"self-check: passed ({normalise.RULESET_VERSION})")

    if not args.run:
        print(
            "\nNo --run file given. Run files come from `generate`, which is "
            "Unit 3 (floor/oracle) and Unit 4 (today).",
            file=sys.stderr,
        )
        return 2
    print(f"scoring {args.run}: Unit 3 wires generate -> score end to end.", file=sys.stderr)
    return 2


def cmd_report(args: argparse.Namespace) -> int:
    print("report: Unit 3. Not implemented yet.", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval",
        description="Pronunciation eval harness (Brief 04a). Run from backend/.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="call engines; write raw JSONL")
    gen.add_argument("--split", default="dev", choices=("train", "dev", "test"),
                     help="develop on dev; report on test (default: dev)")
    gen.add_argument("--langs", type=_langs, default=_langs("all"),
                     help="comma-separated dataset codes, or 'all' (default: all)")
    gen.add_argument("--engines", type=_engines, default=("floor", "oracle"),
                     help=f"comma-separated, from {list(ENGINES)} (default: floor,oracle)")
    gen.add_argument("--limit", type=int, default=None,
                     help="cap names **per language**, for smoke tests")
    gen.add_argument("--dry-run", action="store_true",
                     help="count rows and buckets; call no engine")
    gen.set_defaults(func=cmd_generate)

    sco = sub.add_parser("score", help="score a run file; pure, never calls an engine")
    sco.add_argument("--run", default=None,
                     help="a JSONL run file from `generate` (omit to self-check only)")
    sco.set_defaults(func=cmd_score)

    rep = sub.add_parser("report", help="render a summary from scores")
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
