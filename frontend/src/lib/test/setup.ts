/**
 * Vitest setup — referenced by `setupFiles` in vitest.config.ts.
 *
 * Registers @testing-library/jest-dom's custom matchers (toBeInTheDocument,
 * toHaveTextContent, …) on Vitest's expect. The package has always been a
 * devDependency; nothing ever imported it, so `toBeInTheDocument` failed with
 * "Invalid Chai property" even once the suite could collect.
 *
 * This file's absence is why the unit suite had never executed: vitest.config.ts
 * pointed `setupFiles` at a path that existed in no commit on any branch.
 */
import '@testing-library/jest-dom/vitest';
