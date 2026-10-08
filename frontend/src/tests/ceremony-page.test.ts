/**
 * Design-invariant + regression tests for the ceremony player.
 *
 * ⭐ "No phonetics, ever" — the most important invariant.
 * The ceremony projector screen must never render IPA text, even when
 * the roster data contains `ipa_text`. This is the check that would have
 * caught the projector leak.
 *
 * Also includes named regression tests for bugs we already fixed:
 * - "play works on every item via the persistent audio element"
 * - "auto-advance skips a participant with no clip instead of freezing"
 * - "ceremony player never renders IPA text"
 */

import { test, expect, describe, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';

import CeremonyPage from '../routes/spaces/[spaceId]/ceremony/+page.svelte';
import type { CeremonyData } from '$lib/api';

afterEach(() => {
	cleanup();
});

// ── Test fixtures ─────────────────────────────────────────────────────────── //

const SAMPLE_IPA = '/ˈæli/';

function makeCeremony(overrides: Partial<CeremonyData> = {}): CeremonyData {
	return {
		space_id: 1,
		space_name: 'Graduation 2025',
		advanced_seconds: 3,
		roster: [
			{
				position: 1,
				name: 'Alice Johnson',
				clip_url: 'https://example.com/audio/alice.mp3',
				ipa_text: SAMPLE_IPA,
				ipa_source: 'g2p'
			},
			{
				position: 2,
				name: 'Bob Smith',
				clip_url: 'https://example.com/audio/bob.mp3',
				ipa_text: '/ˈbɒb/',
				ipa_source: 'g2p'
			}
		],
		...overrides
	};
}

/** Render the ceremony page with the given ceremony data. */
function renderCeremony(ceremony: CeremonyData) {
	return render(CeremonyPage, { props: { data: { user: { id: 1, username: 'organizer' }, ceremony } } });
}

// ─⭐ Invariant: No phonetics on any user-facing screen ───────────────────── //

describe('⭐ "no phonetics" invariant — ceremony player', () => {
	test('ceremony player never renders IPA text', () => {
		const ceremony = makeCeremony();
		const { container } = renderCeremony(ceremony);

		// The IPA strings are in the data but must NEVER appear in the DOM
		expect(container.textContent).not.toContain(SAMPLE_IPA);
		expect(container.textContent).not.toContain('/ˈbɒb/');
	});

	test('IPA does not leak in the queue list', () => {
		const ceremony = makeCeremony();
		const { container } = renderCeremony(ceremony);

		// The "Up Next" queue shows names, not phonetics
		expect(container.textContent).not.toContain(SAMPLE_IPA);
	});

	// TODO(ceremony-player brief): real coverage of the finished screen.
	// The deleted 'IPA does not leak on the finished screen' never rendered it:
	// the component starts at currentIndex 0 and isFinished is
	// `currentIndex >= roster.length`, so with the 2-item fixture the
	// {:else if isFinished} branch never mounts. Its two assertions were
	// byte-identical to the test above. Reaching that screen needs the suite to
	// drive Next to the end, which no test here does.
});

// ─ Regression: persistent audio element ───────────────────────────────────── //

describe('regression: play works on every item via the persistent audio element', () => {
	// Deleted: 'the <audio> element is always in the DOM (not conditionally
	// mounted)'. It rendered the default fixture, whose first item HAS a clip,
	// so it could not detect the regression it was named for. Mutation probe:
	// wrapping the <audio> in {#if currentItem?.clip_url} left it passing while
	// the test below failed. That test covers the regression completely.

	test('audio element is in the DOM even when the first item has no clip', () => {
		const ceremony = makeCeremony({
			roster: [
				{
					position: 1,
					name: 'No Clip Person',
					clip_url: null,
					ipa_text: SAMPLE_IPA,
					ipa_source: 'g2p'
				},
				{
					position: 2,
					name: 'Has Clip',
					clip_url: 'https://example.com/audio/bob.mp3',
					ipa_text: null,
					ipa_source: null
				}
			]
		});
		const { container } = renderCeremony(ceremony);

		// Audio must be in the DOM even when current item has no clip
		expect(container.querySelector('audio')).toBeInTheDocument();
	});
});

// ─ Regression: not-prepped gate ───────────────────────────────────────────── //

describe('not-prepped gate', () => {
	test('shows the gate message when all clips are null', () => {
		const ceremony = makeCeremony({
			roster: [
				{ position: 1, name: 'Alice', clip_url: null, ipa_text: SAMPLE_IPA, ipa_source: 'g2p' }
			]
		});
		const { getByText } = renderCeremony(ceremony);

		expect(getByText(/Clips haven't been prepared yet/)).toBeInTheDocument();
	});

	test('does not show the gate when at least one clip exists', () => {
		const ceremony = makeCeremony({
			roster: [
				{ position: 1, name: 'Alice', clip_url: null, ipa_text: null, ipa_source: null },
				{ position: 2, name: 'Bob', clip_url: 'https://example.com/audio/bob.mp3', ipa_text: null, ipa_source: null }
			]
		});
		const { queryByText } = renderCeremony(ceremony);

		expect(queryByText(/Clips haven't been prepared yet/)).not.toBeInTheDocument();
	});
});

// ─ Regression: no-clip item shows "No clip available" ─────────────────────── //

describe('regression: auto-advance skips a participant with no clip instead of freezing', () => {
	test('a null-clip item shows a "No clip available" message', () => {
		// Need at least one non-null clip so the not-prepped gate doesn't trigger.
		// The first item (index 0) has a clip, but the player starts on it.
		// We can't control currentIndex from props, so we test via the queue:
		// verify the "no clip" badge appears for null-clip items in the queue.
		const ceremony = makeCeremony({
			roster: [
				{ position: 1, name: 'Has Clip', clip_url: 'https://example.com/audio/alice.mp3', ipa_text: null, ipa_source: null },
				{ position: 2, name: 'No Clip Person', clip_url: null, ipa_text: SAMPLE_IPA, ipa_source: 'g2p' }
			]
		});
		const { container } = renderCeremony(ceremony);

		// The queue should show "no clip" badge for the null-clip item
		expect(container.textContent).toContain('no clip');
	});

	// TODO(ceremony-player brief): real coverage of prev/next on a no-clip item.
	// The deleted 'a null-clip item shows navigation buttons (prev/next)'
	// asserted only `toContain('No Clip')` — which is the participant NAME from
	// its own fixture, not a button. Mutation probe: deleting both .nav-btn
	// pairs from the component left it passing. Real coverage needs
	// getByRole('button', { name: /prev|next/i }) and a click that advances
	// currentIndex, which requires driving the component rather than props.
});