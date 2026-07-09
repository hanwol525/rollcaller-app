<script lang="ts">
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let showCreate = $state(false);
	let newName = $state('');
	let newAdvanced = $state(5);
</script>

<svelte:head><title>Events — RollCaller</title></svelte:head>

<div class="header">
	<h1>Events</h1>
	<button onclick={() => (showCreate = !showCreate)} class="btn-primary">
		{showCreate ? 'Cancel' : 'Create Event'}
	</button>
</div>

{#if showCreate}
	<form method="POST" action="?/create" class="create-form">
		{#if form?.error}
			<p class="error">{form.error}</p>
		{/if}
		<label>
			Event Name
			<input name="name" type="text" bind:value={newName} required placeholder="Spring Commencement 2026" />
		</label>
		<label>
			Seconds between names (default 5)
			<input name="advanced_seconds" type="number" min="0" bind:value={newAdvanced} />
		</label>
		<button type="submit" class="btn-primary">Create</button>
	</form>
{/if}

{#if data.spaces.length === 0 && !showCreate}
	<div class="empty">
		<p>Create your first event to get started.</p>
	</div>
{:else}
	<div class="space-list">
		{#each data.spaces as space (space.id)}
			<a href={`/spaces/${space.id}`} class="space-card">
				<h2>{space.name}</h2>
				<span class="meta">Pacing: {space.advanced_seconds === 0 ? 'Manual' : `${space.advanced_seconds}s`}</span>
			</a>
		{/each}
	</div>
{/if}

<style>
	.header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 1.5rem;
	}

	h1 {
		font-size: 1.5rem;
		color: #33322f;
		margin: 0;
	}

	.btn-primary {
		padding: 0.5rem 1.2rem;
		background: #33322f;
		color: white;
		border: none;
		border-radius: 8px;
		cursor: pointer;
		font-size: 0.9rem;
	}

	.btn-primary:hover {
		background: #444;
	}

	.create-form {
		background: white;
		border-radius: 12px;
		padding: 1.5rem;
		margin-bottom: 1.5rem;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
	}

	.error {
		color: #c33;
		font-size: 0.85rem;
		margin-bottom: 0.5rem;
	}

	label {
		display: block;
		margin-bottom: 0.75rem;
		font-size: 0.85rem;
		color: #555;
	}

	input {
		display: block;
		width: 100%;
		padding: 0.5rem 0.7rem;
		border: 1px solid #cfccc4;
		border-radius: 8px;
		font-size: 1rem;
		margin-top: 0.25rem;
		box-sizing: border-box;
	}

	.empty {
		text-align: center;
		padding: 3rem;
		color: #999;
	}

	.space-list {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.space-card {
		background: white;
		border-radius: 12px;
		padding: 1.2rem 1.5rem;
		text-decoration: none;
		color: inherit;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.space-card:hover {
		box-shadow: 0 2px 12px rgba(0, 0, 0, 0.12);
	}

	.space-card h2 {
		font-size: 1.1rem;
		margin: 0;
		color: #33322f;
	}

	.meta {
		font-size: 0.8rem;
		color: #999;
	}
</style>