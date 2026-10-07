# Consolidation — one source of truth

**Brief 00a · 2026-10-07 · completed**

`main` now equals the consolidated app: the full backend from the old `main` plus the
frontend data layer, deploy infrastructure and performance work from `ci-cd-gke-deploy`.
Nothing was deleted. Every pre-merge branch tip is preserved as an `archive/*` tag on origin.

| | Before | After |
|---|---|---|
| `main` tip | `0157986` ("setup") | `af32d25` |
| Commits on `main` | 46 | 65 |
| Frontend builds? | ❌ no — `$lib` missing | ✅ yes — `npm run build` emits `build/index.js` |
| `svelte-check` | 19 errors, 1 warning | 4 errors, 1 warning |
| Backend tests | 113 pass / 11 fail / 4 skip | 113 pass / 11 fail / 4 skip (unchanged) |

---

## The topology, corrected

Brief 00a was written on the premise that `main` was a 1-commit orphan stub whose history was
disjoint from the real app, and proposed a force-push to promote `ci-cd-gke-deploy` over it.
Re-derived from live `origin` before anything was executed, that premise did not hold:

| Brief 00a premise | Measured |
|---|---|
| `main` is a 1-commit orphan stub | **46 commits** (`git rev-list --count origin/main`) |
| Histories are disjoint | **One root commit** for the whole repo: `ee728fc` (2026-07-06) |
| `main` is not an ancestor of any feature branch | `main` and `ci-cd-gke-deploy` **share merge-base `0cde241`** |
| `origin/fixes` has 17 unique commits | **0 ahead of `main`** — the 17 figure was measured against `ci-cd-gke-deploy`, not `main` |
| `ci-cd-gke-deploy` is the real app (a superset) | **17 ahead / 20 behind** `main` — neither branch contained the other |

The tip of `main` was *named* `setup`, which reads like an initial scaffold. It was in fact a
794-line `package-lock.json` refresh committed 2026-10-06 — the likely source of the misreading.

**Two consequences.** No force-push was possible or needed; the branches share an ancestor, so
this was an ordinary merge. And promoting `ci-cd-gke-deploy` would have silently reverted the
audio-to-IPA recognizer from **wav2vec2** back to **Allosaurus** — commit `e4418f0`
("allosaurus replacement") exists only on `main`'s line and `ci-cd-gke-deploy` never received it.

## Archive tags

All eight pushed to origin **before** any ref moved. The pre-merge state is fully recoverable.

| Tag | Commit | Was |
|---|---|---|
| `archive/main-pre-merge` | `0157986` | `main` before consolidation |
| `archive/ci-cd-gke-deploy` | `e458469` | the merged-in branch |
| `archive/backend` | `413531c` | |
| `archive/frontend` | `acb9ef4` | |
| `archive/fixes` | `e777f6d` | |
| `archive/deployment` | `14abc32` | |
| `archive/gemma-fixes` | `e4418f0` | |
| `archive/hatred` | `ee98251` | |

Named `main-pre-merge` rather than Brief 00a's `main-stub`, because it was not a stub.

## Branch fates

No branch was deleted. All eight remain on origin.

| Branch | Commits | Unique vs new `main` | Fate |
|---|---|---|---|
| `main` | 65 | — | **is now the source of truth** |
| `ci-cd-gke-deploy` | 43 | 0 | fully merged — keep as archive, safe to delete later |
| `backend` | 6 | 0 | merged long ago — archive |
| `frontend` | 23 | 0 | merged long ago — archive |
| `gemma-fixes` | 27 | 0 | merged long ago — archive |
| `deployment` | 29 | 0 | merged long ago — archive |
| `fixes` | 43 | 0 | merged long ago — archive |
| `hatred` | 4 | 0 | vent/experiment branch — archive |

Local `big-refactor` and `model-upgrade-test` sit at `cf01207`, one commit behind the old
`main` and now 20+ behind. Neither holds unique work. `refactor` was recreated from the new
`main`.

## Conflict resolutions

Only five files changed on both sides of `0cde241`; `backend/app/main.py` auto-merged, leaving
four to resolve by hand.

| File | Resolution | Why |
|---|---|---|
| `.gitignore` | Union of both | Keeps `ci-cd`'s `/lib/` anchor **and** `main`'s `backend-app-dump/`. |
| `backend/Dockerfile` | `ci-cd`'s header comment + `main`'s multi-line `pip install` | Same resulting image; `ci-cd`'s comment documents *why* Python 3.12 (kokoro requires <3.13). |
| `frontend/Dockerfile` | **`ci-cd`'s multi-stage build, verbatim** | `main`'s ran `npm run dev` as production. This is the real build, and it is the version that was actually deployed. Taken unmodified rather than inventing an untested third variant. |
| `frontend/vite.config.ts` | `main`'s `'/auth': backend` | `ci-cd` had hardcoded `http://localhost:8000`, which would have ignored `BACKEND_URL`. |

### The `.gitignore` fix matters most

`.gitignore:17` was `lib/` — an unanchored pattern from the standard GitHub **Python**
template, sitting at the root of a polyglot repo. Git applies such patterns at any depth, so
it silently excluded `frontend/src/lib/`, the frontend's entire data layer, from version
control. The merge takes `ci-cd`'s anchored `/lib/`, and those four files are now git-tracked
for the first time:

```
$ git ls-files frontend/src/lib/
frontend/src/lib/api.ts
frontend/src/lib/assets/favicon.svg
frontend/src/lib/recorder.ts
frontend/src/lib/server/index.ts
```

Credit where due: commit `d8e054b` (jsilve12@umich.edu, 2026-07-10) diagnosed this
independently and wrote both the reconstruction and the `.gitignore` fix.

## Verification on the merged tree

| Check | Result |
|---|---|
| Backend imports, routes register | ✅ 24 routes |
| `GET /health` | ✅ `{"ok":true}` |
| `pytest app/tests` | 113 passed, 11 failed, 4 skipped — **identical to pre-merge** |
| `$lib` symbol resolution | ✅ every imported symbol is exported |
| `svelte-check` | 19 errors → **4**; zero `$lib` errors remain |
| `npm run build` | ✅ succeeds, emits `build/index.js` |
| `recognize.py` | ✅ `facebook/wav2vec2-lv-60-espeak-cv-ft` — no regression |

The 11 backend failures are environmental, not defects: the local venv is a deliberately
slimmed Intel-Mac install without `kokoro`. All 11 are `No module named 'kokoro'`. The 4 skips
are `ffmpeg not installed`. The count is unchanged from before the merge.

One follow-up commit was needed: the merge took `ci-cd`'s `package.json` (which adds
`@sveltejs/adapter-node`) alongside `main`'s newer `package-lock.json`, which had no entry for
it. `npm install` reconciled them — committed separately as `af32d25`.

## Known gaps carried forward

Recorded here, deliberately **not fixed** — this brief moves refs; code changes belong to
later briefs.

1. **Frontend port mismatch.** `frontend/Dockerfile` now serves on **3000** (adapter-node),
   while `docker-compose.yml` still maps `5173:5173`. Docker was not running locally, so this
   is unverified by execution. Carried into the audit's risk section.
2. **Frontend test suite still cannot run.** `vitest.config.ts` references
   `src/lib/test/setup.ts` and `src/lib/test/app-environment.ts`, which exist on **no branch**
   — they were never committed anywhere. The 4 remaining `svelte-check` errors
   (`toBeInTheDocument` typings) are the same root cause. This predates the merge.
3. **Deploy infrastructure is in the tree, undecided.** The merge brought in `infra/k8s/`
   (5 manifests, 490 lines) and `.github/workflows/deploy.yml` (399 lines), because that is
   what a merge does — **not as an endorsement**. They are inert: the workflow's trigger is
   `branches-ignore: [main]`, and it requires GCP Secret Manager entries that are not
   configured. It targets a collaborator's infrastructure (`personal-project-289714`,
   `rollcall.jonathansilverstein.us`). Adopting or removing this is your call; removing it is
   one commit.

   ⚠️ **Operational note:** because the workflow triggers on pushes to *any branch except
   `main`*, pushing a feature branch that touches `backend/**`, `frontend/**` or
   `infra/k8s/**` will attempt a real deploy. This is why the consolidation was merged
   locally and only `main` was pushed.

## What was deliberately not changed

Per Brief 00a's Scope OUT: no refactoring, renaming, or cleanup. Still present on `main` and
left for later briefs — the `GEMMA_*` naming on a provider-agnostic layer, the README prize
pitch, `backend/rollcaller.db`, `backend/test.py`, `Fireworks-Compute.png`, `terminal.png`,
the duplicated `.env.example` / `.dockerignore` files, and the second `backend/docker-compose.yml`.

The **g2p dead path survives intentionally** — it is present on every branch and is an audit
finding, not a consolidation artifact. `espeak_ipa()` checks `if _backend is not None` but
nothing ever calls `warm()` in production, so the eSpeak floor never runs and `g2p()` returns
pseudo-IPA like `/firstname_lastname/`.
