import type { PageServerLoad, Actions } from './$types';
import { serverFetch } from '$lib/server';
import { redirect } from '@sveltejs/kit';

export const load: PageServerLoad = async ({ params, cookies }) => {
const spaceId = parseInt(params.spaceId, 10);
const space = await serverFetch<{ id: number; name: string; advanced_seconds: number; created_at: string }>(`/spaces/${spaceId}`, cookies);
const participants = await serverFetch<{
id: number; name: string; email: string | null;
status: string; position: number;
ipa_text: string | null; ipa_source: string | null; ipa_confirmed: boolean;
invite_token: string; recording_key: string | null; clip_key: string | null;
}[]>(`/spaces/${spaceId}/participants`, cookies);
return { space, participants };
};

export const actions: Actions = {
add: async ({ request, params, cookies }) => {
const spaceId = parseInt(params.spaceId, 10);
const data = await request.formData();
const name = data.get('name')?.toString() ?? '';
const email = data.get('email')?.toString() || null;
try {
await serverFetch(`/spaces/${spaceId}/participants`, cookies, {
method: 'POST',
body: JSON.stringify([{ name, email }])
});
} catch (e) {
if (e instanceof Response) throw e;
return { action: 'add', error: String(e) };
}
throw redirect(302, `/spaces/${spaceId}`);
},

importCsv: async ({ request, params, cookies }) => {
const spaceId = parseInt(params.spaceId, 10);
const data = await request.formData();
const file = data.get('csv_file') as File;
if (!file) return { action: 'import', error: 'No file provided' };

const formData = new FormData();
formData.append('csv_file', file);
try {
await serverFetch(`/spaces/${spaceId}/participants`, cookies, {
method: 'POST',
body: formData
});
} catch (e) {
if (e instanceof Response) throw e;
return { action: 'import', error: String(e) };
}
throw redirect(302, `/spaces/${spaceId}`);
},

delete: async ({ request, params, cookies }) => {
const spaceId = parseInt(params.spaceId, 10);
const data = await request.formData();
const pid = parseInt(data.get('pid')?.toString() ?? '0', 10);
try {
await serverFetch(`/spaces/${spaceId}/participants/${pid}`, cookies, {
method: 'DELETE'
});
} catch (e) {
if (e instanceof Response) throw e;
return { action: 'delete', error: String(e) };
}
throw redirect(302, `/spaces/${spaceId}`);
},

reorder: async ({ request, params, cookies }) => {
	const spaceId = parseInt(params.spaceId, 10);
	const data = await request.formData();
	const pid = parseInt(data.get('pid')?.toString() ?? '0', 10);
	const direction = data.get('direction')?.toString() ?? '';

	try {
		// Fetch the current roster (already sorted by position)
		const participants = await serverFetch<{
			id: number; position: number;
		}[]>(`/spaces/${spaceId}/participants`, cookies);

		// Find the clicked participant by pid in the sorted list
		const clickedIndex = participants.findIndex((p) => p.id === pid);
		if (clickedIndex === -1) {
			return { action: 'reorder', error: 'Participant not found' };
		}

		// Find the neighbor by sorted adjacency
		const neighborIndex = direction === 'up' ? clickedIndex - 1 : clickedIndex + 1;

		// Guard boundaries — first row up or last row down is a no-op
		if (neighborIndex < 0 || neighborIndex >= participants.length) {
			throw redirect(302, `/spaces/${spaceId}`);
		}

		const clicked = participants[clickedIndex];
		const neighbor = participants[neighborIndex];

		// Swap the two participants' position values — two PUTs
		await serverFetch(`/spaces/${spaceId}/participants/${clicked.id}`, cookies, {
			method: 'PUT',
			body: JSON.stringify({ position: neighbor.position })
		});
		await serverFetch(`/spaces/${spaceId}/participants/${neighbor.id}`, cookies, {
			method: 'PUT',
			body: JSON.stringify({ position: clicked.position })
		});
	} catch (e) {
		if (e instanceof Response) throw e;
		return { action: 'reorder', error: String(e) };
	}
	throw redirect(302, `/spaces/${spaceId}`);
},

render: async ({ params, cookies }) => {
const spaceId = parseInt(params.spaceId, 10);
try {
const result = await serverFetch<{ rendered: number }>(`/spaces/${spaceId}/render`, cookies, {
method: 'POST'
});
return { action: 'render', rendered: result.rendered };
} catch (e) {
if (e instanceof Response) throw e;
return { action: 'render', error: String(e) };
}
},

pacing: async ({ request, params, cookies }) => {
const spaceId = parseInt(params.spaceId, 10);
const data = await request.formData();
const advanced_seconds = parseInt(data.get('advanced_seconds')?.toString() ?? '5', 10);
try {
await serverFetch(`/spaces/${spaceId}`, cookies, {
method: 'PUT',
body: JSON.stringify({ advanced_seconds })
});
} catch (e) {
if (e instanceof Response) throw e;
return { action: 'pacing', error: String(e) };
}
throw redirect(302, `/spaces/${spaceId}`);
}
};
