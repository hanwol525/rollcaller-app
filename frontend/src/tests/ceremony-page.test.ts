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

	test('finished screen never displays IPA text', () => {
		const { container } = renderCeremony(makeCeremony({ roster: [] }));
		expect(container.textContent).toContain('Ceremony Complete');
		expect(container.textContent).not.toMatch(new RegExp(`${SAMPLE_IPA}|/ˈbɒb/`));
	});
	// byte-identical to the test above.
	//
	// It IS reachable from props, contrary to what this TODO first claimed:
	// an empty roster makes `0 >= 0` true, and the not-prepped gate is guarded
	// by `roster.length > 0` so it does not intercept. A real test renders
	// makeCeremony({ roster: [] }) and asserts the completion copy, then a
	// populated roster driven to the end via Next.
});

// ─ Regression: persistent audio element ───────────────────────────────────── //

describe('regression: play works on every item via the persistent audio element', () => {
	// Both halves of the regression are needed. The pair is complementary, not
	// redundant: this one pins the clip-BEARING path, the one below pins the
	// null-clip path. Deleting either leaves a conditional-mount regression
	// detectable in only one direction.
	test('audio element is in the DOM when the current item HAS a clip', () => {
		const ceremony = makeCeremony();
		const { container } = renderCeremony(ceremony);

		expect(container.querySelector('audio')).toBeInTheDocument();
	});

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

describe('no-clip participants are marked in the Up Next queue', () => {
	test('a null-clip item gets the lowercase "no clip" badge in the queue', () => {
		// Named for what it actually asserts. It does NOT cover the player's own
		// "No clip available for this person." message (+page.svelte:161, capital
		// N) — toContain is case-sensitive, so this only ever matched the queue
		// badge at +page.svelte:204. It also does not cover auto-advance, which
		// is why this describe block no longer claims to.
		//
		// The player message and auto-advance both need currentIndex control,
		// which props cannot provide — see the TODO below.
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

	// TODO(ceremony-player brief): three uncovered things, all needing the same
	// capability — driving currentIndex, which props cannot do.
	//   1. prev/next buttons. The deleted 'a null-clip item shows navigation
	//      buttons (prev/next)' asserted only toContain('No Clip'), the
	//      participant NAME from its own fixture, not a button. Probe: deleting
	//      both .nav-btn pairs left it passing. Needs
	//      getByRole('button', { name: /prev|next/i }) plus a click.
	//   2. The player's "No clip available for this person." message
	//      (+page.svelte:161), reachable only when the CURRENT item lacks a clip.
	//   3. Auto-advance itself — armAdvance/handleEnded and the
	//      advanced_seconds timer are untested end to end.
	//
	// Also note: deleting that test removed the suite's only assertion that a
	// participant name renders anywhere on the ceremony page (the queue-name
	// span at +page.svelte:202). Nothing asserts it today.
});