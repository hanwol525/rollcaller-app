export type ParticipantStatus = 'invited' | 'recorded' | 'confirmed';

export interface ParticipantSelf {
	id: number;
	name: string;
	space_name: string;
	status: ParticipantStatus;
	ipa_text: string | null;
	ipa_confirmed: boolean;
}

export interface CeremonyItem {
	position: number;
	name: string;
	clip_url: string | null;
	ipa_text: string | null;
	ipa_source: string | null;
}

export interface CeremonyData {
	space_id: number;
	space_name: string;
	advanced_seconds: number;
	roster: CeremonyItem[];
}

export async function getSelf(token: string): Promise<ParticipantSelf> {
	const res = await fetch(`/invite/${token}`);
	if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
	return res.json();
}

export async function uploadRecording(token: string, blob: Blob): Promise<ParticipantSelf> {
	const formData = new FormData();
	formData.append('file', blob);
	const res = await fetch(`/invite/${token}/recording`, { method: 'POST', body: formData });
	if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
	return res.json();
}

export async function previewIpa(token: string, ipa: string): Promise<Blob> {
	const res = await fetch(`/invite/${token}/ipa/preview`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ ipa })
	});
	if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
	return res.blob();
}

export async function confirmIpa(
	token: string,
	ipa: string,
	isEdit: boolean
): Promise<ParticipantSelf> {
	const res = await fetch(`/invite/${token}/ipa/confirm`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ ipa, is_edit: isEdit })
	});
	if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
	return res.json();
}

export async function logout(): Promise<void> {
	await fetch('/auth/logout', { method: 'POST' });
}
