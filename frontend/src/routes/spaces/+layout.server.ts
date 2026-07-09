import type { LayoutServerLoad } from './$types';
import { serverFetch } from '$lib/server';

export const load: LayoutServerLoad = async ({ cookies }) => {
// Gate: if not authenticated, redirect to login
const me = await serverFetch<{ id: number; username: string }>('/auth/me', cookies);
return { user: me };
};
