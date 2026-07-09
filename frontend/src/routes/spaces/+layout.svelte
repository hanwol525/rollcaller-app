<script lang="ts">
	import { logout } from '$lib/api';
	import { goto } from '$app/navigation';

	let { children } = $props();

	async function handleLogout() {
		await logout();
		goto('/');
	}
</script>

<div class="shell">
	<nav>
		<a href="/spaces" class="brand">RollCaller</a>
		<button onclick={handleLogout} class="logout-btn">Sign Out</button>
	</nav>
	<main>
		{@render children()}
	</main>
</div>

<style>
	.shell {
		min-height: 100vh;
		background: #eceae5;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
	}

	nav {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.75rem 1.5rem;
		background: white;
		border-bottom: 1px solid #cfccc4;
	}

	.brand {
		font-size: 1.2rem;
		font-weight: 700;
		color: #33322f;
		text-decoration: none;
	}

	.logout-btn {
		padding: 0.4rem 1rem;
		background: transparent;
		border: 1px solid #cfccc4;
		border-radius: 8px;
		cursor: pointer;
		font-size: 0.85rem;
		color: #555;
	}

	.logout-btn:hover {
		background: #f5f5f5;
	}

	main {
		padding: 2rem 1.5rem;
		max-width: 900px;
		margin: 0 auto;
	}
</style>