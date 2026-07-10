import adapter from '@sveltejs/adapter-auto';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// adapter-auto only supports some environments, see https://svelte.dev/docs/kit/adapter-auto for a list.
			// If your environment is not supported, or you settled on a specific environment, switch out the adapter.
			// See https://svelte.dev/docs/kit/adapters for more information about adapters.
			adapter: adapter()
		})
	],

	server: {
		proxy: {
			// Only proxy API paths that don't collide with page routes.
			// /spaces is a SvelteKit page route AND an API path — the
			// server-side code (serverFetch in $lib/server.ts) calls the
			// backend directly at localhost:8000, so no proxy is needed.
			// The client-side recorder page uses /invite and /media via
			// fetch, which do collide — so we bypass those for non-fetch
			// (page navigation) requests.
			'/auth': 'http://localhost:8000',
			'/invite': {
				target: 'http://localhost:8000',
				bypass: (req) => {
					// Only proxy fetch/API calls, not page navigations
					if (req.headers.accept?.includes('text/html')) return false;
				}
			},
			'/media': 'http://localhost:8000'
		}
	}
});