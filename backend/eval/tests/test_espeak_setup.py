"""The espeak pin, and the two things it is there to prevent.

The failure it guards against is silent: `g2p.warm()` catches every exception
and negative-caches the result, so a library phonemizer cannot find produces
`""` from `espeak_ipa()` rather than an error, and a whole run reads as 100%
refusal in every language instead of as void.
"""
import importlib
import importlib.resources
import importlib.util
import sys

import pytest

from eval import espeak_setup


@pytest.fixture(autouse=True)
def _fresh():
    """`pin_bundled_espeak` memoises, and `eval/__init__` already called it."""
    saved = (espeak_setup._result, espeak_setup._reason)
    espeak_setup._result = None
    espeak_setup._reason = None
    yield
    espeak_setup._result, espeak_setup._reason = saved


class TestItNeverHardcodesALibraryFilename:
    def test_the_path_comes_from_the_loader_not_from_a_literal(self):
        """`libespeak-ng.so` / `.dylib` / `espeak-ng.dll` -- and no `lib`
        prefix on Windows. Any literal is wrong on two platforms of three.
        """
        src = (importlib.resources.files("eval") / "espeak_setup.py").read_text(
            encoding="utf-8"
        )
        body = "\n".join(
            line for line in src.splitlines()
            if not line.strip().startswith("#")
        )
        _, _, code = body.partition('"""')
        _, _, code = code.partition('"""')
        for literal in ("libespeak-ng.so", "libespeak-ng.dylib", "espeak-ng.dll"):
            assert literal not in code, f"hardcoded {literal}"


class TestItDegradesRatherThanRaises:
    def test_a_missing_loader_skips_the_pin_and_says_why(self):
        """A host with a system espeak-ng still works by discovery, so an
        absent loader must not be fatal.
        """
        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(sys.modules, "espeakng_loader", None)
            assert espeak_setup.pin_bundled_espeak() is None
        assert "espeakng-loader not installed" in espeak_setup.reason()

    def test_a_loader_that_raises_skips_the_pin_and_records_the_error(self):
        class Boom:
            @staticmethod
            def get_library_path():
                raise OSError("no wheel for this platform")

            @staticmethod
            def get_data_path():
                raise OSError("no wheel for this platform")

        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(sys.modules, "espeakng_loader", Boom)
            assert espeak_setup.pin_bundled_espeak() is None
        assert "OSError" in espeak_setup.reason()

    def test_it_is_idempotent(self):
        first = espeak_setup.pin_bundled_espeak()
        assert espeak_setup.pin_bundled_espeak() == first


class TestTheVersionCheckLivesInProvenanceNotHere:
    def test_the_pin_does_not_gate_on_a_version(self):
        """A mismatch belongs in the run file, where `provenance._espeak`
        records it, not in a console no one keeps. Gating here would also
        break `pytest eval/tests` at import time.
        """
        from eval import provenance

        assert provenance.EXPECTED_ESPEAK == (1, 52, 0)
        version, library, warning = provenance._espeak()
        assert library, "provenance must record which library won"
        if version != "1.52.0":
            assert warning, "a version mismatch must surface as a warning"


@pytest.mark.skipif(
    importlib.util.find_spec("espeakng_loader") is None
    or importlib.util.find_spec("phonemizer") is None,
    reason="espeakng-loader or phonemizer not installed",
)
class TestOnAHostThatHasBoth:
    def test_the_pin_applies_and_points_at_the_wheel(self):
        result = espeak_setup.pin_bundled_espeak()
        assert result is not None
        library, data = result
        assert "espeakng_loader" in library
        assert "espeak-ng-data" in data

    def test_phonemizer_resolves_to_the_pinned_library(self):
        espeak_setup.pin_bundled_espeak()
        from phonemizer.backend.espeak.wrapper import EspeakWrapper

        assert "espeakng_loader" in str(EspeakWrapper().library())
