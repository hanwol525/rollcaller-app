"""Audio-to-IPA recognition using a wav2vec2 phoneme model.

Loaded lazily; a fallback returns a pseudo-IPA when transformers/the model
aren't available so tests can exercise the pipeline without the ML stack.
"""
from __future__ import annotations

import io

# Turns audio directly into IPA phonemes (English, espeak-style IPA).
_MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"

_processor = None  # type: ignore[var-annotated]
_model = None      # type: ignore[var-annotated]


def warm() -> None:
    """Pre-load the wav2vec2 phoneme model + processor once at app startup."""
    global _processor, _model
    try:
        from transformers import AutoProcessor, AutoModelForCTC
        _processor = AutoProcessor.from_pretrained(_MODEL_ID)
        _model = AutoModelForCTC.from_pretrained(_MODEL_ID)
    except Exception:
        _processor = None
        _model = None


def recognize(wav_bytes: bytes) -> str:
    """Recognize IPA from a WAV audio bytes blob."""
    if _processor is not None and _model is not None:
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
            return _clean_phones(phonemes[0] if phonemes else "")
        except Exception:
            pass
    return _fallback(wav_bytes)


def _clean_phones(raw: str) -> str:
    """Collapse the model's space-separated phones into a continuous IPA
    string Kokoro's markup can read (`t ɑː m` -> `tɑːm`)."""
    return "".join(raw.split())


def _fallback(wav_bytes: bytes) -> str:
    """Placeholder IPA for dev/test without the model. Deterministic by length."""
    length = len(wav_bytes)
    return f"/rec{length % 1000:03d}/"