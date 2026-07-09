<script lang="ts">
import { onMount } from 'svelte';
import { getSelf, uploadRecording, previewIpa, confirmIpa } from '$lib/api';
import type { ParticipantSelf } from '$lib/api';
import { startRecording, stopRecording } from '$lib/recorder';

	let { params } = $props();
	let token = $derived(params.token);

	// State
	let self = $state<ParticipantSelf | null>(null);
	let loading = $state(true);
let error = $state('');

// Recording sub-state (within invited status)
let recording = $state(false);
let uploading = $state(false);
let recorder: MediaRecorder | null = null;
let micError = $state('');

// Review sub-state (within recorded status)
	let previewLoading = $state(false);
	let previewUrl = $state<string | null>(null);
	let confirming = $state(false);
let ipaText: string | null = null; // the hidden IPA baton

// Re-record override: when true, show the recorder regardless of status
let reRecording = $state(false);

// Determine current screen from status + local state
let screen = $derived(
error ? 'error' :
reRecording ? 'recording' :
!self ? 'loading' :
self.status === 'confirmed' ? 'confirmed' :
self.status === 'recorded' ? 'review' :
recording || uploading ? 'recording' :
'landing'
);

onMount(async () => {
await loadSelf();
});

async function loadSelf() {
loading = true;
error = '';
try {
self = await getSelf(token);
ipaText = self.ipa_text; // baton survives refresh
} catch (e) {
error = String(e).replace('Error: ', '');
if (error.includes('404') || error.includes('Not found')) {
error = 'This invite link is no longer valid.';
}
} finally {
loading = false;
}
}

async function handleRecord() {
micError = '';
try {
recorder = await startRecording();
recording = true;
} catch (e) {
micError = 'Could not access your microphone. Please allow microphone access and try again.';
}
}

async function handleStop() {
if (!recorder) return;
recording = false;
uploading = true;
try {
const blob = await stopRecording(recorder);
recorder = null;
self = await uploadRecording(token, blob);
ipaText = self.ipa_text;
if (previewUrl) { URL.revokeObjectURL(previewUrl); }
previewUrl = null; // reset cached preview
reRecording = false; // return to review with fresh take
} catch (e) {
error = String(e).replace('Error: ', '');
} finally {
uploading = false;
}
}

async function handleHearPreview() {
if (previewUrl) {
// Replay cached
const audio = document.querySelector('audio#preview') as HTMLAudioElement;
if (audio) { audio.currentTime = 0; audio.play(); }
return;
}
if (!ipaText) return;
previewLoading = true;
try {
const blob = await previewIpa(token, ipaText);
previewUrl = URL.createObjectURL(blob);
// Auto-play on first load
setTimeout(() => {
const audio = document.querySelector('audio#preview') as HTMLAudioElement;
if (audio) audio.play();
}, 100);
} catch (e) {
error = 'Could not generate preview. Please try again.';
} finally {
previewLoading = false;
}
}

async function handleConfirm() {
if (!ipaText) return;
confirming = true;
try {
self = await confirmIpa(token, ipaText, false);
} catch (e) {
error = String(e).replace('Error: ', '');
} finally {
confirming = false;
}
}

function handleReRecord() {
if (previewUrl) { URL.revokeObjectURL(previewUrl); }
previewUrl = null;
reRecording = true;
micError = '';
}
</script>

<svelte:head><title>Record Your Name — RollCaller</title></svelte:head>

<div class="record-container">
{#if screen === 'loading'}
<p class="loading-text">Loading…</p>

{:else if screen === 'error'}
<div class="card">
<p class="error-msg">{error}</p>
</div>

{:else if screen === 'confirmed'}
<div class="card confirmed">
<div class="check">✓</div>
<h1>You're all set for {self?.space_name}!</h1>
<p class="subtitle">Your name pronunciation has been confirmed.</p>
<button onclick={handleReRecord} class="btn-secondary">
	Changed your mind? Re-record
</button>
</div>

{:else if screen === 'review'}
<div class="card review">
<p class="event-name">{self?.space_name}</p>
<h1>Review your name</h1>
<p class="subtitle">Hi {self?.name}, listen to how your name will be pronounced at the ceremony.</p>

{#if micError}
<p class="error-msg">{micError}</p>
{/if}

<div class="preview-section">
<button onclick={handleHearPreview} disabled={previewLoading} class="btn-primary">
{previewLoading ? 'Loading…' : '♫ Hear your name'}
</button>
{#if previewUrl}
				<audio id="preview" src={previewUrl} controls></audio>
{/if}
</div>

<p class="hint">This is the AI ceremony voice rendering your pronunciation — not your own recording.</p>

<div class="actions">
<button onclick={handleConfirm} disabled={confirming} class="btn-primary">
{confirming ? 'Confirming…' : 'Confirm'}
</button>
<button onclick={handleReRecord} class="btn-secondary">
Re-record
</button>
</div>
</div>

{:else if screen === 'recording'}
<div class="card recording">
<p class="event-name">{self?.space_name}</p>
{#if recording}
<div class="recording-indicator">
<div class="pulse"></div>
<span>Recording…</span>
</div>
<p class="recording-name">Say your name clearly</p>
<button onclick={handleStop} class="btn-stop">Stop</button>
{:else if uploading}
<p class="loading-text">Uploading & processing…</p>
{/if}
</div>

{:else if screen === 'landing'}
<div class="card landing">
<p class="event-name">{self?.space_name}</p>
<h1>Record your name</h1>
<p class="prompt">
Please record yourself clearly pronouncing your full name exactly how you would like it pronounced aloud at {self?.space_name}.
</p>
{#if micError}
<p class="error-msg">{micError}</p>
{/if}
<button onclick={handleRecord} class="btn-record">
<span class="dot"></span>
Start Recording
</button>
</div>
{/if}
</div>

<style>
.record-container {
display: flex;
align-items: center;
justify-content: center;
min-height: 100vh;
background: #eceae5;
font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
padding: 1rem;
}

.card {
background: white;
border-radius: 14px;
padding: 2rem;
width: 100%;
max-width: 380px;
text-align: center;
box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.event-name {
font-size: 0.8rem;
color: #999;
text-transform: uppercase;
letter-spacing: 0.05em;
margin: 0 0 0.5rem;
}

h1 {
font-size: 1.4rem;
color: #33322f;
margin: 0 0 0.75rem;
}

.subtitle {
color: #777;
font-size: 0.9rem;
margin: 0 0 1.5rem;
}

.prompt {
color: #555;
font-size: 0.95rem;
line-height: 1.5;
margin: 0 0 1.5rem;
}

.btn-record {
display: flex;
align-items: center;
justify-content: center;
gap: 0.5rem;
width: 100%;
padding: 0.8rem;
background: #33322f;
color: white;
border: none;
border-radius: 8px;
font-size: 1rem;
cursor: pointer;
}

.btn-record:hover {
background: #444;
}

.dot {
width: 10px;
height: 10px;
border-radius: 50%;
background: #e44;
}

.recording-indicator {
display: flex;
align-items: center;
justify-content: center;
gap: 0.5rem;
margin: 1rem 0;
color: #e44;
font-size: 0.9rem;
}

.pulse {
width: 12px;
height: 12px;
border-radius: 50%;
background: #e44;
animation: pulse 1s infinite;
}

@keyframes pulse {
0%, 100% { opacity: 1; }
50% { opacity: 0.3; }
}

.recording-name {
font-size: 1.1rem;
color: #33322f;
margin: 1rem 0;
}

.btn-stop {
padding: 0.7rem 2rem;
background: #e44;
color: white;
border: none;
border-radius: 8px;
font-size: 1rem;
cursor: pointer;
}

.btn-stop:hover {
background: #c33;
}

.btn-primary {
padding: 0.7rem 1.5rem;
background: #33322f;
color: white;
border: none;
border-radius: 8px;
font-size: 1rem;
cursor: pointer;
}

.btn-primary:hover {
background: #444;
}

.btn-primary:disabled {
opacity: 0.5;
cursor: not-allowed;
}

.btn-secondary {
padding: 0.7rem 1.5rem;
background: transparent;
color: #555;
border: 1px solid #cfccc4;
border-radius: 8px;
font-size: 1rem;
cursor: pointer;
}

.btn-secondary:hover {
background: #f5f5f5;
}

.preview-section {
margin: 1.5rem 0;
display: flex;
flex-direction: column;
align-items: center;
gap: 0.75rem;
}

.preview-section audio {
width: 100%;
}

.hint {
font-size: 0.8rem;
color: #999;
margin: 0.5rem 0 1.5rem;
}

.actions {
display: flex;
flex-direction: column;
gap: 0.5rem;
}

.confirmed .check {
font-size: 3rem;
color: #4a4;
margin-bottom: 0.5rem;
}

.loading-text {
color: #999;
font-size: 1rem;
}

.error-msg {
background: #fee;
color: #c33;
padding: 0.8rem;
border-radius: 8px;
font-size: 0.9rem;
margin: 0;
}
</style>