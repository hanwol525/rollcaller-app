"""The JSONL contract between ``generate`` and ``score``.

One header line, then one row per (name, lang, engine). The split exists so a
scoring rule can change after a paid LLM run and cost nothing to re-apply, and
that only works if a row carries everything scoring needs:

* **every attested reference**, not the one that happened to win. Amendment A
  takes the closest variant, and that rule has already changed once; a run file
  that stored only the winner would have to be regenerated to change it again.
* **the frequency bucket and zipf**, because ``wordfreq`` is a pinned
  dependency whose tables could move under a re-score.
* **what the loggers said**, because a branch read from a log is Unit 4's only
  way to classify a gemma failure, and logs do not survive the process.

**Duplicate (name, lang, engine) rows are rejected on read.** ``summarise``
decides shared-set membership from a last-wins dict while drawing values from
every matching row, so a duplicate would be counted twice into a mean and once
into the set -- a silently wrong number rather than an error. Nothing this
harness writes produces one, which is exactly why the check belongs here: the
reachable way to get one is concatenating two run files by hand, and that is
the operation a merged multi-engine score invites.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from eval.llm import BRANCHES

# Deliberately still 1 after Unit 4 added `branch` / `branch_reason`. Those
# fields are additive and the reader defaults them, so a pre-Unit-4 file still
# scores correctly -- and no such file can contain a `today` row, because
# `today` raised until Unit 4 implemented it. What a version number would have
# guarded is instead enforced directly below: a `today` row carrying no branch
# is refused on read. That catches the failure mode a version cannot (a
# *current* writer that recorded nothing) and costs no earlier artifact.
FORMAT_VERSION = 1


class RunFileError(ValueError):
    """The file is not a run file, or not a coherent one."""


@dataclass(frozen=True)
class RunRow:
    name: str
    lang: str
    engine: str
    output: str
    references: tuple[str, ...]
    bucket: str
    zipf: float
    kinds: tuple[str, ...]
    word_count_mismatch: bool
    log_messages: tuple[str, ...]
    # `today` only (Amendment B). None for the eSpeak engines, which make no
    # LLM call -- not "unknown", but "there was nothing to classify".
    branch: str | None = None
    branch_reason: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.name, self.lang, self.engine)


@dataclass(frozen=True)
class RunFile:
    header: dict
    rows: tuple[RunRow, ...]

    @property
    def engines(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.engine for r in self.rows))

    @property
    def langs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.lang for r in self.rows))


def write(path: str | Path, header: dict, rows) -> Path:
    """Write the header then every row. Returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(
            {"kind": "header", "format": FORMAT_VERSION, **header},
            ensure_ascii=False,
        ) + "\n")
        for row in rows:
            fh.write(json.dumps({
                "kind": "row",
                "name": row.name,
                "lang": row.lang,
                "engine": row.engine,
                "output": row.output,
                "references": list(row.references),
                "bucket": row.bucket,
                "zipf": row.zipf,
                "kinds": list(row.kinds),
                "word_count_mismatch": row.word_count_mismatch,
                "log_messages": list(row.log_messages),
                "branch": row.branch,
                "branch_reason": row.branch_reason,
            }, ensure_ascii=False) + "\n")
    return path


def read(path: str | Path) -> RunFile:
    """Read and validate. Raises rather than scoring something incoherent."""
    path = Path(path)
    if not path.exists():
        raise RunFileError(f"no run file at {path}")

    header: dict | None = None
    rows: list[RunRow] = []
    seen: dict[tuple[str, str, str], int] = {}

    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise RunFileError(f"{path}:{lineno} is not JSON ({e})") from e

            kind = record.get("kind")
            if kind == "header":
                if header is not None:
                    raise RunFileError(
                        f"{path}:{lineno} is a second header. Two run files "
                        "concatenated: score them separately, or merge the "
                        "row lines under one header that describes both."
                    )
                if record.get("format") != FORMAT_VERSION:
                    raise RunFileError(
                        f"{path} is format {record.get('format')!r}, this "
                        f"reader is {FORMAT_VERSION}"
                    )
                header = record
                continue
            if kind != "row":
                raise RunFileError(f"{path}:{lineno} has kind={kind!r}")
            if header is None:
                raise RunFileError(f"{path}:{lineno} is a row before any header")

            row = RunRow(
                name=record["name"],
                lang=record["lang"],
                engine=record["engine"],
                output=record["output"],
                references=tuple(record["references"]),
                bucket=record["bucket"],
                zipf=record["zipf"],
                kinds=tuple(record.get("kinds", ())),
                word_count_mismatch=bool(record.get("word_count_mismatch", False)),
                log_messages=tuple(record.get("log_messages", ())),
                branch=record.get("branch"),
                branch_reason=record.get("branch_reason"),
            )
            if row.engine == "today" and row.branch is None:
                raise RunFileError(
                    f"{path}:{lineno} is a `today` row with no branch. Every "
                    "LLM call must be classified (Amendment B); an "
                    "unclassified one means the recording wrapper was not "
                    "installed, which happens when gemma.gemma_ipa was "
                    "patched instead of app.pronunciation.g2p.gemma_ipa. "
                    "Scoring this would report the eSpeak floor as the "
                    "model's output."
                )
            if row.branch is not None and row.branch not in BRANCHES:
                raise RunFileError(
                    f"{path}:{lineno} has branch={row.branch!r}, which is not "
                    f"one of {list(BRANCHES)}"
                )
            if row.key in seen:
                raise RunFileError(
                    f"{path}:{lineno} repeats {row.key}, first seen at line "
                    f"{seen[row.key]}. summarise would count it twice into "
                    "the mean and once into the shared set, so this is "
                    "refused rather than scored."
                )
            seen[row.key] = lineno
            rows.append(row)

    if header is None:
        raise RunFileError(f"{path} has no header line")
    return RunFile(header=header, rows=tuple(rows))


# ---------------------------------------------------------------------------
# Scores files: what `score` writes and `report` reads.
# ---------------------------------------------------------------------------
# A second on-disk stage, for the same reason as the first: rendering is
# presentation and should never be a reason to re-score, let alone re-generate.
# Provenance travels with the scores because `ruleset_version` is only known
# here -- normalisation is a scoring concern, so a run file cannot carry it.

SCORES_FORMAT_VERSION = 2

# Cell fields needing a type the JSON round-trip would otherwise flatten.
_COUNTER_FIELDS = ("refusal_reasons", "dropped_histogram",
                   "branches", "branch_reasons")
_TUPLE_FIELDS = ("engines_compared", "raw_ci", "kokoro_ci")


def _cell_to_json(cell) -> dict:
    """Every field of the dataclass, derived rather than listed.

    Listed by hand this went wrong immediately: `n_over_ceiling`,
    `raw_max`, `raw_max_name` and `lang_per_name_best` were added to `Cell`
    and not here, so the renderer read their defaults and printed "0" and
    "—" for figures that had been computed correctly. Deriving the field list
    makes forgetting one impossible.
    """
    from dataclasses import fields as dataclass_fields

    out = {}
    for field in dataclass_fields(cell):
        value = getattr(cell, field.name)
        if field.name in _COUNTER_FIELDS:
            out[field.name] = dict(value)
        elif field.name in _TUPLE_FIELDS:
            out[field.name] = list(value)
        else:
            out[field.name] = value
    return out


def write_scores(path: str | Path, provenance: dict, header: dict, cells: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": SCORES_FORMAT_VERSION,
        "provenance": provenance,
        "run_header": header,
        "cells": [_cell_to_json(c) for c in cells.values()],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


def read_scores(path: str | Path):
    """Return ``(Provenance, run_header, cells)`` ready for report.render."""
    from collections import Counter
    from dataclasses import fields as dataclass_fields

    from eval.provenance import Provenance
    from eval.score import Cell

    path = Path(path)
    if not path.exists():
        raise RunFileError(f"no scores file at {path}")
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if payload.get("format") != SCORES_FORMAT_VERSION:
        raise RunFileError(
            f"{path} is scores format {payload.get('format')!r}, this reader "
            f"is {SCORES_FORMAT_VERSION}"
        )

    known = {f.name for f in dataclass_fields(Cell)}
    cells = {}
    for raw in payload["cells"]:
        unknown = set(raw) - known
        if unknown:
            raise RunFileError(
                f"{path} has cell field(s) {sorted(unknown)} this Cell does "
                "not define; it was written by a newer harness"
            )
        kwargs = {}
        for name, value in raw.items():
            if name in _COUNTER_FIELDS:
                kwargs[name] = Counter(value)
            elif name in _TUPLE_FIELDS:
                kwargs[name] = tuple(value)
            else:
                kwargs[name] = value
        cell = Cell(**kwargs)
        cells[(cell.lang, cell.engine)] = cell
    return Provenance(**payload["provenance"]), payload["run_header"], cells
