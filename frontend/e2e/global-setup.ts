/**
 * Playwright global setup — seeds the backend with known state.
 *
 * Creates an organizer, logs in, creates a space, adds a participant,
 * and exposes the invite token for the participant E2E test.
 *
 * Prerequisites:
 *   - FastAPI backend must be running on http://localhost:8000
 *
 * The setup is idempotent — it reuses existing data if present.
 */

import { request, expect } from '@playwright/test';

const BACKEND = 'http://localhost:8000';
const ORGANIZER_USER = 'organizer';
const ORGANIZER_PASS = 'changeme';

async function apiFetch(context: any, path: string, options: any = {}) {
	const res = await context.fetch(`${BACKEND}${path}`, {
		...options,
		headers: {
			...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
			...options.headers
		}
	});
	if (!res.ok()) {
		const body = await res.text();
		throw new Error(`${options.method || 'GET'} ${path} → ${res.status()}: ${body}`);
	}
	return res;
}

export default async function globalSetup() {
	const context = await request.newContext();

	// 1. Log in as organizer — the session cookie is stored in the context
	await apiFetch(context, '/auth/login', {
		method: 'POST',
		body: JSON.stringify({ username: ORGANIZER_USER, password: ORGANIZER_PASS })
	});

	// 2. Get existing spaces
	const spacesRes = await apiFetch(context, '/spaces');
	const spaces = await spacesRes.json();

	// 3. Find or create a space named "E2E Test Event"
	let space = spaces.find((s: any) => s.name === 'E2E Test Event');
	if (!space) {
		const createRes = await apiFetch(context, '/spaces', {
			method: 'POST',
			body: JSON.stringify({ name: 'E2E Test Event', advanced_seconds: 2 })
		});
		space = await createRes.json();
	}

	// 4. Get existing participants
	const partsRes = await apiFetch(context, `/spaces/${space.id}/participants`);
	const participants = await partsRes.json();

	// 5. Find or create a test participant named "E2E Test Person"
	let participant = participants.find((p: any) => p.name === 'E2E Test Person');
	if (!participant) {
		const addRes = await apiFetch(context, `/spaces/${space.id}/participants`, {
			method: 'POST',
			body: JSON.stringify([{ name: 'E2E Test Person', email: 'e2e@test.com' }])
		});
		const added = await addRes.json();
		participant = added[0];
	}

	// 6. Expose the token for tests via env
	process.env.E2E_SPACE_ID = String(space.id);
	process.env.E2E_INVITE_TOKEN = participant.invite_token;

	console.log(`[global-setup] Space ID: ${space.id}, Token: ${participant.invite_token}`);

	await context.dispose();
}