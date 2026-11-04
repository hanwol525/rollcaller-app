"""Tests for non-endpoint logic: model validators, storage, pronunciation fallbacks."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import Participant, ParticipantStatus, IPASource
from app.tests._deps import (
    HF_HUB_AVAILABLE,
    KOKORO_AVAILABLE,
    PHONEMIZER_AVAILABLE,
)


class TestParticipantIPAVariant:
    """The ipa_text/ipa_source invariant: they move together.

    SQLModel table=True models do NOT run @model_validator on __init__ or
    attribute assignment, so we call validate_ipa_invariant() explicitly.
    """

    def test_both_none_is_valid(self):
        """None/None = no pronunciation received. Should be valid."""
        p = Participant(space_id=1, name="Alice")
        p.validate_ipa_invariant()
        assert p.ipa_text is None
        assert p.ipa_source is None
        assert p.ipa_confirmed is False

    def test_both_filled_valid_when_confirmed(self):
        """Both filled + ipa_confirmed=True is valid for recognized/g2p."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/ælɪs/", ipa_source="recognized", ipa_confirmed=True,
        )
        p.validate_ipa_invariant()
        assert p.ipa_text == "/ælɪs/"
        assert p.ipa_source == "recognized"

    def test_text_without_source_raises(self):
        """ipa_text set but ipa_source None should raise."""
        p = Participant(space_id=1, name="Alice", ipa_text="/x/", ipa_source=None)
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_source_without_text_raises(self):
        """ipa_source set but ipa_text None should raise."""
        p = Participant(space_id=1, name="Alice", ipa_text=None, ipa_source="g2p")
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_g2p_requires_confirmed(self):
        """ipa_source='g2p' with ipa_confirmed=False should raise."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="g2p", ipa_confirmed=False,
        )
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_recognized_requires_confirmed(self):
        """ipa_source='recognized' with ipa_confirmed=False should raise."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="recognized", ipa_confirmed=False,
        )
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_manual_with_confirmed_is_valid(self):
        """manual + confirmed is valid."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="manual", ipa_confirmed=True,
        )
        p.validate_ipa_invariant()
        assert p.ipa_source == "manual"

    def test_manual_without_confirmed_is_valid(self):
        """manual + unconfirmed is valid (human edit pending confirmation)."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="manual", ipa_confirmed=False,
        )
        p.validate_ipa_invariant()
        assert p.ipa_source == "manual"
        assert p.ipa_confirmed is False

    def test_non_manual_unconfirmed_raises(self):
        """If ipa_text is set and unconfirmed, source must be manual."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="recognized", ipa_confirmed=False,
        )
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_invite_token_auto_generated(self):
        """invite_token should be auto-generated and unique."""
        p1 = Participant(space_id=1, name="A")
        p2 = Participant(space_id=1, name="B")
        assert p1.invite_token != p2.invite_token
        assert len(p1.invite_token) > 10

    def test_default_status_is_invited(self):
        p = Participant(space_id=1, name="Alice")
        assert p.status == ParticipantStatus.invited

    def test_default_position_is_zero(self):
        p = Participant(space_id=1, name="Alice")
        assert p.position == 0


class TestStorageFilesystem:
    """Test the filesystem storage backend directly."""

    def test_save_and_load_roundtrip(self):
        from app.storage import storage
        data = b"fake audio bytes"
        key = storage.save(data, ext="wav")
        assert key.endswith(".wav")
        loaded = storage.load(key)
        assert loaded == data

    def test_url_format(self):
        from app.storage import storage
        key = "testfile.wav"
        url = storage.url(key)
        assert url == f"/media/{key}"

    def test_delete_removes_file(self):
        from app.storage import storage
        key = storage.save(b"data", ext="wav")
        storage.delete(key)
        from app.storage import storage as s
        import pytest
        with pytest.raises(FileNotFoundError):
            s.load(key)

    # TODO(security brief): real path-traversal coverage for FilesystemStorage.
    # The deleted test_path_traversal_prevention never constructed a traversal
    # string — it saved a clean UUID key and loaded it straight back, so it was
    # a duplicate of test_save_and_load_roundtrip wearing a security name.
    # Mutation probe: deleting the `safe = Path(key).name` sanitizer at
    # storage.py:45 left it passing. A real test calls
    # fs.load("../../etc/passwd") and asserts the escape fails.


class TestPronunciationFallbacks:
    """Test the g2p / recognize / TTS paths without the ML stack installed.

    Since the eSpeak floor was fixed, the g2p tests here exercise the real
    phonemizer backend rather than the placeholder, so they assert the
    contract (a non-empty str) and never the engine or the format.
    """

    def test_g2p_returns_non_empty_ipa_for_a_normal_name(self):
        from app.pronunciation.g2p import g2p
        result = g2p("Alice")
        assert len(result) > 0
        assert isinstance(result, str)

    def test_recognize_returns_none_when_unavailable(self, caplog):
        """No recognizer means no IPA -- not an invented one.

        This replaces test_recognize_fallback_returns_non_empty, whose whole
        premise was the `rec###` placeholder. That value was stored as
        ipa_source="recognized" and, once the TTS guard existed, guaranteed
        the participant no clip at all.

        Live path here rather than a stub: `transformers` genuinely is not
        installed in this venv.
        """
        import logging as _logging
        from app.pronunciation.recognize import recognize
        caplog.set_level(_logging.WARNING)

        assert recognize(b"fake wav bytes 12345") is None

        msgs = [r.getMessage() for r in caplog.records
                if r.name == "app.pronunciation.recognize"]
        assert any("unavailable" in m for m in msgs), msgs
        # The exception type is named, so "not installed" is distinguishable
        # from "the weights would not load".
        assert any("ModuleNotFoundError" in m for m in msgs), msgs

    def test_recognizer_unavailability_is_reported_once(self, monkeypatch, caplog):
        """Recognition runs once per upload, so a roster of recordings would
        otherwise log the same line once each."""
        import logging as _logging
        from app.pronunciation import recognize as rec
        caplog.set_level(_logging.WARNING)
        monkeypatch.setattr(rec, "_warned_unavailable", False)

        for _ in range(3):
            assert rec.recognize(b"x" * 1024) is None

        msgs = [r.getMessage() for r in caplog.records
                if r.name == "app.pronunciation.recognize"]
        assert len(msgs) == 1, msgs

    def test_recognize_reports_an_exception_distinctly(self, monkeypatch, caplog):
        """An exception inside inference must read differently from the
        recognizer being absent. The silent `except Exception: pass` this
        replaces made the two indistinguishable.

        Asserts the *shape* of the report, not which exception: pinning the
        type would make the test depend on which package the venv happens to
        be missing, which is the trap CLAUDE.md's don't-depend-on-an-absent-
        dependency Gotcha records.
        """
        import logging as _logging
        from app.pronunciation import recognize as rec
        caplog.set_level(_logging.WARNING)

        # Pretend the model loaded, so the unavailable branch is skipped and
        # the inference block runs and throws.
        monkeypatch.setattr(rec, "_processor", object())
        monkeypatch.setattr(rec, "_model", object())

        assert rec.recognize(b"x" * 1024) is None
        msgs = [r.getMessage() for r in caplog.records
                if r.name == "app.pronunciation.recognize"]
        assert any("recognition failed" in m for m in msgs), msgs
        assert not any("unavailable" in m for m in msgs), msgs

    def test_recognize_self_heals_when_only_the_processor_is_missing(
        self, monkeypatch
    ):
        """The asymmetry: the trigger used to test `_model is None` while the
        use-site required both, so a half-load never retried and every
        recording silently fell through."""
        from app.pronunciation import recognize as rec

        monkeypatch.setattr(rec, "_processor", None)
        monkeypatch.setattr(rec, "_model", object())   # half-loaded

        warmed = []
        monkeypatch.setattr(rec, "warm", lambda: warmed.append(True))

        rec.recognize(b"x" * 1024)
        assert warmed == [True], "a half-load must still retry warm()"

    def test_synthesize_raises_when_pipeline_unavailable(self, monkeypatch):
        """With no Kokoro pipeline, synthesize must raise — no silent tone fallback.

        Forces the unavailable condition rather than assuming it. Nulling
        _pipeline alone is not enough: synthesize() lazily re-warms at
        tts.py:70, so warm() is neutered too. Without this the test asserts
        only that kokoro happens to be uninstalled, and fails the moment it is.
        """
        from app.pronunciation import tts
        monkeypatch.setattr(tts, "_pipeline", None)
        monkeypatch.setattr(tts, "warm", lambda: None)
        with pytest.raises(RuntimeError, match="pipeline not loaded"):
            tts.synthesize("Alice", ipa="/ælɪs/")

    def test_orchestrator_g2p_name(self):
        from app.pronunciation.orchestrator import g2p_name
        result = g2p_name("Bob")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_orchestrator_render_clip_raises_when_pipeline_unavailable(self, monkeypatch):
        """Same contract one layer up: render_clip must not swallow the failure.

        What makes the patch visible is that synthesize() reads its own module
        globals at call time (tts.py:68-71) — not how orchestrator resolves
        synthesize. Patching tts._pipeline/tts.warm would be seen even through a
        from-import binding.
        """
        from app.pronunciation import orchestrator, tts
        monkeypatch.setattr(tts, "_pipeline", None)
        monkeypatch.setattr(tts, "warm", lambda: None)
        with pytest.raises(RuntimeError, match="pipeline not loaded"):
            orchestrator.render_clip("Bob", ipa_text="/bɒb/")

    def test_normalise_for_kokoro_keeps_what_kokoro_accepts(self):
        """`ç` and the nasal tilde are both in Kokoro's vocabulary, so the
        fold must leave them alone. This is the defect the sanitizer used to
        have, relocated here and corrected."""
        from app.pronunciation.tts import _normalise_for_kokoro
        assert _normalise_for_kokoro("ɪç") == "ɪç"
        assert _normalise_for_kokoro("ɐ̃ʊ̃") == "ɐ̃ʊ̃"
        assert _normalise_for_kokoro("bˈɑːb") == "bˈɑːb"
        for ch in "æøœ":
            assert _normalise_for_kokoro(ch) == ch

    def test_normalise_for_kokoro_folds_what_it_does_not(self):
        """An accented Latin letter is absent from the vocabulary, and Kokoro
        drops unknown phonemes silently -- so folding to the base keeps the
        sound instead of deleting it from the clip."""
        from app.pronunciation.tts import _normalise_for_kokoro
        assert _normalise_for_kokoro("álvaro") == "alvaro"
        assert _normalise_for_kokoro("ã") == "a\u0303"       # precomposed -> base + nasal
        assert _normalise_for_kokoro("t͡ʃ") == "tʃ"            # tie bar is not in the vocabulary

    def test_synthesize_logs_when_ipa_is_folded(self, monkeypatch, caplog):
        """A fold is a silent degradation unless it says so."""
        import logging as _logging
        from app.pronunciation import tts
        caplog.set_level(_logging.WARNING)
        # Non-None pipeline skips the warm/raise; stub the Kokoro call itself.
        monkeypatch.setattr(tts, "_pipeline", object())
        monkeypatch.setattr(tts, "_kokoro_wav", lambda source: b"RIFF")

        tts.synthesize("Álvaro", ipa="álvaro")
        msgs = [r.getMessage() for r in caplog.records
                if r.name == "app.pronunciation.tts"]
        assert any("folded to Kokoro" in m and "alvaro" in m for m in msgs), msgs

        caplog.clear()
        tts.synthesize("Bob", ipa="bˈɑːb")
        assert not [r for r in caplog.records
                    if r.name == "app.pronunciation.tts"]

    def test_garbage_guard_refuses_each_failure_mode(self):
        """Each rule names its own cause -- the three need different fixes."""
        from app.pronunciation.tts import _reject_reason
        cases = [
            # The live one: recognize._fallback reaches the renderer today via
            # the ceremony batch render, with no client involvement at all.
            ("Alice Lee", "rec044", "digit"),
            ("Nguyen", "ˈɛn dʒˈiː jˈuː wˈaɪ", "letter names"),
            ("Li Lei", "tʃˈaɪniːzlˌɛɾɚ", "letter"),
        ]
        for name, ipa, fragment in cases:
            reason = _reject_reason(name, ipa)
            assert reason is not None, f"{name!r}/{ipa!r} was not refused"
            assert fragment in reason, reason

    def test_garbage_guard_passes_real_pronunciations(self):
        """A guard that rejects real names is worse than no guard.

        Initials are the trap: letter-name IPA is *correct* for them. And
        "Letterman" begins with the same phonemes as eSpeak's word "letter",
        which is why the marker check is token-final.
        """
        from app.pronunciation.tts import _reject_reason
        clean = [
            ("Bob", "bˈɑːb"),
            ("Siobhan", "ʃɪvˈɔːn"),
            ("Hannah Wolfson", "hˈænə wˈʊlfsən"),
            ("Letterman", "lˈɛɾɚmən"),
            ("J R R Tolkien", "dʒˈeɪ ˈɑːɹ ˈɑːɹ tolkˈiːn"),
            ("T S Eliot", "tˈiː ˈɛs ˈɛlɪət"),
            ("W E B Du Bois", "dˈʌbəljˌuː ˈiː bˈiː dˈuː bˈɔɪz"),
        ]
        refused = [(n, i, _reject_reason(n, i)) for n, i in clean
                   if _reject_reason(n, i) is not None]
        assert not refused, refused

    def test_refused_ipa_still_produces_a_clip_from_the_name(
        self, monkeypatch, caplog
    ):
        """Refusing the override must not fail the render.

        ceremony.py's future.result() is unguarded, so raising here would 500
        the whole roster over one odd name. Kokoro gets the plain name.
        """
        import logging as _logging
        from app.pronunciation import tts
        caplog.set_level(_logging.WARNING)
        monkeypatch.setattr(tts, "_pipeline", object())

        seen = []

        def _fake_wav(source):
            seen.append(source)
            return b"RIFF"

        monkeypatch.setattr(tts, "_kokoro_wav", _fake_wav)

        assert tts.synthesize("Alice Lee", ipa="rec044") == b"RIFF"
        # The plain name, never the [name](/ipa/) override form.
        assert seen == ["Alice Lee"], seen
        msgs = [r.getMessage() for r in caplog.records
                if r.name == "app.pronunciation.tts"]
        assert any("IPA refused" in m and "rec044" in m for m in msgs), msgs

    @pytest.mark.skipif(
        not PHONEMIZER_AVAILABLE, reason="phonemizer not installed"
    )
    def test_garbage_guard_constants_match_espeak(self):
        """The guard's constants are a snapshot of one espeak-ng build, and
        test_gemma_g2p.py's expected IPA is the baseline Brief 04 measures
        against -- so this records the engine, not just the strings.

        Asserts the *version*, so a `brew upgrade espeak-ng` fails here as
        "the engine moved" rather than as a mysterious IPA mismatch someone
        fixes by editing one expected string at a time. Reports the library
        *path* without asserting it: this machine has two espeak-ng 1.52.0
        installs (brew's and the one espeakng-loader bundles) that produce
        identical IPA, and the Docker image apt-installs it elsewhere again.
        """
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        from app.pronunciation import g2p as g2p_module
        from app.pronunciation.tts import _LETTER_NAMES, _NARRATION_MARKERS

        library = EspeakWrapper.library()
        version = EspeakWrapper().version
        assert version == (1, 52, 0), (
            f"espeak-ng moved to {version} (library={library}). The guard "
            "constants and the expected IPA in test_gemma_g2p.py were all "
            "recorded under 1.52.0 -- re-derive them together rather than "
            "editing expected strings one by one."
        )

        # Reuse the app's own warmed backend rather than building another:
        # constructing an EspeakBackend costs ~2.3 s.
        g2p_module.warm()
        backend = g2p_module._backends[g2p_module.DEFAULT_LANGUAGE]
        assert backend is not None, f"eSpeak unavailable (library={library})"

        derived = {
            backend.phonemize([c.upper()], strip=True)[0].strip()
            for c in "abcdefghijklmnopqrstuvwxyz"
        }
        assert derived == set(_LETTER_NAMES), {
            "espeak has": sorted(derived - set(_LETTER_NAMES)),
            "constant has stale": sorted(set(_LETTER_NAMES) - derived),
            "library": library,
        }

        marker = backend.phonemize(["letter"], strip=True)[0].strip()
        assert marker in _NARRATION_MARKERS, (
            marker, sorted(_NARRATION_MARKERS), library,
        )
        assert marker.replace("ˈ", "ˌ") in _NARRATION_MARKERS

    @pytest.mark.skipif(
        not HF_HUB_AVAILABLE, reason="huggingface_hub not installed"
    )
    def test_kokoro_constants_match_published_vocabulary(self):
        """The vocabulary constants are hand-derived from a config fetched at
        runtime, so they can drift silently -- and in the direction worse than
        the bug they fix. If `ç` ever left the vocabulary, keeping it
        undecomposed would make Kokoro drop the consonant outright, where the
        old code at least produced `c`.

        Unmarked on purpose: this needs only huggingface_hub, not kokoro or
        torch, so it runs on a machine where the @integration tests cannot.
        """
        import json
        from app.pronunciation.gemma import _GRAPHEME_MAP
        from app.pronunciation.tts import (
            _KOKORO_COMBINING,
            _KOKORO_PRECOMPOSED,
        )

        try:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download("hexgrad/Kokoro-82M", "config.json")
            with open(path, encoding="utf-8") as fh:
                vocab = set(json.load(fh)["vocab"])
        except Exception as e:  # offline, rate-limited, repo moved
            pytest.skip(f"Kokoro config unreachable ({type(e).__name__}: {e})")

        assert _KOKORO_PRECOMPOSED <= vocab, sorted(_KOKORO_PRECOMPOSED - vocab)
        assert _KOKORO_COMBINING <= vocab, sorted(_KOKORO_COMBINING - vocab)
        targets = set("".join(_GRAPHEME_MAP.values()))
        assert targets <= vocab, sorted(targets - vocab)

    def test_orchestrator_recognize_recording(self):
        """The façade passes the None through rather than substituting."""
        from app.pronunciation.orchestrator import recognize_recording
        assert recognize_recording(b"fake audio") is None

    @pytest.mark.integration
    @pytest.mark.skipif(not KOKORO_AVAILABLE, reason="kokoro not installed")
    def test_warm_all_does_not_raise(self):
        """warm_all should not raise even if ML deps aren't installed.

        Marked integration: warm_all() constructs a real KPipeline and a real
        wav2vec2 model, downloading weights. Unmarked it was the one test that
        made the push gate pull ~1.6 GB despite deselecting the other 11.
        """
        from app.pronunciation.orchestrator import warm_all
        warm_all()  # should complete without error

    def test_lifespan_warms_all_four_stages(self, monkeypatch):
        """The lifespan must call warm_all() — the only automated proof.

        Unmarked on purpose: it stubs every stage's warm() so nothing heavy
        loads, which lets it run on a kokoro-less machine. Its sibling
        test_warm_all_does_not_raise is @integration and so runs nowhere.

        Builds its own TestClient rather than taking the `client` fixture,
        because that fixture neuters warm_all to keep the suite fast.
        """
        from fastapi.testclient import TestClient
        from app.main import app
        from app.pronunciation import g2p, recognize, transcode, tts

        called = []
        # g2p.warm takes a language now, hence *a/**k.
        monkeypatch.setattr(g2p, "warm", lambda *a, **k: called.append("g2p"))
        monkeypatch.setattr(recognize, "warm", lambda: called.append("recognize"))
        monkeypatch.setattr(tts, "warm", lambda: called.append("tts"))
        monkeypatch.setattr(transcode, "warm", lambda: called.append("transcode"))

        with TestClient(app):
            pass

        assert sorted(called) == ["g2p", "recognize", "transcode", "tts"]


class TestMediaEndpoint:
    """Test the /media/{key} dev endpoint."""

    def test_serves_existing_file(self, client):
        from app.storage import storage
        data = b"fake audio for media test"
        key = storage.save(data, ext="wav")
        resp = client.get(f"/media/{key}")
        assert resp.status_code == 200
        assert resp.content == data
        assert resp.headers["content-type"] == "audio/wav"

    def test_404_for_nonexistent_file(self, client):
        resp = client.get("/media/nonexistent.wav")
        assert resp.status_code == 404

    def test_media_serves_without_auth(self, client):
        """Media endpoint should be accessible without login."""
        from app.storage import storage
        key = storage.save(b"data", ext="wav")
        resp = client.get(f"/media/{key}")
        assert resp.status_code == 200