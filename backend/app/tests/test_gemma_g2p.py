"""Tests for the Gemma-powered g2p fallback (new tests only — no existing
tests modified).

- ``test_sanitizer_strips_contamination``: the sanitizer turns model-style
  output into bare IPA in the eSpeak-matching format.
- ``test_g2p_falls_back_when_gemma_unavailable``: with Gemma env unset, the
  g2p path still yields IPA via eSpeak and the source is still ``IpaSource.g2p``.
"""
from __future__ import annotations

from app.models import IPASource
from app.pronunciation.gemma import _sanitize_ipa, gemma_ipa
from app.pronunciation.g2p import g2p, espeak_ipa

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