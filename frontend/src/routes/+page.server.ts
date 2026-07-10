import type { Actions } from '@sveltejs/kit';
import { redirect } from '@sveltejs/kit';
import { serverLogin, serverFetch } from '$lib/server';

export const actions: Actions = {
	default: async ({ request, cookies }) => {
		const data = await request.formData();
		const username = data.get('username')?.toString() ?? '';
		const password = data.get('password')?.toString() ?? '';

		const ok = await serverLogin(cookies, username, password);
		if (!ok) {
			return { success: false, error: 'Invalid username or password' };
		}

		// Verify the session works
		try {
			await serverFetch('/auth/me', cookies);
			throw redirect(302, '/spaces');
		} catch (e) {
			// SvelteKit 2.x redirect throws a Redirect object, not Response.
			// Re-throw it so SvelteKit can perform the redirect.
			if (e && typeof e === 'object' && 'status' in e && 'location' in e) {
				throw e;
			}
			return { success: false, error: 'Login failed: ' + String(e) };
		}
	}
};
