/**
 * Ambient module declarations for .svelte imports in test files.
 *
 * VS Code's TS server doesn't resolve .svelte imports from src/tests/
 * the way svelte-check does. These declarations silence the red squiggles.
 */

declare module '*.svelte' {
	import type { Component } from 'svelte';
	const component: Component<Record<string, any>>;
	export default component;
}