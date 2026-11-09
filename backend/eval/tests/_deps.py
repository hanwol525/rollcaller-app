"""Optional-dependency probes, following app/tests/_deps.py.

``find_spec`` rather than a real import, for the same reason that file gives:
importing to test for presence drags the dependency's own import cost into
collection. Here it also keeps ``pytest eval/tests`` green on a venv that has
the app's deps but not the harness's, which is the state of a fresh clone
before ``pip install -r eval/requirements.txt``.
"""
from importlib.util import find_spec


def _have(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


PANPHON_AVAILABLE = _have("panphon")
WORDFREQ_AVAILABLE = _have("wordfreq")
PHONEMIZER_AVAILABLE = _have("phonemizer")
HF_HUB_AVAILABLE = _have("huggingface_hub")
