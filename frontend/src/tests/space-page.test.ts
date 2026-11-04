/**
 * Regression tests for the organizer's render report and Clip column.
 *
 * The batch render used to be all-or-nothing: one bad name 500'd the whole
 * roster, nothing was committed, and the organizer saw a raw
 * "Error: 500: Internal Server Error". Now a partial render reports which
 * participants got no clip.
 *
 * Two things these pin:
 * - "a run that prepared nothing does not claim success" — `rendered: 0`
 *   satisfies `!== undefined`, so the original branch order would have
 *   printed "Prepared 0 clip(s)." while silently dropping the failures.
 * - the Clip column is durable where the message is not: `form` is null
 *   after any reload or any of the five redirecting actions. That claim only
 *   became true once the column grew enough states to carry the information
 *   — it used to collapse "refused" and "never prepped" into one "None", so
 *   the durable signal said nothing the transient one had said.
 */

import { test, expect, describe, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';

import SpacePage from '../routes/spaces/[spaceId]/+page.svelte';

afterEach(() => {
	cleanup();
});

function participant(overrides: Record<string, unknown> = {}) {
	return {
		id: 1,
		name: 'Alice',
		email: null,
		status: 'invited',
		position: 0,
		ipa_text: null,
		ipa_source: null,
		ipa_confirmed: false,
		invite_token: 'tok-1',
		recording_key: null,
		clip_key: null,
		// Computed server-side: why Prep Clips will refuse this name. Explicit
		// here rather than absent, so a test that depends on it being null
		// says so.
		clip_blocked_reason: null,
		...overrides
	};
}

function renderPage(form: unknown, participants = [participant()]) {
	return render(SpacePage, {
		props: {
			data: {
				space: { id: 1, name: 'Graduation 2025', advanced_seconds: 3, created_at: '' },
				participants
			},
			form
		} as never
	});
}

describe('the render report', () => {
	test('names the participants who got no clip', () => {
		const { container } = renderPage({
			action: 'render',
			rendered: 2,
			failures: [
				{ name: 'Nguyễn', reason: "contains the digit '5'" },
				{ name: 'Boom', reason: 'RuntimeError: Kokoro produced no audio' }
			]
		});

		expect(container.textContent).toContain('No pronunciation for 2');
		expect(container.textContent).toContain('Nguyễn');
		expect(container.textContent).toContain('Boom');
	});

	test('a clean run reports only the count', () => {
		const { container } = renderPage({ action: 'render', rendered: 3, failures: [] });

		expect(container.textContent).toContain('Prepared 3 clip(s).');
		expect(container.textContent).not.toContain('No pronunciation');
	});

	test('a run that prepared nothing does not claim success', () => {
		const { container } = renderPage({
			action: 'render',
			rendered: 0,
			failures: [{ name: 'Alice', reason: 'no pronunciation could be derived' }]
		});

		expect(container.textContent).toContain('No pronunciation for 1');
		expect(container.textContent).toContain('Alice');
	});

	test('a hard error still shows the error, not a count', () => {
		const { container } = renderPage({ action: 'render', error: 'Error: 401: Unauthorized' });

		expect(container.textContent).toContain('401');
		expect(container.textContent).not.toContain('Prepared');
	});
});

describe('the roster Clip column', () => {
	test('marks a participant who has no clip', () => {
		const { getByText } = renderPage(null, [participant({ clip_key: null })]);
		expect(getByText('None')).toBeInTheDocument();
	});

	test('marks a participant whose clip is ready', () => {
		const { getByText } = renderPage(null, [participant({ clip_key: 'abc123.wav' })]);
		expect(getByText('Ready')).toBeInTheDocument();
	});

	test('flags a refused participant once the render message is gone', () => {
		// `form === null` is the state after any reload and after all five
		// redirecting actions, so the per-run warning has already vanished.
		// This column is the only thing left that knows, which is what the
		// file header means by durable. Before it grew a third state, this
		// participant read "None" — indistinguishable from one nobody had
		// prepped.
		const { getByText, queryByText } = renderPage(null, [
			participant({
				clip_key: null,
				ipa_text: 'rec044',
				ipa_source: 'manual',
				ipa_confirmed: true,
				clip_blocked_reason: "contains the digit '0', which Kokoro reads aloud"
			})
		]);

		expect(getByText('Needs attention')).toBeInTheDocument();
		expect(queryByText('None')).not.toBeInTheDocument();
		expect(queryByText('Ready')).not.toBeInTheDocument();
	});

	test('separates a pronunciation awaiting a render from no pronunciation', () => {
		// Neither has a clip, and neither is refused. The difference is whether
		// there is anything to render, and both used to read "None".
		const { getByText } = renderPage(null, [
			participant({ clip_key: null, ipa_text: 'bˈɑːb', ipa_source: 'g2p', ipa_confirmed: true })
		]);

		expect(getByText('Not prepared')).toBeInTheDocument();
	});
});
