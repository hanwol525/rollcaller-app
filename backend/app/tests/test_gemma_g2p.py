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
    """Install a phonemizer whose Backend lookup raises ``exc``.

    Deliberately hermetic: asserting on the exception the *real* import
    produces would make the test depend on whether phonemizer is installed,
    which is the trap CLAUDE.md's "don't let a test depend on a dependency
    being absent" Gotcha records.
    """
    backend_mod = types.ModuleType("phonemizer.backend")

    def _raise(*args, **kwargs):
        raise exc

    backend_mod.Backend = _raise
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

    def test_gemma_ipa_wraps_in_espeak_format(self, monkeypatch):
        """gemma_ipa returns /.../ bracketed IPA to match the eSpeak convention,
        so it is a literal drop-in for espeak_ipa (no new format invented)."""
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
        # Must be wrapped in /.../ to match espeak_ipa's convention.
        assert result == "/bɒb/"
        # And it must be interchangeable in shape with espeak_ipa output.
        espeak = espeak_ipa("Bob")
        assert espeak.startswith("/") and espeak.endswith("/")
        assert result.startswith("/") and result.endswith("/")

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
        monkeypatch.setattr(g2p_module, "_warned_warm_failed", False)
        monkeypatch.setattr(g2p_module, "_backend", None)
        _fake_phonemizer(monkeypatch, TypeError("unexpected keyword 'backend'"))

        g2p_module.warm()

        msgs = _messages(caplog, "g2p")
        assert len(msgs) == 1
        assert "eSpeak warm failed" in msgs[0]
        assert "TypeError" in msgs[0]
        # And the swallow still swallows -- this unit changes no behaviour.
        assert g2p_module._backend is None

    def test_warm_failure_is_reported_once_per_process(
        self, monkeypatch, caplog
    ):
        """Unit 3's lazy self-heal calls warm() per use; without the dedupe
        flag a broken backend becomes one line per participant per render."""
        caplog.set_level(logging.WARNING)
        monkeypatch.setattr(g2p_module, "_warned_warm_failed", False)
        monkeypatch.setattr(g2p_module, "_backend", None)
        _fake_phonemizer(monkeypatch, AttributeError("no attribute 'Backend'"))

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

        monkeypatch.setattr(g2p_module, "_backend", _Boom())

        assert espeak_ipa("Bob") == "/bob/"

        msgs = _messages(caplog, "g2p")
        assert any(
            "phonemize failed" in m and "RuntimeError" in m and "'Bob'" in m
            for m in msgs
        ), msgs

    def test_placeholder_path_names_the_participant(self, monkeypatch, caplog):
        """The placeholder reaching a real ceremony is the one line that must
        be per-name, not deduped -- which names it hit is the whole point."""
        caplog.set_level(logging.WARNING)
        monkeypatch.setattr(g2p_module, "_backend", None)

        assert espeak_ipa("Hannah Wolfson") == "/hannah_wolfson/"

        msgs = _messages(caplog, "g2p")
        assert len(msgs) == 1
        assert "'Hannah Wolfson'" in msgs[0]
        assert "/hannah_wolfson/" in msgs[0]

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
        """Naming the match is what evidences the false positive.

        Measured: every IPA symbol is a ``\\w`` character, including the
        stress mark and the length mark, so ``\\b`` fires only at the string
        ends. "ˈmeɪ" therefore passes; it is bare all-ASCII IPA that is
        wrongly rejected, exactly as the brief states.

        ⚠️ Unit 5 inverts the ``is None`` half of this -- short ASCII IPA
        must survive. Keep the log assertion when you do.
        """
        caplog.set_level(logging.WARNING)
        assert _sanitize_ipa("ˈmeɪ", name="May") == "ˈmeɪ"   # passes today, correctly
        assert _sanitize_ipa("me", name="Mei") is None       # rejected today, wrongly
        msgs = _messages(caplog, "gemma")
        assert len(msgs) == 1
        assert "prose word 'me'" in msgs[0]
        assert "'Mei'" in msgs[0]

    # --- the structural guard ---------------------------------------------

    def test_no_except_handler_in_either_module_is_silent(self):
        """Encodes this unit's acceptance criterion directly, so a later unit
        cannot quietly reintroduce a silent swallow. A handler that genuinely
        must stay silent should add an explicit exemption here."""
        pronunciation = pathlib.Path(__file__).resolve().parent.parent / "pronunciation"
        offenders = []
        for module in ("g2p.py", "gemma.py"):
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