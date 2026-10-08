/**
 * Test stub for SvelteKit's `$app/environment` virtual module.
 *
 * vitest.config.ts aliases `$app/environment` here because the real module is
 * synthesized by the SvelteKit Vite plugin, which the standalone test config
 * deliberately does not load (see the header comment in vitest.config.ts).
 *
 * Mirrors the real module's surface exactly — `browser`, `dev`, `building`,
 * `version` (node_modules/@sveltejs/kit/src/runtime/app/environment/types.d.ts).
 *
 * Nothing imports `$app/environment` today, so this is hardening rather than a
 * fix: Vite resolves aliases lazily, and an alias pointing at a missing file
 * only fails once something actually imports it. It exists so that the first
 * component to need it does not resurrect the same class of breakage.
 */

/** Tests run in jsdom, which is a browser-like environment. */
export const browser = true;

/** Tests are never a production build. */
export const dev = true;

/** True only during `vite build`'s prerender pass; never in a test. */
export const building = false;

/** Real value is the build timestamp; tests only need it to be a string. */
export const version = 'test';
