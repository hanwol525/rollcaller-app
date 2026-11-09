"""Kokoro's phoneme vocabulary, fetched at runtime.

The vocabulary is a closed set of 114 symbols and anything outside it is
dropped **silently** by ``kokoro/model.py`` -- so an unrenderable symbol does
not degrade a clip, it deletes a sound from it. That is what the harness's
Kokoro-side figure measures, so the set has to be the real one rather than a
copy that can go stale.

``app/tests/test_models.py:484-515`` fetches the same file for its drift
detector, but with no ``revision=``, so it tracks a moving ``main``. The
harness resolves the revision explicitly and records it in provenance: a
baseline that cannot say which vocabulary it was scored against is not a
baseline.
"""
from __future__ import annotations

import json
from functools import lru_cache

KOKORO_REPO = "hexgrad/Kokoro-82M"

# Not IPA, and panphon segments none of them: misaki's own symbols (the
# capitals), ASCII punctuation, and the prosody arrows. They are *in* the
# vocabulary, so the Kokoro-side filter keeps them while the scorer cannot
# read them -- which is exactly why "characters dropped" is reported as its
# own column rather than folded into the distance.
NON_IPA_IN_VOCAB = frozenset('AIOQSTWY!"(),.:;?—“”…→↓↗↘')


@lru_cache(maxsize=1)
def fetch(cache_dir: str | None = None) -> tuple[frozenset[str], str]:
    """Return ``(vocab, revision)``. Network call; cached per process."""
    from huggingface_hub import HfApi, hf_hub_download

    revision = HfApi().model_info(KOKORO_REPO).sha
    path = hf_hub_download(
        KOKORO_REPO, "config.json", revision=revision, cache_dir=cache_dir
    )
    with open(path, encoding="utf-8") as fh:
        return frozenset(json.load(fh)["vocab"]), revision
