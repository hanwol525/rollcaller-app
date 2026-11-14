"""Shared fixtures for the eval tests.

Note there is deliberately no app-style conftest here: app/tests/conftest.py
mutates os.environ and builds a database at import time, and none of that is
wanted for a pure scorer. Conftest discovery never reaches it from this
directory, but `pytest` run bare from backend/ would collect both trees in one
process and leak those env writes -- so always target `eval/tests` explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def vocab() -> frozenset[str]:
    """A small synthetic stand-in for Kokoro's vocabulary.

    Synthetic rather than a snapshot of the real 114, so a filter test states
    its own intent: `ħ`, `ʐ`, `˥` and digits are absent *because the test
    wants them dropped*. Drift against the published set is covered separately
    by test_score.py::TestPublishedVocabulary, which fetches the real one.

    **One pair is deliberately not synthetic.** It holds `ɡ` (U+0261) and not
    ASCII `g`, because that is what the real vocabulary does -- every ASCII
    lowercase letter *except* g is in the 114-set. Inverting it here would let
    a Kokoro-side test assert the opposite of what production does on the one
    character normalisation now folds for.
    """
    with open(FIXTURES / "vocab.json", encoding="utf-8") as fh:
        return frozenset(json.load(fh)["vocab"])
