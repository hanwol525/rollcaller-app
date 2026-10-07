import type { Cookies } from '@sveltejs/kit';

// In prod (k8s) this is http://rollcaller-backend.rollcall.svc.cluster.local:8000.
// In dev it defaults to the local backend.
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';
const SESSION_COOKIE = 'session';

export async function serverLogin(
	cookies: Cookies,
	username: string,
	password: string
): Promise<boolean> {
	const res = await fetch(`${BACKEND_URL}/auth/login`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ username, password })
	});

	if (res.status === 401) return false;
	if (!res.ok) throw new Error(`Login failed: ${res.status}`);

	const setCookie = res.headers.get('set-cookie');
	if (setCookie) {
		const match = setCookie.match(/session=([^;]+)/);
		if (match) {
			cookies.set(SESSION_COOKIE, match[1], {
				httpOnly: true,
				sameSite: 'lax',
				secure: process.env.NODE_ENV === 'production',
				path: '/',
				maxAge: 60 * 60 * 24 * 7
			});
		}
	}
	return true;
}

export async function serverFetch<T>(
	path: string,
	cookies: Cookies,
	options?: { method?: string; body?: BodyInit }
): Promise<T> {
	const session = cookies.get(SESSION_COOKIE);
	const headers: Record<string, string> = {};
	if (session) headers['Cookie'] = `${SESSION_COOKIE}=${session}`;

	if (options?.body && typeof options.body === 'string') {
		headers['Content-Type'] = 'application/json';
	}

	const res = await fetch(`${BACKEND_URL}${path}`, {
		method: options?.method || 'GET',
		headers,
		body: options?.body
	});

	if (!res.ok) {
		const detail = await res.text().catch(() => res.statusText);
		throw new Error(`${res.status}: ${detail}`);
	}

	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}
