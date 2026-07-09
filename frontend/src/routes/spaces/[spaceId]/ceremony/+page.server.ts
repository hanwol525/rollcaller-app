import type { PageServerLoad } from './$types';
import { serverFetch } from '$lib/server';
import type { CeremonyData } from '$lib/api';

export const load: PageServerLoad = async ({ params, cookies }) => {
	const spaceId = parseInt(params.spaceId, 10);
	const ceremony = await serverFetch<CeremonyData>(`/spaces/${spaceId}/ceremony`, cookies);
	return { ceremony };
};