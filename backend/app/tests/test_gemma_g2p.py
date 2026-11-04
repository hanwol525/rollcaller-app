"""Tests for the Gemma-powered g2p fallback (new tests only — no existing
tests modified).

- ``test_sanitizer_strips_contamination``: the sanitizer turns model-style
  output into bare IPA in the eSpeak-matching format.
- ``test_g2p_falls_back_when_gemma_unavailable``: with Gemma env unset, the
  g2p path still yields IPA via eSpeak and the source is still ``IpaSource.g2p``.
- ``TestSwallowSitesAreNotSilent``: every silent failure path in g2p.py and
  gemma.py reports a cause that is distinguishable from the others.
"""
from __future__ import annotations

import ast
import logging
import pathlib
import sys
import types

import httpx
import pytest

from app.models import IPASource
from app.tests._deps import KOKORO_AVAILABLE
from app.pronunciation import g2p as g2p_module, gemma as gemma_module
from app.pronunciation.gemma import _sanitize_ipa, gemma_ipa
from app.pronunciation.g2p import g2p, espeak_ipa


def _configure_gemma(monkeypatch, api_key="key-test"):
    """Give gemma_ipa a complete config so it proceeds past the env check."""
    monkeypatch.setenv("GEMMA_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("GEMMA_MODEL", "gemma-test")
    monkeypatch.setenv("GEMMA_API_KEY", api_key)
    from app.config import Settings
    monkeypatch.setattr("app.pronunciation.gemma.settings", Settings())


def _fake_phonemizer(monkeypatch, exc):
    """Install a phonemizer whose EspeakBackend construction raises ``exc``.

    Deliberately hermetic: asserting on the exception the *real* import
    produces would make the test depend on whether phonemizer is installed,
    which is the trap CLAUDE.md's "don't let a test depend on a dependency
    being absent" Gotcha records.
    """
    backend_mod = types.ModuleType("phonemizer.backend")

    def _raise(*args, **kwargs):
        raise exc

    backend_mod.EspeakBackend = _raise
    pkg = types.ModuleType("phonemizer")
    pkg.backend = backend_mod
    monkeypatch.setitem(sys.modules, "phonemizer", pkg)
    monkeypatch.setitem(sys.modules, "phonemizer.backend", backend_mod)


def _messages(caplog, module_suffix):
    """Formatted messages from one pronunciation module's logger only."""
    return [
        r.getMessage() for r in caplog.records
        if r.name == f"app.pronunciation.{module_suffix}"
    ]

# Build think-wrapper tags programmatically (chr-based) so they never appear
# as literal angle-bracket tags in source, which some tooling strips.
_T_OPEN = chr(60) + "think" + chr(62)
_T_CLOSE = chr(60) + "/think" + chr(62)


class TestSanitizerStripsContamination:
    """The sanitizer must turn noisy model output into bare IPA."""

    def test_prose_with_slash_wrapped_ipa(self):
        """'The IPA is /SHEE-vawn/' -> bare IPA."""
        raw = "The IPA is /ʃɪˈvɔːn/"
        assert _sanitize_ipa(raw) == "ʃɪˈvɔːn"

    def test_backtick_wrapped_output(self):
        """Backtick/code-fence wrapped IPA -> bare IPA."""
        raw = "```\nʃɪˈvɔːn\n```"
        assert _sanitize_ipa(raw) == "ʃɪˈvɔːn"

    def test_think_block_stripped(self):
        """A reasoning block must be removed, leaving the bare IPA."""
        raw = (
            _T_OPEN
            + "Siobhan is an Irish name. Let me think about the pronunciation."
            + _T_CLOSE
            + " ʃɪˈvɔːn"
        )
        assert _sanitize_ipa(raw) == "ʃɪˈvɔːn"

    def test_ipa_label_stripped(self):
        """An 'IPA:'-labelled string -> bare IPA."""
        raw = "IPA: ˈdmɪtri"
        assert _sanitize_ipa(raw) == "ˈdmɪtri"

    def test_bracket_wrapped_ipa(self):
        """[...] wrapped IPA -> bare IPA."""
        raw = "[ˈsɜːrʃə]"
        assert _sanitize_ipa(raw) == "ˈsɜːrʃə"

    def test_quotes_stripped(self):
        """Surrounding quotes -> bare IPA."""
        raw = '"ʃɪˈvɔːn"'
        assert _sanitize_ipa(raw) == "ʃɪˈvɔːn"

    def test_bare_ipa_passthrough(self):
        """Already-bare IPA passes through unchanged."""
        raw = "ˈdmɪtri"
        assert _sanitize_ipa(raw) == "ˈdmɪtri"

    def test_empty_returns_none(self):
        assert _sanitize_ipa("") is None
        assert _sanitize_ipa(None) is None  # type: ignore[arg-type]

    def test_implausibly_long_returns_none(self):
        """A prose paragraph masquerading as IPA is rejected."""
        raw = "a" * 200
        assert _sanitize_ipa(raw) is None

    def test_mid_string_punctuation_returns_none(self):
        """ASCII sentence punctuation mid-string -> None (prose marker)."""
        raw = "ʃɪˈvɔːn, the pronunciation"
        assert _sanitize_ipa(raw) is None

    def test_embedded_english_word_returns_none(self):
        """Embedded common English word -> None (prose contamination)."""
        raw = "the pronunciation is ʃɪˈvɔːn"
        assert _sanitize_ipa(raw) is None


class TestSanitizerPreservesLegitimateIPA:
    """The sanitizer must not destroy or reject real IPA.

    Both failures were §7-verified in AUDIT.md and had no coverage at all:
    nothing in the repo asserted anything about `ç`, nasal marks, the
    syllable separator, or short all-ASCII IPA.
    """

    def test_c_cedilla_survives(self):
        """`ç` is a legitimate IPA symbol and one Kokoro accepts. It used to
        be NFD-decomposed and shipped as a bare `c` -- a different phoneme."""
        assert _sanitize_ipa("ɪç") == "ɪç"

    def test_nasal_marks_survive(self):
        """U+0303 is the only combining mark in Kokoro's vocabulary, so
        stripping it destroyed a phoneme the voice can say. João's name."""
        assert _sanitize_ipa("ˈdʒoːɐ̃w̃") == "ˈdʒoːɐ̃w̃"

    def test_syllable_separator_survives(self):
        """The ASCII period is the IPA syllable separator, and U+002E is in
        Kokoro's vocabulary -- it is not sentence punctuation."""
        assert _sanitize_ipa("ˈmɑːkɪs.ˈdʒuːniə") == "ˈmɑːkɪs.ˈdʒuːniə"

    def test_short_ascii_ipa_is_accepted(self):
        """Bare `no`, `me`, `it` are plausible complete IPA for short names,
        and were rejected outright by the old stopword membership test."""
        for text in ("no", "me", "it"):
            assert _sanitize_ipa(text, name="short") == text

    def test_multi_word_ipa_is_accepted(self):
        """A space counts as prose structure only alongside a short stopword;
        a real two-word name must still pass."""
        assert _sanitize_ipa("hˈænə wˈʊlfsən") == "hˈænə wˈʊlfsən"

    def test_leaked_graphemes_map_to_their_phoneme(self):
        """Every mapping targets a symbol verified present in Kokoro's
        vocabulary, so none of them is silently dropped at synthesis."""
        assert _sanitize_ipa("ɲ") == "ɲ"              # already a phoneme
        assert _sanitize_ipa("ñ") == "ɲ"              # Spanish grapheme -> phoneme
        assert _sanitize_ipa("łukasz") == "wukasz"
        assert _sanitize_ipa("šžč") == "ʃʒtʃ"


class TestG2pFallsBackWhenGemmaUnavailable:
    """With Gemma env unset, the g2p path still yields IPA via eSpeak and the
    source is still ``IpaSource.g2p``."""

    def test_gemma_ipa_returns_none_when_env_unset(self, monkeypatch):
        """No GEMMA_* env -> gemma_ipa returns None immediately (-> eSpeak)."""
        monkeypatch.delenv("GEMMA_BASE_URL", raising=False)
        monkeypatch.delenv("GEMMA_MODEL", raising=False)
        monkeypatch.delenv("GEMMA_API_KEY", raising=False)
        # Reload settings so the unset env is reflected.
        from app.config import Settings
        monkeypatch.setattr(
            "app.pronunciation.gemma.settings", Settings()
        )
        assert gemma_ipa("Bob") is None

    def test_g2p_yields_espeak_floor_when_gemma_unavailable(self, monkeypatch):
        """g2p('Bob') still returns non-empty IPA via eSpeak when Gemma is off."""
        # Force gemma_ipa to the unavailable state.
        monkeypatch.setattr("app.pronunciation.g2p.gemma_ipa", lambda _n: None)
        result = g2p("Bob")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_espeak_produces_real_phonemes_not_the_spelling(self, monkeypatch):
        """The first content assertion on the pipeline's actual output.

        AUDIT.md §5.5 calls "nothing asserts what g2p() returns" the biggest
        untested risk, and it was right: for months g2p("Siobhan") returned
        "/siobhan/" while every test passed, because they only ever checked
        isinstance and len > 0 -- which a placeholder satisfies perfectly.
        """
        monkeypatch.setattr("app.pronunciation.g2p.gemma_ipa", lambda _n: None)
        assert espeak_ipa("Bob") == "bˈɑːb"
        assert g2p("Siobhan") == "ʃɪvˈɔːn"
        assert g2p("Siobhan") != "/siobhan/"      # the old placeholder, named

    def test_hard_names_seed(self, monkeypatch):
        """Seed of the eval set Brief 04 expands. Small, and honest.

        Exact IPA only where eSpeak is demonstrably right. The anglicised
        ones are asserted as "real IPA, not a placeholder" and no more --
        pinning eSpeak's current output as *expected* would fight Brief 04,
        whose whole job is to improve exactly those names.

        Recorded under espeak-ng 1.52.0; the engine is pinned by
        test_garbage_guard_constants_match_espeak in test_models.py.
        """
        monkeypatch.setattr("app.pronunciation.g2p.gemma_ipa", lambda _n: None)

        EXACT = {
            "Bob": "bˈɑːb",
            "Siobhan": "ʃɪvˈɔːn",
            "Hannah Wolfson": "hˈænə wˈʊlfsən",
        }
        # eSpeak anglicises these today. Brief 04's targets, not assertions:
        #   Malgorzata -> mˌælɡɔːɹzˈɑːɾə   (Polish is maw-go-ZHA-ta)
        #   Saoirse    -> sˈɜːʃə
        #   Dmitri     -> dəmˈiːtɹi
        #   Xiuying    -> zˈɪaɪɪŋ
        ANGLICISED = ("Malgorzata", "Saoirse", "Dmitri", "Xiuying")

        failures = []
        for name, expected in EXACT.items():
            got = g2p(name)
            if got != expected:
                failures.append(f"{name!r}: expected {expected!r}, got {got!r}")
        for name in ANGLICISED:
            got = g2p(name)
            if not got:
                failures.append(f"{name!r}: empty")
            elif got == f"/{name.lower()}/":
                failures.append(f"{name!r}: still the placeholder, got {got!r}")
            elif got.isascii():
                failures.append(
                    f"{name!r}: all-ASCII {got!r} -- that is spelling, not IPA"
                )
        assert not failures, failures

    def test_gemma_ipa_returns_bare_ipa_like_espeak(self, monkeypatch):
        """Both engines return bare IPA, so either is a drop-in for the other.

        This replaces an assertion that gemma_ipa wraps in /.../, which only
        held while the eSpeak floor was broken and its placeholder was the one
        path producing slashes.
        """
        # Provide config so gemma_ipa proceeds past the env check.
        monkeypatch.setenv("GEMMA_BASE_URL", "https://example.test/v1")
        monkeypatch.setenv("GEMMA_MODEL", "gemma-test")
        monkeypatch.setenv("GEMMA_API_KEY", "key-test")
        from app.config import Settings
        monkeypatch.setattr("app.pronunciation.gemma.settings", Settings())

        # Stub the HTTP client so no network is hit: a bare-IPA model response.
        class _FakeResp:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": "bɒb"}}]}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def post(self, *a, **kw):
                return _FakeResp()

        import httpx
        monkeypatch.setattr(httpx, "Client", _FakeClient)

        result = gemma_ipa("Bob")
        # Bare: _sanitize_ipa strips any wrapper the model emits, and nothing
        # re-adds one before storage.
        assert result == "bɒb"
        # And interchangeable in shape with espeak_ipa's output.
        espeak = espeak_ipa("Bob")
        for value in (result, espeak):
            assert not value.startswith("/") and not value.endswith("/")

    def test_no_producer_returns_slash_wrapped_ipa(self, monkeypatch):
        """The settled convention: bare in storage, slashes only at Kokoro.

        Shape only -- the phoneme content is Unit 6's to assert. The stubbed
        model deliberately replies *with* slashes, proving _sanitize_ipa
        strips them rather than the caller re-adding them.
        """
        from app.pronunciation import recognize

        _configure_gemma(monkeypatch)

        class _Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "/bɒb/"}}]}

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr(httpx, "Client", _Client)

        produced = {
            "gemma_ipa": gemma_ipa("Bob"),
            "espeak_ipa": espeak_ipa("Bob"),
            "g2p._fallback": g2p_module._fallback("Hannah Wolfson"),
            # recognize._fallback is gone: it invented `rec###` IPA, which the
            # TTS guard then refused, so a recorded participant got no clip.
            # recognize() returns None now and has no format to get wrong.
        }
        for label, value in produced.items():
            assert "/" not in value, f"{label} returned {value!r}"

    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
    def test_render_keeps_g2p_source_when_gemma_unavailable(
        self, authed_client, make_space
    ):
        """End-to-end: with Gemma env unset (the test default), a prep-clips
        run fills g2p names via eSpeak and the source is still 'g2p'."""
        space = make_space()
        authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}, {"name": "Bob"}],
        )
        resp = authed_client.post(f"/spaces/{space['id']}/render")
        assert resp.status_code == 200
        roster = authed_client.get(
            f"/spaces/{space['id']}/participants"
        ).json()
        for p in roster:
            assert p["ipa_text"] is not None
            # The source is still the g2p provenance category — unchanged.
            assert p["ipa_source"] == IPASource.g2p.value
            assert p["clip_key"] is not None


class TestSwallowSitesAreNotSilent:
    """Each silent failure path must say which cause it was.

    Before this unit, "Gemma not configured", "Gemma timed out", "Gemma
    returned prose" and "phonemizer missing" all produced the same
    ``/firstname_lastname/`` with no log line, which is why the dead eSpeak
    floor survived months of use.
    """

    # --- g2p.py -----------------------------------------------------------

    def test_warm_failure_names_the_exception_type(self, monkeypatch, caplog):
        """The type is the diagnosis; without it all four causes look alike."""
        caplog.set_level(logging.WARNING)
        monkeypatch.setattr(g2p_module, "_warned_warm_failed", set())
        monkeypatch.setattr(g2p_module, "_backends", {})
        _fake_phonemizer(monkeypatch, TypeError("unexpected keyword 'backend'"))

        g2p_module.warm()

        msgs = _messages(caplog, "g2p")
        assert len(msgs) == 1
        assert "eSpeak warm failed" in msgs[0]
        assert "TypeError" in msgs[0]
        # The failure is cached as a negative entry rather than left absent --
        # that is what stops the self-heal repeating a ~1.6 s construction
        # once per participant.
        assert g2p_module._backends["en-us"] is None

    def test_warm_failure_is_reported_once_per_process(
        self, monkeypatch, caplog
    ):
        """The lazy self-heal calls warm() on first use per language; without
        the dedupe set a broken backend would log once per participant."""
        caplog.set_level(logging.WARNING)
        monkeypatch.setattr(g2p_module, "_warned_warm_failed", set())
        monkeypatch.setattr(g2p_module, "_backends", {})
        _fake_phonemizer(monkeypatch, RuntimeError("espeak-ng not found"))

        g2p_module.warm()
        g2p_module.warm()
        g2p_module.warm()

        assert len(_messages(caplog, "g2p")) == 1

    def test_phonemize_failure_names_the_name_and_the_exception(
        self, monkeypatch, caplog
    ):
        caplog.set_level(logging.WARNING)

        class _Boom:
            def phonemize(self, *args, **kwargs):
                raise RuntimeError("espeak library vanished")

        # Seeded by membership, so the self-heal leaves it in place.
        monkeypatch.setattr(g2p_module, "_backends", {"en-us": _Boom()})

        assert espeak_ipa("Bob") == ""

        msgs = _messages(caplog, "g2p")
        assert any(
            "phonemize failed" in m and "RuntimeError" in m and "'Bob'" in m
            for m in msgs
        ), msgs

    def test_no_ipa_path_names_the_participant(self, monkeypatch, caplog):
        """A ceremony getting no pronunciation is the one line that must be
        per-name, not deduped -- which names it hit is the whole point."""
        caplog.set_level(logging.WARNING)
        # Force a genuine warm failure instead of relying on espeak being
        # absent: with the floor fixed, a real backend phonemizes this name
        # successfully and no placeholder is ever produced.
        monkeypatch.setattr(g2p_module, "_warned_warm_failed", set())
        monkeypatch.setattr(g2p_module, "_backends", {})
        _fake_phonemizer(monkeypatch, RuntimeError("espeak-ng not found"))

        assert espeak_ipa("Hannah Wolfson") == ""

        # Two records: the warm failure, then the no-IPA line.
        msgs = _messages(caplog, "g2p")
        assert any(
            "no IPA for name=" in m and "'Hannah Wolfson'" in m
            for m in msgs
        ), msgs

    # --- gemma.py: gemma_ipa ---------------------------------------------

    def test_unconfigured_names_which_variables_are_unset(
        self, monkeypatch, caplog
    ):
        """"Not configured" is a shrug; naming the variables is actionable."""
        caplog.set_level(logging.WARNING)
        monkeypatch.setattr(gemma_module, "_warned_unconfigured", False)
        from app.config import Settings
        monkeypatch.delenv("GEMMA_BASE_URL", raising=False)
        monkeypatch.delenv("GEMMA_MODEL", raising=False)
        monkeypatch.delenv("GEMMA_API_KEY", raising=False)
        monkeypatch.setattr(
            "app.pronunciation.gemma.settings",
            Settings(gemma_base_url=None, gemma_model=None, gemma_api_key=None),
        )

        assert gemma_ipa("Bob") is None

        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        for env in ("GEMMA_BASE_URL", "GEMMA_MODEL", "GEMMA_API_KEY"):
            assert env in msgs[0]

    def test_unconfigured_is_reported_once_per_process(
        self, monkeypatch, caplog
    ):
        caplog.set_level(logging.WARNING)
        monkeypatch.setattr(gemma_module, "_warned_unconfigured", False)
        from app.config import Settings
        monkeypatch.delenv("GEMMA_BASE_URL", raising=False)
        monkeypatch.delenv("GEMMA_MODEL", raising=False)
        monkeypatch.delenv("GEMMA_API_KEY", raising=False)
        monkeypatch.setattr(
            "app.pronunciation.gemma.settings",
            Settings(gemma_base_url=None, gemma_model=None, gemma_api_key=None),
        )

        for _ in range(3):
            gemma_ipa("Bob")

        assert len(_messages(caplog, "gemma")) == 1

    def test_missing_httpx_is_logged(self, monkeypatch, caplog):
        """Otherwise-unreachable branch: httpx ships with FastAPI's TestClient,
        so only a forced absence exercises it."""
        caplog.set_level(logging.WARNING)
        _configure_gemma(monkeypatch)
        monkeypatch.setattr(gemma_module, "_warned_no_httpx", False)
        monkeypatch.setitem(sys.modules, "httpx", None)

        assert gemma_ipa("Bob") is None

        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "httpx not installed" in msgs[0]

    def test_http_error_logs_the_status_and_never_the_token(
        self, monkeypatch, caplog
    ):
        caplog.set_level(logging.WARNING)
        _configure_gemma(monkeypatch, api_key="super-secret-key")

        class _Resp:
            status_code = 503
            text = '{"error":"upstream unavailable"}'

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr(httpx, "Client", _Client)

        assert gemma_ipa("Bob") is None

        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "503" in msgs[0]
        # The bearer token lives in `headers`; it must never be logged.
        assert "super-secret-key" not in msgs[0]

    def test_request_failure_names_the_exception_type(
        self, monkeypatch, caplog
    ):
        """One broad except covers network, timeout, JSON and missing-field;
        the type is what separates them."""
        caplog.set_level(logging.WARNING)
        _configure_gemma(monkeypatch)

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **kw):
                raise httpx.ReadTimeout("timed out")

        monkeypatch.setattr(httpx, "Client", _Client)

        assert gemma_ipa("Bob") is None

        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "ReadTimeout" in msgs[0]

    def test_empty_content_is_logged(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING)
        _configure_gemma(monkeypatch)

        class _Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": ""}}]}

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr(httpx, "Client", _Client)

        assert gemma_ipa("Bob") is None

        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "empty content" in msgs[0]
        assert "'Bob'" in msgs[0]

    # --- gemma.py: _sanitize_ipa ------------------------------------------

    def test_empty_model_output_is_logged(self, caplog):
        caplog.set_level(logging.WARNING)
        assert _sanitize_ipa("", name="Bob") is None
        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "empty model output" in msgs[0]

    def test_cleaned_to_nothing_is_logged(self, caplog):
        caplog.set_level(logging.WARNING)
        assert _sanitize_ipa("```", name="Bob") is None
        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "nothing left after cleaning" in msgs[0]

    def test_overlong_rejection_reports_both_lengths(self, caplog):
        caplog.set_level(logging.WARNING)
        assert _sanitize_ipa("a" * 200, name="Bob") is None
        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "200" in msgs[0] and "80" in msgs[0]

    def test_punctuation_rejection_names_the_character(self, caplog):
        caplog.set_level(logging.WARNING)
        assert _sanitize_ipa("ʃɪˈvɔːn, the pronunciation", name="Siobhan") is None
        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "punctuation ','" in msgs[0]
        assert "'Siobhan'" in msgs[0]

    def test_prose_rejection_names_the_matched_word(self, caplog):
        """Naming the match is what evidences a rejection.

        Measured: every IPA symbol is a ``\\w`` character, including the
        stress mark and the length mark, so ``\\b`` fires only at the string
        ends -- which is why bare all-ASCII IPA was rejected while "ˈmeɪ"
        passed. Short IPA survives now (see TestSanitizerPreservesLegitimateIPA);
        this keeps the log assertion on an input that is still, correctly, prose.
        """
        caplog.set_level(logging.WARNING)
        assert _sanitize_ipa("the pronunciation is ʃɪˈvɔːn", name="Siobhan") is None
        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "prose word 'the'" in msgs[0]
        assert "'Siobhan'" in msgs[0]

    def test_ambiguous_short_words_rejection_names_them(self, caplog):
        """The second tier is a live guard, not dead code: all-short-word
        prose has no unambiguous word for the first tier to catch."""
        caplog.set_level(logging.WARNING)
        assert _sanitize_ipa("is of", name="Bob") is None
        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "reads as prose" in msgs[0]
        assert "'is'" in msgs[0] and "'of'" in msgs[0]

    # --- the structural guard ---------------------------------------------

    def test_no_except_handler_in_the_pipeline_is_silent(self):
        """Encodes Brief 03 Unit 2's acceptance criterion directly, so a later
        change cannot quietly reintroduce a silent swallow. A handler that
        genuinely must stay silent should add an explicit exemption here.

        recognize.py joined the list once it gained a logger: Unit 5 left it
        out precisely because its `except Exception: pass` would have failed
        this. transcode.py is still absent -- it has no logger yet.
        """
        pronunciation = pathlib.Path(__file__).resolve().parent.parent / "pronunciation"
        offenders = []
        for module in ("g2p.py", "gemma.py", "recognize.py"):
            path = pronunciation / module
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                logs = any(
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "log"
                    for inner in node.body
                    for call in ast.walk(inner)
                    if isinstance(call, ast.Call)
                )
                if not logs:
                    offenders.append(f"{module}:{node.lineno}")
        assert not offenders, f"silent except handler(s): {offenders}"