import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit()
	],

	server: {
		proxy: {
			'/auth': 'http://localhost:8000',
			'/invite': {
				target: 'http://localhost:8000',
				bypass: (req) => {
					if (req.headers.accept?.includes('text/html')) return false;
				}
			},
			'/media': 'http://localhost:8000'
		}
	}
});
