export async function startRecording(): Promise<MediaRecorder> {
	const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
	const recorder = new MediaRecorder(stream);
	recorder.start();
	return recorder;
}

export function stopRecording(recorder: MediaRecorder): Promise<Blob> {
	return new Promise((resolve, reject) => {
		const chunks: Blob[] = [];
		recorder.ondataavailable = (e) => {
			if (e.data.size > 0) chunks.push(e.data);
		};
		recorder.onstop = () => {
			resolve(new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' }));
		};
		recorder.onerror = () => reject(new Error('Recording failed'));
		recorder.stop();
		recorder.stream.getTracks().forEach((t) => t.stop());
	});
}
