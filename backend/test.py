from transformers import AutoProcessor, AutoModelForCTC
import torch, soundfile as sf
mid = "facebook/wav2vec2-lv-60-espeak-cv-ft"
proc = AutoProcessor.from_pretrained(mid)   # cached now — instant
model = AutoModelForCTC.from_pretrained(mid)
audio, sr = sf.read("/tmp/test16.wav")
print("shape:", audio.shape, "sr:", sr, "dur:", round(len(audio)/sr, 2))   # sanity check
inputs = proc(audio, sampling_rate=16000, return_tensors="pt", padding=True)
with torch.no_grad():
    logits = model(inputs.input_values).logits
print("PHONEMES:", proc.batch_decode(torch.argmax(logits, dim=-1)))