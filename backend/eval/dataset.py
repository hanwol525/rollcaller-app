"""The say-my-name rows, grouped into one record per name.

Dataset: https://huggingface.co/datasets/saraelhark/say-my-name (CC BY-SA 4.0,
mined from Wiktionary via Wiktextract). Fetched at runtime, never committed.

**The dataset is keyed by pronunciation, not by name.** The ``test`` split is
3,091 rows over 2,507 names -- 1.01 variants per name in Polish, 2.47 in
Spanish. An engine emits one pronunciation per name, so it can never match all
of a name's variants at once, and scoring per row would penalise it for the
variants it did not match while double-weighting multi-variant names. So the
unit here is the **name**, carrying every attested variant as a reference; the
scorer takes the closest. Any attested pronunciation counts as correct.

The files are TSV on ``main`` -- Parquet exists only on the auto-converted
``refs/convert/parquet`` ref -- so the stdlib ``csv`` module reads them and
neither ``pyarrow`` nor ``datasets`` is needed.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field

DATASET_REPO = "saraelhark/say-my-name"

# Pinned, not floating. The card's own figures (row counts, variants per name)
# describe this revision, and a baseline whose corpus can shift underneath it
# is not reproducible. Commit c7d27b1, 2026-08-02, "Filter 81 syllable-fragment
# rows; update dataset card; drop per-language intermediates".
DATASET_REVISION = "c7d27b1ec1f444f5f6de58968ca3486104ee0b6b"

# The eight pronunciation languages. `lang` is the language the name is
# *pronounced* in, not the name's origin -- the card is explicit about that.
LANGUAGES = ("ar", "de", "es", "fr", "it", "pl", "tr", "vi")

# Dataset code -> eSpeak voice. Only French needs it: eSpeak has fr-be, fr-ch
# and fr-fr but no bare `fr`. Validated against EspeakBackend's own list
# before any warm() (see engines.py) because g2p caches a *sticky* None for a
# language whose backend failed to build, so a bad code would read as a 100%
# refusal rate for that whole language rather than as an error.
ESPEAK_CODE = {"fr": "fr-fr"}

# The file is splits/dev.tsv; the HF dataset *viewer* calls that split
# "validation". Same data, different label.
SPLITS = ("train", "dev", "test")

BUCKET_COMMON, BUCKET_MID, BUCKET_RARE = "common", "mid", "rare"


@dataclass(frozen=True)
class NameRecord:
    """One name, with every pronunciation the dataset attests for it."""

    name: str
    lang: str
    kinds: tuple[str, ...]        # given / surname / both; a name can carry several
    references: tuple[str, ...]   # all attested IPA variants, file order preserved
    bucket: str
    zipf: float                   # the rarest token's Zipf frequency; 0.0 = unattested

    @property
    def kind(self) -> str:
        return "|".join(self.kinds)


def espeak_code(lang: str) -> str:
    return ESPEAK_CODE.get(lang, lang)


def download(split: str, cache_dir: str | None = None) -> str:
    """Fetch one split's TSV and return its local path."""
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}; expected one of {SPLITS}")
    from huggingface_hub import hf_hub_download

    return hf_hub_download(
        DATASET_REPO,
        f"splits/{split}.tsv",
        repo_type="dataset",
        revision=DATASET_REVISION,
        cache_dir=cache_dir,
    )


def bucket_for(name: str, lang: str) -> tuple[str, float]:
    """Frequency bucket from the name's **rarest** token.

    Labelled a weak signal wherever it is reported: every name in this corpus
    is on Wiktionary by construction, so "rare" here means rare in running
    text, not rare as a name. ``zipf_frequency`` returns 0.0 for a token it
    has never seen, which is the common case for surnames -- so expect most
    rows to bucket as rare, and read the buckets as an ordering rather than as
    a population estimate.
    """
    from wordfreq import zipf_frequency

    tokens = [t for t in name.replace("-", " ").split() if t]
    zipf = min((zipf_frequency(t, lang) for t in tokens), default=0.0)
    if zipf >= 4.0:
        bucket = BUCKET_COMMON
    elif zipf < 3.0:
        bucket = BUCKET_RARE
    else:
        bucket = BUCKET_MID
    return bucket, zipf


def read_tsv(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def group(rows: list[dict[str, str]], langs: tuple[str, ...] = LANGUAGES) -> list[NameRecord]:
    """Collapse pronunciation rows into one NameRecord per (name, lang).

    Order is the file's, not sorted: it keeps a run reproducible and keeps
    ``--limit`` meaning "the first N", which is what makes a smoke test
    comparable across engines.
    """
    wanted = set(langs)
    seen: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        lang = (row.get("lang") or "").strip()
        name = (row.get("name") or "").strip()
        if lang not in wanted or not name:
            continue
        seen.setdefault((name, lang), []).append(row)

    out: list[NameRecord] = []
    for (name, lang), group_rows in seen.items():
        refs = tuple(
            ipa for ipa in ((r.get("ipa") or "").strip() for r in group_rows) if ipa
        )
        if not refs:
            continue  # nothing to score against
        kinds = tuple(sorted({(r.get("kind") or "").strip() for r in group_rows if r.get("kind")}))
        bucket, zipf = bucket_for(name, lang)
        out.append(NameRecord(name, lang, kinds, refs, bucket, zipf))
    return out


def load_split(
    split: str,
    cache_dir: str | None = None,
    langs: tuple[str, ...] = LANGUAGES,
    limit: int | None = None,
) -> list[NameRecord]:
    records = group(read_tsv(download(split, cache_dir)), langs)
    if limit is not None:
        # Per language, not per split: a global head would be all Arabic and
        # tell you nothing about the other seven.
        kept: dict[str, int] = {}
        capped = []
        for r in records:
            if kept.get(r.lang, 0) >= limit:
                continue
            kept[r.lang] = kept.get(r.lang, 0) + 1
            capped.append(r)
        records = capped
    return records
