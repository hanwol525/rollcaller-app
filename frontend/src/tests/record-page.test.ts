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
import { render, cleanup, waitFor, fireEvent } from '@testing-library/svelte';

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
import { getSelf, uploadRecording, previewIpa } from '$lib/api';
import { startRecording, stopRecording } from '$lib/recorder';
import type { ParticipantSelf } from '$lib/api';

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

// jsdom has no URL.createObjectURL/revokeObjectURL, and its
// HTMLMediaElement.play is a "not implemented" stub. Without these,
// handleHearPreview calls previewIpa and then throws on the very next line,
// which flips `screen` to 'error' and unmounts the review card. The
// call-argument assertions below would still hold — the call precedes the
// throw — but they would be asserting through a broken render, and the
// autoplay setTimeout would outlive the test.
URL.createObjectURL = (() => 'blob:preview') as typeof URL.createObjectURL;
URL.revokeObjectURL = (() => {}) as typeof URL.revokeObjectURL;
HTMLMediaElement.prototype.play = (() =>
	Promise.resolve()) as typeof HTMLMediaElement.prototype.play;

const SAMPLE_IPA = '/ˈæli/';

function makeSelf(overrides: Partial<ParticipantSelf> = {}): ParticipantSelf {
	return {
		id: 1,
		name: 'Alice Johnson',
		space_name: 'Graduation 2025',
		status: 'recorded',
		ipa_text: SAMPLE_IPA,
		// A row with IPA always has a source. 'recognized' is the one that
		// means "from the recording currently on file", so it is the default.
		ipa_source: 'recognized',
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
		// ipa_text is deliberately POPULATED. It was `null`, which made the
		// assertion below unfalsifiable — the string it looks for was never in
		// the data. Mutation probe: leaking {self.ipa_text} onto the landing
		// screen left the old version passing and fails this one.
		const self = makeSelf({ status: 'invited', ipa_text: SAMPLE_IPA });
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
// ─ No pronunciation was derived ───────────────────────────────────────────── //

describe('review screen when no pronunciation could be read', () => {
	test('says so plainly, and offers re-record instead of confirm', async () => {
		// `recorded` with no IPA is a legal row: the recording was stored but
		// the recognizer produced nothing. Before this branch existed, the page
		// showed the full review UI and both Preview and Confirm returned
		// silently on `if (!ipaText) return;` — a dead end with no message.
		//
		// The assertion is positive, unlike the three invariants above: a null
		// fixture paired with a `not.toContain` is what made the landing-screen
		// test unfalsifiable (see its comment). Deleting the {:else} branch
		// fails this test.
		const self = makeSelf({ status: 'recorded', ipa_text: null });
		vi.mocked(getSelf).mockResolvedValue(self);

		const { container } = renderRecordPage();

		await waitFor(() => {
			expect(container.textContent).toContain('Recording saved');
		});

		expect(container.textContent).toContain('read a pronunciation from it');
		expect(container.textContent).toContain('Re-record');

		// Nothing to preview and nothing to confirm, so neither control renders.
		expect(container.textContent).not.toContain('Confirm');
		expect(container.textContent).not.toContain('Hear your name');
		expect(container.textContent).not.toContain('Review your name');
	});
});

// ─ Re-recording ───────────────────────────────────────────────────────────── //

describe('re-recording', () => {
	const TAKE_ONE = '/ˈoʊld/';
	const TAKE_TWO = '/ˈnjuː/';

	/** Drive Re-record → Start Recording → Stop, back to the review screen. */
	async function reRecord(ui: ReturnType<typeof renderRecordPage>) {
		const { getByRole } = ui;
		await fireEvent.click(getByRole('button', { name: 'Re-record' }));
		await fireEvent.click(getByRole('button', { name: 'Start Recording' }));
		await waitFor(() => getByRole('button', { name: 'Stop' }));
		await fireEvent.click(getByRole('button', { name: 'Stop' }));
	}

	function mockRecorder() {
		// handleStop guards on `if (!recorder) return;`, and `recorder` is a
		// plain let, not $state. A bare vi.fn() resolves undefined, which
		// leaves it falsy and swallows the Stop click silently.
		vi.mocked(startRecording).mockResolvedValue({} as unknown as MediaRecorder);
		vi.mocked(stopRecording).mockResolvedValue(new Blob(['audio']));
	}

	/**
	 * A GUARD, not a regression test — and the distinction matters.
	 *
	 * With $lib/api mocked, the frontend is already correct given the
	 * backend's response: once a failed re-record clears ipa_text, the {:else}
	 * branch renders. The defect was entirely in invite.py, and
	 * test_an_upload_moves_the_ipa_with_its_provenance is what catches it.
	 * This pins that the branch reads live state rather than anything cached
	 * at mount. Writing it as a "regression test" would be the unfalsifiable
	 * shape the landing-screen test above already got caught in.
	 */
	test('a re-record that reads nothing lands on "Recording saved"', async () => {
		vi.mocked(getSelf).mockResolvedValue(makeSelf({ ipa_text: TAKE_ONE }));
		vi.mocked(uploadRecording).mockResolvedValue(
			makeSelf({ ipa_text: null, ipa_source: null })
		);
		mockRecorder();

		const ui = renderRecordPage();
		await waitFor(() => {
			expect(ui.container.textContent).toContain('Review your name');
		});

		await reRecord(ui);

		await waitFor(() => {
			expect(ui.container.textContent).toContain('Recording saved');
		});
		expect(ui.container.textContent).toContain('read a pronunciation from it');
		expect(ui.container.textContent).not.toContain('Confirm');
		// ⭐ still no phonetics, on the screen the previous take's IPA left.
		expect(ui.container.textContent).not.toContain(TAKE_ONE);
	});

	/**
	 * THE regression test, and the one with a live mutation probe.
	 *
	 * `ipaText` (+page.svelte:25) is a deliberately non-reactive baton, set
	 * only in loadSelf and handleStop. Nothing else guards it going stale, so
	 * if handleStop stops refreshing it, a participant who re-records gets a
	 * preview — and then a Confirm — of the take they just replaced. Probe:
	 * deleting `ipaText = self.ipa_text` at +page.svelte:79 leaves the guard
	 * above passing and fails this one.
	 */
	test('"Hear your name" previews the new take, not the previous one', async () => {
		vi.mocked(getSelf).mockResolvedValue(makeSelf({ ipa_text: TAKE_ONE }));
		vi.mocked(uploadRecording).mockResolvedValue(makeSelf({ ipa_text: TAKE_TWO }));
		vi.mocked(previewIpa).mockResolvedValue(new Blob(['wav']));
		mockRecorder();

		const ui = renderRecordPage();
		await waitFor(() => {
			expect(ui.container.textContent).toContain('Review your name');
		});

		await reRecord(ui);
		await waitFor(() => {
			expect(ui.container.textContent).toContain('Review your name');
		});

		await fireEvent.click(ui.getByRole('button', { name: /Hear your name/ }));
		await waitFor(() => {
			expect(previewIpa).toHaveBeenCalled();
		});

		expect(previewIpa).toHaveBeenCalledWith('test-token-123', TAKE_TWO);
		expect(previewIpa).not.toHaveBeenCalledWith('test-token-123', TAKE_ONE);
	});

	test('a kept fallback pronunciation says so instead of claiming the recording was read', async () => {
		vi.mocked(getSelf).mockResolvedValue(
			makeSelf({ ipa_text: TAKE_ONE, ipa_source: 'g2p' })
		);

		const { container } = renderRecordPage();
		await waitFor(() => {
			expect(container.textContent).toContain('Review your name');
		});

		expect(container.textContent).toContain('this is the one already on file');
		expect(container.textContent).not.toContain(TAKE_ONE);
	});

	test('a hand-typed pronunciation is not described as unreadable', async () => {
		vi.mocked(getSelf).mockResolvedValue(
			makeSelf({ ipa_text: TAKE_ONE, ipa_source: 'manual' })
		);

		const { container } = renderRecordPage();
		await waitFor(() => {
			expect(container.textContent).toContain('Review your name');
		});

		expect(container.textContent).toContain('pronunciation you entered yourself');
		// The g2p line would be a lie here: recognition may well have worked.
		expect(container.textContent).not.toContain("couldn't read a pronunciation");
	});
});
