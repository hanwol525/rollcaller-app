"""CLI: ``./app/venv/bin/python -m eval generate|score|report`` from ``backend/``.

Two stages on purpose. ``generate`` is the expensive, side-effecting half: it
calls engines, possibly a paid LLM, and writes raw JSONL. ``score`` is pure and
reads only those files, so a scoring rule can change after a paid run without
spending anything again.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from eval.dataset import LANGUAGES

_HERE = Path(__file__).resolve().parent
CACHE_DIR = _HERE / ".cache"    # gitignored: dataset + hub downloads
RUNS_DIR = _HERE / "runs"       # gitignored: raw per-name outputs
RESULTS_DIR = _HERE / "results"  # committed: summaries

ENGINES = ("floor", "oracle", "today")


def _langs(value: str) -> tuple[str, ...]:
    if value == "all":
        return LANGUAGES
    # dict.fromkeys de-duplicates while keeping the order given. A repeat is
    # not an error worth rejecting, but it must not reach the run file: two
    # rows for one (name, lang, engine) is the one shape runfile.read refuses.
    chosen = tuple(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))
    unknown = [c for c in chosen if c not in LANGUAGES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown language code(s) {unknown}; the dataset has {list(LANGUAGES)}"
        )
    return chosen


def _engines(value: str) -> tuple[str, ...]:
    chosen = tuple(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))
    unknown = [c for c in chosen if c not in ENGINES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown engine(s) {unknown}; expected from {list(ENGINES)}"
        )
    return chosen


def cmd_generate(args: argparse.Namespace) -> int:
    from eval import dataset, provenance

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # The model is read from settings rather than passed, so the run file can
    # never name a model other than the one that was actually called.
    model_id = model_host = None
    if "today" in args.engines and not args.dry_run:
        from app.config import settings
        model_id, model_host = settings.gemma_model, settings.gemma_base_url
    prov = provenance.collect(
        split=args.split, cache_dir=str(CACHE_DIR),
        model_id=model_id, model_host=model_host,
        # gemma.py:268 sends temperature 0, deterministically.
        temperature=0 if model_id else None,
        request_modification=(
            __import__("json").dumps(args.extra_payload, sort_keys=True)
            if getattr(args, "extra_payload", None) else None),
    )

    records = dataset.load_split(
        args.split, cache_dir=str(CACHE_DIR), langs=args.langs, limit=args.limit
    )
    by_name = {(r.name, r.lang): r for r in records}

    if args.dry_run:
        _print_dry_run(prov, records, args)
        return 0

    from collections import Counter

    from eval import engines as engine_mod
    from eval import runfile

    # Amendment B's `llm_unconfigured`: a static, process-wide condition
    # asserted once here rather than carried as a row branch. Unset, every
    # row would be the eSpeak floor wearing an LLM's label -- and gemma.py
    # dedups the warning process-wide, so only the first name would even say
    # so. Checked before warming, so nothing is paid for or waited on.
    model_fields: dict = {}
    if "today" in args.engines:
        from eval import llm
        try:
            model_fields = llm.require_configured()
        except llm.NotConfigured as e:
            print(f"\nTODAY IS NOT RUNNABLE\n{e}", file=sys.stderr)
            return 2
        print(f"`today` will call {model_fields['model_id']} at "
              f"{model_fields['model_host']}")
        if args.extra_payload:
            print(f"  MODIFIED REQUEST: adding {args.extra_payload} to every "
                  "outgoing body — this is no longer exactly today's path, "
                  "and provenance records it as such")

    # Validate every eSpeak code BEFORE warming anything (P16). g2p.warm
    # swallows an unsupported language and caches a sticky None, so a bad code
    # would read as a 100% refusal rate for that language rather than an error.
    try:
        codes = engine_mod.espeak_codes(args.langs)
        print(f"eSpeak voices validated: "
              f"{', '.join(f'{k}->{v}' for k, v in sorted(codes.items()))}")
        timings = engine_mod.warm(args.langs)
    except engine_mod.EngineUnavailable as e:
        print(f"\nENGINE UNAVAILABLE\n{e}", file=sys.stderr)
        return 1
    print(f"warmed {len(timings)} backends in {sum(timings.values()):.1f}s "
          f"({', '.join(sorted(timings))})")

    print(f"\n{len(records)} names x {len(args.engines)} engines; eSpeak "
          "single-threaded (g2p._espeak_lock serialises phonemize anyway)"
          + (f", `today` on {args.workers} worker(s)"
             if "today" in args.engines else ""))
    rows, log_counts, branches = [], Counter(), Counter()
    for engine in args.engines:
        started = time.perf_counter()
        is_llm = engine == "today"
        for out in engine_mod.run(
            engine, records,
            workers=args.workers if is_llm else 1,
            extra_payload=args.extra_payload if is_llm else None,
        ):
            record = by_name[(out.name, out.lang)]
            rows.append(runfile.RunRow(
                name=out.name, lang=out.lang, engine=out.engine,
                output=out.output, references=record.references,
                bucket=record.bucket, zipf=record.zipf, kinds=record.kinds,
                word_count_mismatch=out.word_count_mismatch,
                log_messages=out.log_messages,
                branch=out.branch, branch_reason=out.branch_reason,
            ))
            for message in out.log_messages:
                log_counts[_log_kind(message)] += 1
            if out.branch:
                branches[out.branch] += 1
        print(f"  {engine}: {len(records)} names in "
              f"{time.perf_counter() - started:.1f}s")

    header = {
        "split": args.split,
        "langs": list(args.langs),
        "engines": list(args.engines),
        "limit": args.limit,
        "n_names": len(records),
        # Which voice actually produced each language's IPA. Recoverable from
        # espeak_warm_seconds' key order, but only positionally -- and "which
        # voice said this" is the provenance a pronunciation baseline turns
        # on, so it is recorded as data.
        "espeak_voices": dict(codes),
        "espeak_warm_seconds": {k: round(v, 3) for k, v in timings.items()},
        # Empty for an as-shipped run. Non-empty means the harness added
        # fields to the outgoing request body, so the run measures a path the
        # app cannot take by itself.
        "extra_payload": args.extra_payload or {},
        "provenance": prov.as_dict(),
    }
    path = runfile.write(_run_path(args, prov), header, rows)
    print(f"\nwrote {len(rows)} rows -> {path}")
    if branches:
        print("\n`today` branches (Amendment B; every row carries exactly one):")
        total = sum(branches.values())
        for branch, count in branches.most_common():
            print(f"  {count:6}  {branch:16} {count / total:6.1%}")
    if log_counts:
        print("\nlog records during the run (counted, not printed per name):")
        for kind, count in log_counts.most_common():
            print(f"  {count:6}  {kind}")
    print(f"\nnext: python -m eval score --run {path}")
    return 0


def _payload(value: str) -> dict:
    """Parse --extra-payload. A JSON object, or argparse rejects it."""
    import json as _json
    try:
        parsed = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"not JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError(
            f"must be a JSON object, got {type(parsed).__name__}")
    return parsed


def cmd_probe(args: argparse.Namespace) -> int:
    """Unit 4's first step: one request, with nothing thrown away.

    ``gemma_ipa`` reads ``data["choices"][0]["message"]["content"]`` and
    discards the rest, so a reasoning trace returned in a sibling field, a
    ``finish_reason`` of ``length``, and the reasoning token count are all
    invisible from inside the app's path -- and those are exactly what decide
    whether a model needs a thinking-off configuration at all.

    **Covered by the paid-run gate as an explicit exemption:** one to four
    requests of roughly 100 input tokens each, a small fraction of a cent.
    """
    if args.levers and args.extra_payload:
        # The first attempt in a --levers sweep is the as-shipped baseline
        # every later one is compared against. Folding --extra-payload into
        # it would label a modified request "as-shipped" and then measure
        # every lever against that, which is the one mislabel this whole
        # command exists to prevent.
        print("--levers runs its own sweep starting from an unmodified "
              "baseline; --extra-payload would modify that baseline and be "
              "labelled 'as-shipped'. Pass one or the other.", file=sys.stderr)
        return 2

    from eval import llm

    try:
        fields = llm.require_configured()
    except llm.NotConfigured as e:
        print(f"CANNOT PROBE\n{e}", file=sys.stderr)
        return 2

    name = args.name or llm.PROBE_NAME
    print(f"probing {fields['model_id']} at {fields['model_host']} "
          f"with name={name!r}\n")

    attempts = [None]
    if args.levers:
        # as-shipped first: if the model does not reason by default, the two
        # configurations are identical and only as-shipped is ever run.
        attempts += [dict(c) for c in llm.THINKING_OFF_CANDIDATES]
        attempts += [{**llm.THINKING_OFF_CANDIDATES[0], **llm.REQUIRE_PARAMETERS}]

    results = []
    for extra in attempts:
        label = "as-shipped" if not extra else repr(extra)
        print(f"--- {label} ---")
        result = llm.probe(
            extra_payload=args.extra_payload if not args.levers else extra,
            name=name)
        print(result.render())
        print()
        results.append((label, result))

    baseline = results[0][1]
    print("=" * 70)
    if baseline.reasons_by_default is None:
        print("INCONCLUSIVE: the as-shipped request did not return a usable "
              "body, so nothing can be concluded about reasoning. Fix the "
              "request before reading anything below.")
        return 1
    if not baseline.reasons_by_default:
        print("This model does NOT reason by default on this route, so "
              "`as-shipped` and `thinking-off` are the same configuration "
              "and only `as-shipped` need be run (brief section 2).")
        return 0

    print("This model DOES reason by default, so it needs a thinking-off "
          "configuration.")
    if len(results) == 1:
        # Only as-shipped was attempted; saying "no lever worked" here would
        # report a negative result for an experiment that never ran.
        print("No lever was tried. Re-run with --levers to find out which "
              "one, if any, actually suppresses the trace.")
        return 0
    print("Levers that suppressed it:")
    working = [label for label, r in results[1:]
               if r.reasons_by_default is False]
    if not working:
        print("  none. Every candidate was accepted and the trace persisted, "
              "which means the lever is being ignored rather than applied. "
              "Do NOT label a run `thinking-off` on this evidence: it would "
              "be byte-identical to as-shipped while claiming otherwise. "
              "This belongs to the rename brief, which owns the payload.")
        return 1
    for label in working:
        print(f"  {label}")
    print("\nUse the first of these as --extra-payload. The run's provenance "
          "will record it as a modified request.")
    return 0


def _run_path(args, prov) -> Path:
    """A run-file path that cannot collide with a different run.

    The stamp is second-resolution, so the languages go in the name too: two
    generates in the same second over different --langs would otherwise write
    the same file and the second would silently replace the first. If a file
    still exists, suffix rather than overwrite -- a run file is the only
    record of a generate, and for `today` it is the only record of money spent.
    """
    stamp = prov.generated_at.replace(":", "").replace("-", "")[:15]
    langs = "all" if tuple(args.langs) == tuple(LANGUAGES) else "".join(args.langs)
    base = f"{args.split}-{langs}-{'-'.join(args.engines)}-{stamp}"
    path = RUNS_DIR / f"{base}.jsonl"
    n = 2
    while path.exists():
        path = RUNS_DIR / f"{base}-{n}.jsonl"
        n += 1
    return path


def _log_kind(message: str) -> str:
    """Collapse a log message to its template, so counts aggregate."""
    for marker, label in (
        ("words count mismatch", "phonemizer: words count mismatch (narration)"),
        ("no IPA for name", "g2p: no IPA at all (refused downstream)"),
        ("eSpeak returned empty IPA", "g2p: eSpeak returned empty IPA"),
        ("eSpeak phonemize failed", "g2p: phonemize raised"),
        ("eSpeak warm failed", "g2p: warm failed"),
    ):
        if marker in message:
            return label
    return message[:70]


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
            "\nNo --run file given. `score` self-checks and exits; pass a run "
            "file from `generate` to score one.",
            file=sys.stderr,
        )
        return 2

    from eval import provenance, runfile, score

    try:
        run = runfile.read(args.run)
    except runfile.RunFileError as e:
        print(f"\nBAD RUN FILE\n{e}", file=sys.stderr)
        return 1
    print(f"read {len(run.rows)} rows: engines {list(run.engines)}, "
          f"languages {list(run.langs)}")

    rows = [
        score.score_row(
            name=r.name, lang=r.lang, engine=r.engine, output=r.output,
            references=r.references, vocab=vocab,
            word_count_mismatch=r.word_count_mismatch,
            branch=r.branch, branch_reason=r.branch_reason,
        )
        for r in run.rows
    ]
    cells = score.summarise(rows)
    print(f"scored {len(rows)} rows into {len(cells)} language x engine cells")

    # Each field is kept with the stage that decides it. Copying the run
    # file's record and overwriting the ruleset -- which this did -- credits
    # the generate machine's panphon, Python and platform with numbers this
    # process computed, and panphon is imported by score.py alone.
    prov = provenance.merge_for_scoring(
        run.header["provenance"], normalise.RULESET_VERSION, str(CACHE_DIR)
    )
    out = Path(args.out) if args.out else Path(args.run).with_suffix(".scores.json")
    runfile.write_scores(out, prov.as_dict(), run.header, cells)
    print(f"wrote scores -> {out}")

    for (lang, engine), cell in sorted(cells.items()):
        raw = "—" if cell.raw_mean is None else f"{cell.raw_mean:.3f}"
        print(f"  {lang:3} {engine:7} n={cell.n_names:5} voiced={cell.n_voiced:5} "
              f"in-set={cell.n_raw_scored:5} error/seg={raw}")
    print(f"\nnext: python -m eval report --scores {out}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Pure presentation. Never a reason to re-score, let alone re-generate."""
    from eval import report, runfile

    try:
        prov, header, cells = runfile.read_scores(args.scores)
    except runfile.RunFileError as e:
        print(f"\nBAD SCORES FILE\n{e}", file=sys.stderr)
        return 1

    markdown = report.render(prov, cells, header, title=args.title)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(markdown)
    return 0


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
    gen.add_argument("--workers", type=int, default=1,
                     help="parallel LLM calls for `today` only; the eSpeak "
                          "engines are refused above 1 because "
                          "g2p._espeak_lock serialises them (default: 1)")
    gen.add_argument("--extra-payload", type=_payload, default=None,
                     metavar="JSON",
                     help="JSON merged into every outgoing LLM request body, "
                          "at the httpx layer. Use to disable a model's "
                          "default reasoning mode without editing "
                          "backend/app/. Recorded in provenance as a "
                          "modified request.")
    gen.set_defaults(func=cmd_generate)

    sco = sub.add_parser("score", help="score a run file; pure, never calls an engine")
    sco.add_argument("--run", default=None,
                     help="a JSONL run file from `generate` (omit to self-check only)")
    sco.add_argument("--out", default=None,
                     help="scores JSON path (default: alongside the run file)")
    sco.set_defaults(func=cmd_score)

    rep = sub.add_parser("report", help="render a summary from scores")
    rep.add_argument("--scores", required=True, help="a scores JSON from `score`")
    rep.add_argument("--out", default=None,
                     help="write Markdown here instead of stdout")
    rep.add_argument("--title", default="Pronunciation baseline")
    rep.set_defaults(func=cmd_report)

    pro = sub.add_parser(
        "probe",
        help="one raw LLM request, reporting everything gemma_ipa discards")
    pro.add_argument("--name", default=None,
                     help=f"the name to send (default: {'Malgorzata'!r})")
    pro.add_argument("--extra-payload", type=_payload, default=None,
                     metavar="JSON",
                     help="JSON merged into the request body")
    pro.add_argument("--levers", action="store_true",
                     help="try each THINKING_OFF_CANDIDATE in turn and report "
                          "which one actually suppresses the reasoning trace")
    pro.set_defaults(func=cmd_probe)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
