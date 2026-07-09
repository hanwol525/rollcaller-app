/**
 * Tier A E2E smoke test — Participant core loop.
 *
 * Flow: open /record/{token} → record → stop → land on review →
 *       hear (preview) → confirm → confirmed screen.
 *
 * Uses fake media (Chromium --use-fake-device-for-media-stream) so
 * getUserMedia/MediaRecorder produce real bytes the backend accepts.
 *
 * Prerequisite: FastAPI backend running on :8000, global-setup has seeded
 * an invite token in process.env.E2E_INVITE_TOKEN.
 */

import { test, expect } from '@playwright/test';

test.describe('Participant core loop (Tier A smoke)', () => {
	test('record → stop → review → confirm → confirmed', async ({ page }) => {
		const token = process.env.E2E_INVITE_TOKEN;
		expect(token).toBeTruthy();

		// 1. Open the record page
		await page.goto(`/record/${token}`);

		// 2. Landing screen — click "Start Recording"
		await expect(page.getByText('Record your name')).toBeVisible({ timeout: 10_000 });
		await page.getByRole('button', { name: 'Start Recording' }).click();

		// 3. Recording screen — wait for the recording indicator, then stop
		await expect(page.getByText('Recording…')).toBeVisible();
		// Brief wait so the recording captures some audio bytes
		await page.waitForTimeout(1500);
		await page.getByRole('button', { name: 'Stop' }).click();

		// 4. Upload + processing → should land on review screen
		await expect(page.getByText('Review your name')).toBeVisible({ timeout: 15_000 });

		// 5. Hear preview — click the "Hear your name" button
		//    The preview audio element should appear after clicking
		await page.getByRole('button', { name: /Hear your name/ }).click();
		// Wait for the preview audio to appear (may take a moment for backend TTS)
		await expect(page.locator('audio#preview')).toBeVisible({ timeout: 15_000 });

		// 6. Confirm — click the "Confirm" button
		await page.getByRole('button', { name: 'Confirm' }).click();

		// 7. Should land on the confirmed screen
		await expect(page.getByText("You're all set")).toBeVisible({ timeout: 15_000 });
	});

	test('⭐ E2E: IPA text never appears on the record page DOM', async ({ page }) => {
		const token = process.env.E2E_INVITE_TOKEN;
		expect(token).toBeTruthy();

		await page.goto(`/record/${token}`);

		// Wait for the page to load (either landing or review)
		await page.waitForLoadState('networkidle');

		// The page DOM must never contain IPA notation characters like /ˈ.../
		const bodyText = await page.locator('body').textContent();
		expect(bodyText).not.toMatch(/\/ˈ/);
		expect(bodyText).not.toMatch(/\/ˈæli\//);
	});
});