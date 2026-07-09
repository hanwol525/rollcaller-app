import type { PageServerLoad, Actions } from './$types';
import { serverFetch } from '$lib/server';
import { redirect } from '@sveltejs/kit';

export const load: PageServerLoad = async ({ cookies }) => {
const spaces = await serverFetch<{ id: number; name: string; advanced_seconds: number; created_at: string }[]>('/spaces', cookies);
return { spaces };
};

export const actions: Actions = {
create: async ({ request, cookies }) => {
const data = await request.formData();
const name = data.get('name')?.toString() ?? '';
const advanced_seconds = parseInt(data.get('advanced_seconds')?.toString() ?? '5', 10);

try {
await serverFetch('/spaces', cookies, {
method: 'POST',
body: JSON.stringify({ name, advanced_seconds })
});
} catch (e) {
if (e instanceof Response) throw e;
return { success: false, error: String(e) };
}
throw redirect(302, '/spaces');
}
};
