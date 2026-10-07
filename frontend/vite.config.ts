import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

const backend = process.env.BACKEND_URL || 'http://localhost:8000';

export default defineConfig({
	plugins: [
		sveltekit()
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
			'/auth': backend,
			'/invite': {
				target: backend,
				bypass: (req) => {
					if (req.headers.accept?.includes('text/html')) return false;
				}
			},
			'/media': backend
		}
	}
});
