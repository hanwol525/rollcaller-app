"""Optional-dependency probes shared by the test modules.

Mirrors the `shutil.which("ffmpeg")` idiom already used in test_transcode.py,
but for an importable package rather than a binary on PATH.

Deliberately uses `find_spec` rather than a real import: the push-gate CI job
installs the full pinned requirements (so kokoro IS on sys.path) and must not
pay the cost of importing it — importing kokoro pulls torch. The marker, not
this probe, is what keeps the heavy tests out of that job; this is the safety
net for environments that simply lack the package, such as the slim local venv.

Known limitation, accepted: `find_spec` answers "is there a module on the path",
not "can it be imported". A broken or half-installed kokoro reports available
here, so its tests run and fail rather than skipping. That is the right signal
for a broken install — a skip would hide it.
"""
from __future__ import annotations

import importlib.util


def _spec_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # ValueError: the module is in sys.modules with __spec__ set to None
        # (happens when something injects a stub). Treat as unavailable.
        return False


KOKORO_AVAILABLE = _spec_exists("kokoro")
"""True when the Kokoro TTS package is importable — gates the @integration tests."""
