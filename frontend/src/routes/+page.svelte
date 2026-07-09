<script lang="ts">
	import type { ActionData } from './$types';

	let { form }: { form: ActionData } = $props();

	let username = $state('');
	let password = $state('');
</script>

<svelte:head><title>RollCaller — Sign In</title></svelte:head>

<div class="login-wrapper">
	<div class="login-card">
		<h1>RollCaller</h1>
		<p class="subtitle">Sign in to manage your events</p>

		<form method="POST" class="login-form">
			{#if form?.error}
				<p class="error">{form.error}</p>
			{/if}
			<label>
				Username
				<input name="username" type="text" bind:value={username} required placeholder="organizer" />
			</label>
			<label>
				Password
				<input name="password" type="password" bind:value={password} required placeholder="changeme" />
			</label>
			<button type="submit" class="btn-primary">Sign In</button>
		</form>
	</div>
</div>

<style>
	.login-wrapper {
		min-height: 100vh;
		display: flex;
		align-items: center;
		justify-content: center;
		background: #eceae5;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
	}

	.login-card {
		background: white;
		border-radius: 16px;
		padding: 2.5rem 2rem;
		width: 360px;
		box-shadow: 0 2px 16px rgba(0, 0, 0, 0.08);
	}

	h1 {
		font-size: 1.8rem;
		font-weight: 700;
		color: #33322f;
		text-align: center;
		margin: 0 0 0.25rem;
	}

	.subtitle {
		font-size: 0.85rem;
		color: #999;
		text-align: center;
		margin-bottom: 1.5rem;
	}

	.login-form {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.error {
		color: #c33;
		font-size: 0.85rem;
		text-align: center;
		margin-bottom: 0.25rem;
	}

	label {
		display: block;
		font-size: 0.85rem;
		color: #555;
	}

	input {
		display: block;
		width: 100%;
		padding: 0.6rem 0.8rem;
		border: 1px solid #cfccc4;
		border-radius: 8px;
		font-size: 1rem;
		margin-top: 0.25rem;
		box-sizing: border-box;
	}

	input:focus {
		outline: none;
		border-color: #33322f;
	}

	.btn-primary {
		padding: 0.7rem 1.2rem;
		background: #33322f;
		color: white;
		border: none;
		border-radius: 8px;
		cursor: pointer;
		font-size: 1rem;
		margin-top: 0.5rem;
	}

	.btn-primary:hover {
		background: #444;
	}
</style>