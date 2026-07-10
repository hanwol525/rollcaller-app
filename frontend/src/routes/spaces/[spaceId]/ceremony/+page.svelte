<script lang="ts">
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const ceremony = $derived(data.ceremony);
	const roster = $derived(ceremony.roster);
	const secondsBetweenNames = $derived(ceremony.advanced_seconds);

	let currentIndex = $state(0);
	let isPlaying = $state(false);
	let audioEl: HTMLAudioElement | null = $state(null);
	let advanceTimer: ReturnType<typeof setTimeout> | null = null;
	let srcInit = false;

	const currentItem = $derived(roster[currentIndex] ?? null);
	const isFinished = $derived(currentIndex >= roster.length);
	const notPrepped = $derived(roster.length > 0 && roster.every((r) => r.clip_url === null));

	// Initialize the audio element's src once when the roster loads.
	// After that, goTo() owns src — this effect must not fire again.
	$effect(() => {
		if (!srcInit && audioEl && roster.length > 0) {
			const item = roster[currentIndex] ?? roster[0];
			if (item?.clip_url) audioEl.src = item.clip_url;
			srcInit = true;
		}
	});

	function cancelAdvance() {
		if (advanceTimer) {
			clearTimeout(advanceTimer);
			advanceTimer = null;
		}
	}

	function armAdvance() {
		cancelAdvance();
		if (secondsBetweenNames > 0 && currentIndex < roster.length - 1) {
			advanceTimer = setTimeout(() => {
				goTo(currentIndex + 1);
			}, secondsBetweenNames * 1000);
		}
	}

	function handleEnded() {
		// Clip finished. Do NOT advance immediately.
		if (currentIndex >= roster.length - 1) {
			currentIndex = roster.length; // last item → ceremony complete
			return;
		}
		if (secondsBetweenNames > 0) {
			armAdvance(); // auto-advance ON: wait advanced_seconds, THEN goTo(currentIndex + 1)
		}
		// auto-advance OFF (0): stay on this name; user advances manually
	}

	function goTo(index: number) {
		// Past the end → ceremony complete
		if (index >= roster.length) {
			cancelAdvance();
			if (audioEl) audioEl.pause();
			currentIndex = roster.length;
			return;
		}

		const clamped = Math.max(0, Math.min(index, roster.length - 1));
		const item = roster[clamped];

		// 1. Cancel any pending auto-advance timer
		cancelAdvance();

		// 2. Set src (only if clip is changing) — pause first so onpause fires
		if (clamped !== currentIndex && audioEl) {
			audioEl.pause();
			if (item.clip_url) {
				audioEl.src = item.clip_url;
			} else {
				audioEl.removeAttribute('src');
			}
		}

		// 3. Play (if there's a clip)
		if (audioEl && item.clip_url) {
			audioEl.play().catch((e) => console.warn(e));
		}

		// 4. Update index
		currentIndex = clamped;

		// No clip → onplay won't fire, so arm advance directly
		if (!item.clip_url && secondsBetweenNames > 0 && clamped < roster.length - 1) {
			armAdvance();
		}
	}

	function togglePlay() {
		if (!audioEl) return;
		if (audioEl.paused) {
			audioEl.play().catch((e) => console.warn(e));
		} else {
			audioEl.pause();
		}
	}

	function next() {
		if (currentIndex < roster.length - 1) {
			goTo(currentIndex + 1);
		}
	}

	function prev() {
		if (currentIndex > 0) {
			goTo(currentIndex - 1);
		}
	}

	function restart() {
		goTo(0);
	}
</script>

<svelte:head><title>{ceremony.space_name} — Ceremony — RollCaller</title></svelte:head>

<div class="ceremony">
	<div class="header">
		<a href="/spaces/{ceremony.space_id}" class="back-link">← Back to Roster</a>
		<h1>{ceremony.space_name}</h1>
	</div>

	{#if notPrepped}
		<div class="gate">
			<p>Clips haven't been prepared yet.</p>
			<p>Go back to the roster and click <strong>Prep Clips</strong> to render audio for the ceremony.</p>
			<a href="/spaces/{ceremony.space_id}" class="btn-primary">Back to Roster</a>
		</div>
	{:else if isFinished}
		<div class="finished">
			<h2>Ceremony Complete</h2>
			<p>All {roster.length} names have been called.</p>
			<button onclick={restart} class="btn-primary">Start Over</button>
		</div>
	{:else if currentItem}
		<div class="player">
			<div class="position-indicator">
				{currentIndex + 1} of {roster.length}
			</div>

			<div class="current-name">
				{currentItem.name}
			</div>
			{#if currentItem.clip_url}
				<div class="controls-row">
					<button onclick={togglePlay} class="play-btn">
						{isPlaying ? 'Pause' : 'Play'}
					</button>
					<button onclick={prev} disabled={currentIndex === 0} class="nav-btn">← Prev</button>
					<button onclick={next} disabled={currentIndex === roster.length - 1} class="nav-btn">Next →</button>
				</div>
			{:else}
				<div class="no-clip">No clip available for this person.</div>
				<div class="controls-row">
					<button onclick={prev} disabled={currentIndex === 0} class="nav-btn">← Prev</button>
					<button onclick={next} disabled={currentIndex === roster.length - 1} class="nav-btn">Next →</button>
				</div>
			{/if}

		<!-- Persistent audio element — always in the DOM, events drive state -->
		<audio
			bind:this={audioEl}
			onplay={() => { isPlaying = true; }}
			onplaying={() => { isPlaying = true; }}
			onpause={() => { isPlaying = false; cancelAdvance(); }}
			onended={() => { isPlaying = false; handleEnded(); }}
			preload="auto"
		></audio>

			{#if ceremony.advanced_seconds > 0}
				<div class="auto-advance-note">
					Auto-advance: {ceremony.advanced_seconds}s after each clip
				</div>
			{:else}
				<div class="auto-advance-note">
					Manual mode — click Next to advance
				</div>
			{/if}
		</div>
	{:else}
		<div class="empty">
			<p>No participants in this event.</p>
		</div>
	{/if}

	{#if roster.length > 0 && !notPrepped && !isFinished}
		<div class="queue">
			<h3>Up Next</h3>
			<ol>
				{#each roster as item, i (i)}
					<li class:current={i === currentIndex} class:done={i < currentIndex}>
						<button onclick={() => goTo(i)} class="queue-item">
							<span class="queue-pos">{i + 1}</span>
							<span class="queue-name">{item.name}</span>
							{#if item.clip_url === null}
								<span class="queue-no-clip">no clip</span>
							{/if}
						</button>
					</li>
				{/each}
			</ol>
		</div>
	{/if}
</div>

<style>
	.ceremony {
		max-width: 700px;
		margin: 0 auto;
	}

	.header {
		margin-bottom: 1.5rem;
	}

	.back-link {
		font-size: 0.8rem;
		color: #999;
		text-decoration: none;
	}

	.back-link:hover {
		color: #555;
	}

	h1 {
		font-size: 1.5rem;
		color: #33322f;
		margin: 0.25rem 0 0;
	}

	.gate,
	.finished,
	.empty {
		text-align: center;
		background: white;
		border-radius: 12px;
		padding: 3rem 2rem;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
	}

	.gate p,
	.finished p,
	.empty p {
		color: #777;
		margin-bottom: 1rem;
	}

	.finished h2 {
		color: #33322f;
		margin-bottom: 0.5rem;
	}

	.player {
		background: white;
		border-radius: 12px;
		padding: 2.5rem 2rem;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
		text-align: center;
		margin-bottom: 1.5rem;
	}

	.position-indicator {
		font-size: 0.8rem;
		color: #999;
		margin-bottom: 1rem;
	}

	.current-name {
		font-size: 2.5rem;
		font-weight: 700;
		color: #33322f;
		margin-bottom: 0.5rem;
	}

	.controls-row {
		display: flex;
		justify-content: center;
		gap: 0.75rem;
		margin-top: 1.5rem;
	}

	.play-btn {
		padding: 0.7rem 2rem;
		background: #33322f;
		color: white;
		border: none;
		border-radius: 8px;
		cursor: pointer;
		font-size: 1rem;
	}

	.play-btn:hover {
		background: #444;
	}

	.nav-btn {
		padding: 0.7rem 1.2rem;
		background: transparent;
		border: 1px solid #cfccc4;
		border-radius: 8px;
		cursor: pointer;
		font-size: 0.9rem;
		color: #555;
	}

	.nav-btn:hover:not(:disabled) {
		background: #f5f5f5;
	}

	.nav-btn:disabled {
		opacity: 0.3;
		cursor: default;
	}

	.no-clip {
		color: #c33;
		font-size: 0.9rem;
		margin-top: 1rem;
	}

	.auto-advance-note {
		font-size: 0.8rem;
		color: #999;
		margin-top: 1rem;
	}

	.queue {
		background: white;
		border-radius: 12px;
		padding: 1.5rem;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
	}

	.queue h3 {
		font-size: 0.9rem;
		color: #999;
		margin: 0 0 0.75rem;
	}

	.queue ol {
		list-style: none;
		padding: 0;
		margin: 0;
		max-height: 300px;
		overflow-y: auto;
	}

	.queue li {
		margin-bottom: 0.25rem;
	}

	.queue-item {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		width: 100%;
		padding: 0.5rem 0.75rem;
		background: transparent;
		border: none;
		border-radius: 6px;
		cursor: pointer;
		font-size: 0.9rem;
		color: #33322f;
		text-align: left;
	}

	.queue-item:hover {
		background: #f5f5f5;
	}

	.queue li.current .queue-item {
		background: #e8e6e0;
		font-weight: 600;
	}

	.queue li.done .queue-item {
		opacity: 0.4;
	}

	.queue-pos {
		color: #999;
		width: 30px;
		flex-shrink: 0;
	}

	.queue-name {
		flex: 1;
	}

	.queue-no-clip {
		font-size: 0.75rem;
		color: #c33;
	}

	.btn-primary {
		padding: 0.5rem 1.2rem;
		background: #33322f;
		color: white;
		border: none;
		border-radius: 8px;
		cursor: pointer;
		font-size: 0.9rem;
		text-decoration: none;
		display: inline-block;
		margin-top: 0.5rem;
	}

	.btn-primary:hover {
		background: #444;
	}
</style>