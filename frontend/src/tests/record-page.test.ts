/**
 * Design-invariant + regression tests for the record-flow page.
 *
 * ⭐ "No phonetics, ever" — the review screen must never display IPA text,
 * even though `ipa_text` is carried in state as the "hidden baton" for
 * the preview/confirm flow.
 *
 * Also covers resumability: loading /record/{token} when status is
 * "recorded" lands on the review screen, not the landing screen.
 */

import { test, expect, describe, vi, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/svelte';

// Mock the api module so getSelf returns a participant with IPA data
vi.mock('$lib/api', () => ({
	getSelf: vi.fn(),
	uploadRecording: vi.fn(),
	previewIpa: vi.fn(),
	confirmIpa: vi.fn()
}));

// Mock the recorder module (jsdom has no MediaRecorder)
vi.mock('$lib/recorder', () => ({
	startRecording: vi.fn(),
	stopRecording: vi.fn()
}));

// Import after mocks are set up
import RecordPage from '../routes/record/[token]/+page.svelte';
import { getSelf } from '$lib/api';
import type { ParticipantSelf } from '$lib/api';

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

const SAMPLE_IPA = '/ˈæli/';

function makeSelf(overrides: Partial<ParticipantSelf> = {}): ParticipantSelf {
	return {
		id: 1,
		name: 'Alice Johnson',
		space_name: 'Graduation 2025',
		status: 'recorded',
		ipa_text: SAMPLE_IPA,
		ipa_confirmed: false,
		...overrides
	};
}

/** Render the record page with a mocked token param. */
function renderRecordPage() {
	return render(RecordPage, {
		props: { params: { token: 'test-token-123' } }
	});
}

// ─⭐ Invariant: No phonetics on the review screen ────────────────────────── //

describe('⭐ "no phonetics" invariant — record-flow review screen', () => {
	test('review screen never displays IPA text', async () => {
		const self = makeSelf({ status: 'recorded', ipa_text: SAMPLE_IPA });
		vi.mocked(getSelf).mockResolvedValue(self);

		const { container } = renderRecordPage();

		// Wait for onMount → getSelf → re-render to review screen
		await waitFor(() => {
			expect(container.textContent).toContain('Review your name');
		});

		// The IPA string is in `self.ipa_text` and carried as the hidden baton,
		// but it must NEVER appear in the rendered DOM.
		expect(container.textContent).not.toContain(SAMPLE_IPA);
	});

	test('confirmed screen never displays IPA text', async () => {
		const self = makeSelf({ status: 'confirmed', ipa_text: SAMPLE_IPA });
		vi.mocked(getSelf).mockResolvedValue(self);

		const { container } = renderRecordPage();

		await waitFor(() => {
			expect(container.textContent).toContain("You're all set");
		});

		expect(container.textContent).not.toContain(SAMPLE_IPA);
	});

	test('landing screen never displays IPA text', async () => {
		const self = makeSelf({ status: 'invited', ipa_text: null });
		vi.mocked(getSelf).mockResolvedValue(self);

		const { container } = renderRecordPage();

		await waitFor(() => {
			expect(container.textContent).toContain('Record your name');
		});

		// Even if ipa_text were set, it shouldn't show on the landing screen
		expect(container.textContent).not.toContain(SAMPLE_IPA);
	});
});

// ─ Resumability invariant ─────────────────────────────────────────────────── //

describe('resumability — refresh mid-flow never resets progress', () => {
	test('loading /record/{token} when status is "recorded" lands on the review screen', async () => {
		const self = makeSelf({ status: 'recorded', ipa_text: SAMPLE_IPA });
		vi.mocked(getSelf).mockResolvedValue(self);

		const { container } = renderRecordPage();

		await waitFor(() => {
			expect(container.textContent).toContain('Review your name');
		});

		// Must NOT show the landing/recording screen
		expect(container.textContent).not.toContain('Start Recording');
	});

	test('loading /record/{token} when status is "confirmed" lands on the confirmed screen', async () => {
		const self = makeSelf({ status: 'confirmed', ipa_text: SAMPLE_IPA });
		vi.mocked(getSelf).mockResolvedValue(self);

		const { container } = renderRecordPage();

		await waitFor(() => {
			expect(container.textContent).toContain("You're all set");
		});

		expect(container.textContent).not.toContain('Start Recording');
		expect(container.textContent).not.toContain('Review your name');
	});
});