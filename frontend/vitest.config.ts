/**
 * Vitest configuration — separate from vite.config.ts.
 *
 * The SvelteKit plugin (sveltekit()) overrides resolve conditions, forcing
 * Svelte to resolve to index-server.js where mount() is unavailable.
 * For component tests we need the standalone @sveltejs/vite-plugin-svelte
 * with the browser condition so Svelte resolves to index-client.js.
 *
 * We also need to manually provide the aliases that SvelteKit normally
 * handles: $lib, $app/environment, etc.
 */

import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
	plugins: [
		svelte({
			compilerOptions: {
				runes: true
			}
		})
	],

	resolve: {
		conditions: ['browser'],
		alias: {
			$lib: path.resolve(__dirname, 'src/lib'),
			// SvelteKit virtual modules — stub $app/environment for tests
			'$app/environment': path.resolve(__dirname, 'src/lib/test/app-environment.ts')
		}
	},

	test: {
		environment: 'jsdom',
		setupFiles: ['./src/lib/test/setup.ts'],
		include: ['src/**/*.{test,spec}.{js,ts}'],
		exclude: ['e2e/**', 'node_modules/**', 'build/**'],
		server: {
			deps: {
				inline: ['svelte', '@testing-library/svelte']
			}
		}
	}
});