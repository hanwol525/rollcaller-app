"""Pin phonemizer to the espeak-ng that ships in the venv, before anything
constructs a backend.

**Why this is not optional.** `g2p.warm()` catches *every* exception, sets
`_backends[language] = None` and negative-caches it, so a library phonemizer
cannot find is indistinguishable at the call site from a working one:
`espeak_ipa()` returns `""` and a single WARNING line is the only trace. A
whole run would then read as 100% refusal in every language, for both eSpeak
engines, and the numbers would be measuring an empty string. The repo relies
on phonemizer discovering espeak-ng by itself, which works wherever a
package-managed espeak-ng exists and fails silently where it does not --
notably on Windows, which has no system espeak-ng to find.

`espeakng-loader` carries the library and the full `espeak-ng-data/` as a
wheel, per platform, and is already in the venv. Pinning to it makes the
harness work the same way everywhere and makes the library path a recorded,
reproducible fact rather than a property of the host.

**Two traps this avoids.** The library's filename differs per platform --
`libespeak-ng.so`, `libespeak-ng.dylib`, `espeak-ng.dll`, no `lib` prefix on
Windows -- so it must never be hardcoded; `get_library_path()` resolves it.
And `make_library_available()` is *not* sufficient: it only adds the
directory to the DLL search path and never tells phonemizer where the library
is. Only `set_library` does that.

**Ordering is load-bearing.** `EspeakWrapper.set_library` sets a class
attribute consulted when a backend is *constructed*, so this has to run
before the first `EspeakBackend(...)`. It is called from `eval/__init__.py`,
which Python runs before any `eval.*` submodule -- and `eval/dataset.py`
imports `app.pronunciation.g2p` at module level, so "before the submodules"
is the only safe point.

**The version is not checked here on purpose.** `provenance._espeak()`
already reads the resolved version, compares it against `EXPECTED_ESPEAK`
and records both the path and a warning into every run file. That is the
right place: a mismatch belongs in the artifact, not only in a console no
one keeps.
"""
from __future__ import annotations

_result: tuple[str, str] | None = None
_reason: str | None = None


def pin_bundled_espeak() -> tuple[str, str] | None:
    """Point phonemizer at the bundled library. Idempotent.

    Returns ``(library_path, data_path)`` when the pin was applied, or
    ``None`` when it was skipped -- in which case `reason()` says why and
    phonemizer falls back to its own discovery, which is the behaviour the
    harness had before this existed.
    """
    global _result, _reason
    if _result is not None or _reason is not None:
        return _result

    try:
        import espeakng_loader
    except ImportError as e:
        # Not fatal: a host with a system espeak-ng still works by discovery.
        _reason = f"espeakng-loader not installed ({e})"
        return None

    try:
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
    except ImportError as e:
        _reason = f"phonemizer not installed ({e})"
        return None

    try:
        library = str(espeakng_loader.get_library_path())
        data = str(espeakng_loader.get_data_path())
        EspeakWrapper.set_library(library)
        EspeakWrapper.set_data_path(data)
    except Exception as e:
        # Deliberately broad, and deliberately non-fatal. Discovery may still
        # succeed; if it does not, provenance records "unavailable" and the
        # run is visibly void rather than quietly empty.
        _reason = f"{type(e).__name__}: {e}"
        return None

    _result = (library, data)
    return _result


def reason() -> str | None:
    """Why the pin was skipped, or ``None`` if it was applied or not tried."""
    return _reason
