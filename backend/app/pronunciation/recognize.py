"""Audio-to-IPA recognition using a wav2vec2 phoneme model.

Loaded lazily. When no IPA can be derived, ``recognize()`` returns ``None``
rather than inventing one. It used to return a placeholder (`rec` plus the
audio length), which was stored as ``ipa_source="recognized"`` and reached
Kokoro as a pronunciation override — and once the TTS garbage guard existed,
guaranteed that participant no clip at all.

Which of the three reasons produced the ``None`` goes to the log, because they
need different fixes: the recognizer not being installed is an environment
problem, an exception is a code or audio problem, and empty output is a
recording problem.
"""
from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

# Turns audio directly into IPA phonemes (English, espeak-style IPA).
_MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"

_processor = None  # type: ignore[var-annotated]
_model = None      # type: ignore[var-annotated]

# Set once the recognizer has been reported unavailable. That is a
# process-lifetime fact, so reporting it per upload would be one copy of the
# same line per recording.
_warned_unavailable = False


def warm() -> None:
    """Pre-load the wav2vec2 phoneme model + processor once at app startup."""
    global _processor, _model, _warned_unavailable
    try:
        from transformers import AutoProcessor, AutoModelForCTC
        _processor = AutoProcessor.from_pretrained(_MODEL_ID)
        _model = AutoModelForCTC.from_pretrained(_MODEL_ID)
    except Exception as e:
        _processor = None
        _model = None
        if not _warned_unavailable:
            _warned_unavailable = True
            # Deduped: the self-heal calls warm() on every upload, so without
            # this a roster of recordings logs the same line once each.
            log.warning(
                "wav2vec2 recognizer unavailable (%s: %s) — recordings are "
                "stored but yield no IPA, so the ceremony falls back to g2p "
                "on the name", type(e).__name__, e,
            )


def recognize(wav_bytes: bytes) -> str | None:
    """Recognize IPA from a WAV blob, or ``None`` when none could be derived.

    ``None`` is not an error for the caller to reject: the recording is still
    real audio worth keeping, and the ceremony renderer falls through to g2p.
    """
    global _processor, _model

    # Trigger on *either* being unset. The use-site below needs both, so
    # keying the self-heal on _model alone meant a half-load never retried.
    if _processor is None or _model is None:
        warm()                     # lazy-load the recognizer on first use
    if _processor is None or _model is None:
        # warm() has already reported why, once, and named the exception.
        return None

    try:
        import numpy as np
        import soundfile as sf
        import torch

        # The transcode step already emits 16 kHz mono WAV; read it and
        # guard against stereo just in case (a (N,2) array breaks the model).
        audio, _sr = sf.read(io.BytesIO(wav_bytes))
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        audio = np.asarray(audio, dtype="float32")

        inputs = _processor(
            audio, sampling_rate=16000, return_tensors="pt", padding=True
        )
        with torch.no_grad():
            logits = _model(inputs.input_values).logits
        phonemes = _processor.batch_decode(torch.argmax(logits, dim=-1))
        cleaned = _clean_phones(phonemes[0] if phonemes else "")
    except Exception as e:
        log.warning(
            "wav2vec2 recognition failed on a %d-byte recording (%s: %s)",
            len(wav_bytes), type(e).__name__, e,
        )
        return None

    if not cleaned:
        log.warning(
            "wav2vec2 returned no phonemes for a %d-byte recording",
            len(wav_bytes),
        )
        return None
    return cleaned


def _clean_phones(raw: str) -> str:
    """Collapse the model's space-separated phones into a continuous IPA
    string Kokoro's markup can read (`t ɑː m` -> `tɑːm`)."""
    return "".join(raw.split())
