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
 *   after any reload or any of the five redirecting actions.
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
});
