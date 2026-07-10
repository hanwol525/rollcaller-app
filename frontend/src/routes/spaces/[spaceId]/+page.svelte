<script lang="ts">
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let newName = $state('');
	let newEmail = $state('');
	let showAdd = $state(false);
	let showImport = $state(false);
	let pacingValue = $state(data.space.advanced_seconds);
	let copiedToken = $state('');

	const statusLabels: Record<string, string> = {
		invited: 'Invited',
		recorded: 'Recorded',
		confirmed: 'Confirmed'
	};

	function copyLink(token: string) {
		const url = `${window.location.origin}/record/${token}`;
		navigator.clipboard.writeText(url);
		copiedToken = token;
		setTimeout(() => (copiedToken = ''), 2000);
	}
</script>

<svelte:head><title>{data.space.name} — RollCaller</title></svelte:head>

<div class="header">
	<div>
		<a href="/spaces" class="back-link">← Events</a>
		<h1>{data.space.name}</h1>
	</div>
	<div class="header-actions">
		<a href="/spaces/{data.space.id}/ceremony" class="btn-secondary">Start Ceremony</a>
	</div>
</div>

<div class="controls">
	<form method="POST" action="?/render" class="render-form">
		<button type="submit" class="btn-primary">Prep Clips</button>
		{#if form?.action === 'render'}
			{#if form.rendered !== undefined}
				<span class="success">Prepared {form.rendered} clip(s).</span>
			{:else if form.error}
				<span class="error">{form.error}</span>
			{/if}
		{/if}
	</form>

	<form method="POST" action="?/pacing" class="pacing-form">
		<label>
			Seconds between names
			<input name="advanced_seconds" type="number" min="0" bind:value={pacingValue} />
		</label>
		<button type="submit" class="btn-small">Save</button>
	</form>
</div>

<div class="toolbar">
	<button onclick={() => { showAdd = !showAdd; showImport = false; }} class="btn-secondary">
		{showAdd ? 'Cancel' : 'Add Person'}
	</button>
	<button onclick={() => { showImport = !showImport; showAdd = false; }} class="btn-secondary">
		{showImport ? 'Cancel' : 'Import CSV'}
	</button>
</div>

{#if showAdd}
	<form method="POST" action="?/add" class="add-form">
		{#if form?.action === 'add' && form.error}
			<p class="error">{form.error}</p>
		{/if}
		<input name="name" type="text" bind:value={newName} required placeholder="Full name" />
		<input name="email" type="email" bind:value={newEmail} placeholder="Email (optional)" />
		<button type="submit" class="btn-primary">Add</button>
	</form>
{/if}

{#if showImport}
	<form method="POST" action="?/importCsv" enctype="multipart/form-data" class="import-form">
		{#if form?.action === 'import' && form.error}
			<p class="error">{form.error}</p>
		{/if}
		<input name="csv_file" type="file" accept=".csv" required />
		<button type="submit" class="btn-primary">Upload CSV</button>
	</form>
{/if}

{#if data.participants.length === 0}
	<div class="empty">
		<p>No participants yet. Add people individually or import a CSV.</p>
	</div>
{:else}
	<table class="roster">
		<thead>
			<tr>
				<th>#</th>
				<th>Name</th>
				<th>Email</th>
				<th>Status</th>
				<th>Invite Link</th>
				<th>Reorder</th>
				<th></th>
			</tr>
		</thead>
		<tbody>
			{#each data.participants as p, i (p.id)}
				<tr>
					<td class="pos">{i + 1}</td>
					<td>{p.name}</td>
					<td class="email">{p.email ?? '—'}</td>
					<td><span class="badge badge-{p.status}">{statusLabels[p.status]}</span></td>
					<td>
						<button onclick={() => copyLink(p.invite_token)} class="copy-btn">
							{copiedToken === p.invite_token ? 'Copied!' : 'Copy Link'}
						</button>
					</td>
					<td class="reorder">
						<form method="POST" action="?/reorder" class="inline-form">
							<input type="hidden" name="pid" value={p.id} />
							<input type="hidden" name="direction" value="up" />
							<input type="hidden" name="currentPos" value={p.position} />
							<button type="submit" class="arrow-btn" disabled={i === 0}>↑</button>
						</form>
						<form method="POST" action="?/reorder" class="inline-form">
							<input type="hidden" name="pid" value={p.id} />
							<input type="hidden" name="direction" value="down" />
							<input type="hidden" name="currentPos" value={p.position} />
							<button type="submit" class="arrow-btn" disabled={i === data.participants.length - 1}>↓</button>
						</form>
					</td>
					<td>
					<form
						method="POST"
						action="?/delete"
						class="inline-form"
						onsubmit={(e) => {
							if (!confirm(`Delete ${p.name}?`)) e.preventDefault();
						}}
					>
						<input type="hidden" name="pid" value={p.id} />
						<button type="submit" class="delete-btn">Delete</button>
					</form>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	.header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
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

	.header-actions {
		display: flex;
		gap: 0.5rem;
	}

	.controls {
		display: flex;
		align-items: center;
		gap: 1.5rem;
		background: white;
		border-radius: 12px;
		padding: 1rem 1.5rem;
		margin-bottom: 1rem;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
	}

	.render-form {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}

	.pacing-form {
		display: flex;
		align-items: flex-end;
		gap: 0.5rem;
		margin-left: auto;
	}

	.pacing-form label {
		font-size: 0.8rem;
		color: #555;
	}

	.pacing-form input {
		width: 70px;
		padding: 0.35rem 0.5rem;
		border: 1px solid #cfccc4;
		border-radius: 6px;
		font-size: 0.9rem;
		margin-left: 0.5rem;
	}

	.toolbar {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}

	.add-form,
	.import-form {
		background: white;
		border-radius: 12px;
		padding: 1.25rem;
		margin-bottom: 1rem;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
		display: flex;
		gap: 0.75rem;
		align-items: center;
	}

	.add-form input {
		flex: 1;
		padding: 0.5rem 0.7rem;
		border: 1px solid #cfccc4;
		border-radius: 8px;
		font-size: 0.95rem;
	}

	.import-form input[type='file'] {
		flex: 1;
		font-size: 0.9rem;
	}

	.btn-primary {
		padding: 0.5rem 1.2rem;
		background: #33322f;
		color: white;
		border: none;
		border-radius: 8px;
		cursor: pointer;
		font-size: 0.9rem;
		white-space: nowrap;
	}

	.btn-primary:hover {
		background: #444;
	}

	.btn-secondary {
		padding: 0.5rem 1.2rem;
		background: white;
		color: #33322f;
		border: 1px solid #cfccc4;
		border-radius: 8px;
		cursor: pointer;
		font-size: 0.9rem;
		text-decoration: none;
		white-space: nowrap;
	}

	.btn-secondary:hover {
		background: #f5f5f5;
	}

	.btn-small {
		padding: 0.35rem 0.8rem;
		background: #33322f;
		color: white;
		border: none;
		border-radius: 6px;
		cursor: pointer;
		font-size: 0.85rem;
	}

	.btn-small:hover {
		background: #444;
	}

	.success {
		color: #2a7a2a;
		font-size: 0.85rem;
	}

	.error {
		color: #c33;
		font-size: 0.85rem;
	}

	.empty {
		text-align: center;
		padding: 3rem;
		color: #999;
	}

	.roster {
		width: 100%;
		border-collapse: collapse;
		background: white;
		border-radius: 12px;
		overflow: hidden;
		box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
	}

	.roster th {
		text-align: left;
		padding: 0.75rem 1rem;
		font-size: 0.8rem;
		color: #999;
		border-bottom: 1px solid #eee;
		font-weight: 600;
	}

	.roster td {
		padding: 0.75rem 1rem;
		border-bottom: 1px solid #f0f0f0;
		font-size: 0.9rem;
		color: #33322f;
	}

	.roster tr:last-child td {
		border-bottom: none;
	}

	.pos {
		color: #999;
		width: 30px;
	}

	.email {
		color: #777;
	}

	.badge {
		display: inline-block;
		padding: 0.2rem 0.6rem;
		border-radius: 12px;
		font-size: 0.75rem;
		font-weight: 600;
	}

	.badge-invited {
		background: #eee;
		color: #777;
	}

	.badge-recorded {
		background: #d4e5f7;
		color: #2a5d8a;
	}

	.badge-confirmed {
		background: #d4f0d4;
		color: #2a7a2a;
	}

	.copy-btn {
		padding: 0.3rem 0.7rem;
		background: transparent;
		border: 1px solid #cfccc4;
		border-radius: 6px;
		cursor: pointer;
		font-size: 0.8rem;
		color: #555;
	}

	.copy-btn:hover {
		background: #f5f5f5;
	}

	.reorder {
		display: flex;
		gap: 0.2rem;
	}

	.inline-form {
		display: inline;
	}

	.arrow-btn {
		padding: 0.25rem 0.5rem;
		background: transparent;
		border: 1px solid #cfccc4;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.85rem;
		color: #555;
	}

	.arrow-btn:disabled {
		opacity: 0.3;
		cursor: default;
	}

	.arrow-btn:not(:disabled):hover {
		background: #f5f5f5;
	}

	.delete-btn {
		padding: 0.3rem 0.7rem;
		background: transparent;
		border: 1px solid #e0ccc4;
		border-radius: 6px;
		cursor: pointer;
		font-size: 0.8rem;
		color: #c33;
	}

	.delete-btn:hover {
		background: #fdf0f0;
	}
</style>