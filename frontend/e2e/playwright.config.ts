/**
 * Playwright configuration for RollCaller E2E tests.
 *
 * - Boots the SvelteKit dev server automatically (webServer).
 * - The FastAPI backend must be running separately (documented prerequisite).
 * - Chromium launches with fake-media flags so getUserMedia/MediaRecorder
 *   work headless and permission is auto-granted.
 */

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	testDir: './e2e/tests',
	globalSetup: './e2e/global-setup.ts',
	fullyParallel: false, // E2E shares backend state — run sequentially
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	workers: 1, // Single worker — shared backend state
	reporter: 'list',

	use: {
		baseURL: 'http://localhost:5173',
		trace: 'on-first-retry',
		// Fake media flags — critical for the record flow
		launchOptions: {
			args: [
				'--use-fake-device-for-media-stream',
				'--use-fake-ui-for-media-stream'
			]
		}
	},

	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] }
		}
	],

	webServer: {
		command: 'npm run dev',
		url: 'http://localhost:5173',
		reuseExistingServer: !process.env.CI,
		timeout: 30_000
	}
});