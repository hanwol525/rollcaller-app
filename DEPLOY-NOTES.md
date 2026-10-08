# Deploy notes

RollCaller had a GKE/GCP deploy stack — 889 lines of Kubernetes manifests and a GitHub Actions
workflow — that arrived via a branch merge rather than a decision. It was removed in Brief 01
because it targeted a **collaborator's** GCP project and domain, and because it deployed to that
live cluster on every push to any non-`main` branch (filtered to `backend/**`, `frontend/**`,
`infra/k8s/**`) with no test, lint or typecheck gate of any kind.

> **Correction.** An earlier version of this file, and `AUDIT.md` §8.1, said the stack "did not
> work (four independently verified blockers)". **That was wrong on all four.** The run history
> settles it: the deploy workflow's last five runs on `ci-cd-gke-deploy` all **succeeded**
> (`29135302294`, `29136165863`, `29136485375`, `29141801852`, `29142302682` — through the branch
> tip `e458469`). The stack deployed fine. The four "blockers" were produced by a sweep agent and
> never went through the audit's refutation pass; when they finally did, one was disproven
> outright and three were narrowed to latent defects (see §5 and the notes below). Removing the
> stack was still the right call — someone else's infrastructure, auto-deployed from every
> branch, unverified — but "it is broken" was not the reason.

**Choosing a deploy target is an open decision.** Nothing here recommends one.

This file exists because deleting that stack would otherwise throw away five things that were
expensive to learn. They are constraints on *any* future deploy, not GKE trivia.

---

## Operational facts worth keeping

### 1. Python 3.12, not 3.13

`kokoro==0.8.4` requires Python `<3.13`. Any runtime, base image or venv choice is constrained
by this.

> *Verified at `backend/Dockerfile:3-6`:*
> `# 3.12 (not 3.13) because kokoro==0.8.4 requires Python <3.13.` → `FROM python:3.12-slim`

### 2. Set `ORIGIN` explicitly — adapter-node assumes HTTPS, so plain-HTTP deploys 403

The SvelteKit frontend runs under `@sveltejs/adapter-node`. It resolves the request origin from
`ORIGIN` if set; otherwise it builds one from `PROTOCOL_HEADER` + `HOST_HEADER`, and **when
neither header var is configured it defaults the protocol to `https`** and takes the host from
the `Host` header.

Verified in the installed adapter (`@sveltejs/adapter-node@5.5.7`,
`files/handler.js:208-211`):

```js
function get_origin(headers) {
    const protocol = decodeURIComponent(
        normalise_header(protocol_header, headers[protocol_header]) || 'https'
    );
```

The consequence runs the opposite way from what you might expect:

| Serving over | Browser sends | Server computes | Result |
|---|---|---|---|
| HTTPS behind a TLS-terminating proxy | `Origin: https://host` | `https://host` | ✅ matches — works with nothing set |
| **Plain HTTP** (local Docker, `http://localhost:3000`, a bare container) | `Origin: http://host` | `https://host` | 🔴 **mismatch → 403** |

On a mismatch SvelteKit's CSRF check returns **403 "Cross-site POST form submissions are
forbidden"** on *every* form action — login, create space, add participant, CSV import, reorder,
render, pacing. The app comes up, looks healthy, and cannot be logged into.

So the risk is **plain-HTTP deployments**, not TLS ones. Set it explicitly either way rather than
relying on the default:

```
ORIGIN=https://your-host          # or http://localhost:3000 for a plain-HTTP run
# or, when a proxy sends the standard headers:
PROTOCOL_HEADER=x-forwarded-proto
HOST_HEADER=x-forwarded-host
```

> ⚠️ **Correction.** An earlier version of this note, and `AUDIT.md` §4.2, claimed the default
> was `http` and that the *GKE* deployment therefore 403'd on every form action — listed as a P0
> and as the first of Brief 01's blockers. That was wrong: the ingress terminated TLS, so the
> computed origin matched and login would have worked. The finding came from a sweep agent and
> never went through the audit's refutation pass. Removing the stack was still correct — it
> targeted a collaborator's cluster and auto-deployed from every branch — but this particular
> reason was not.

### 3. Ceremony render exceeds a 60s proxy default

"Prep Clips" synthesizes a clip per participant. On a real roster this runs well past the 60s
default read timeout most proxies ship with, and the request 504s mid-render. The ingress had to
raise connect/send/read timeouts to **120s**.

> *Verified at `backend-deployment.yaml:273-275` — all three set to `"120"`. Added by commit
> `81df906`: "bump nginx proxy timeouts to 120s (default 60s causes 504 on ceremony render)".*

Note the render is also all-or-nothing: one participant's failure 500s the whole batch after
blocking on every remaining render. A long timeout is a workaround, not a fix.

### 4. Frontend entry point is `build/index.js`

adapter-node v5 emits `build/`, not `.output/`, and the entry is `build/index.js` — **not**
`build/server/index.js`. Two commits were burned rediscovering this.

> *`frontend/Dockerfile:26` → `CMD ["node", "build/index.js"]`. See commits `16057c6` ("build
> output is build/ not .output/") and `8ed338e` ("entry point is build/index.js").*

### 5. Clip storage must persist across restarts — and the obvious setup is luck-dependent

Rendered clips and participant recordings must survive a restart, or every ceremony has to be
re-rendered.

The deleted stack solved this with a MinIO sidecar on a `ReadWriteOnce` PVC
(`backend-deployment.yaml:229-230`) under `RollingUpdate` / `maxSurge: 1` / `maxUnavailable: 0`
at `replicas: 1` (`:25-30`). During a rolling update the surge pod starts before the incumbent
drains, so **two pods briefly want the same volume.**

That worked — but only because of where the scheduler happened to put them. `ReadWriteOnce` is a
**per-node** constraint, not per-pod: Kubernetes explicitly allows multiple pods to mount an RWO
volume when they are co-scheduled on the same node (`ReadWriteOncePod` is the per-pod mode). The
pod template sets no `nodeSelector`, `affinity` or `topologySpreadConstraints`, so nothing pins
that co-location — it just happened on every recorded redeploy, each completing `kubectl rollout
status` in 46–83s against a 900s timeout.

Two real risks remain for any future design:

- **If the surge pod lands on a different node**, it cannot attach the volume, sits `Pending`,
  and the rollout stalls until the timeout. Nothing in the manifest prevents this.
- **During the surge window two MinIO servers run against one volume**, which is not a
  supported MinIO configuration regardless of whether the mount succeeds.

Use `strategy: Recreate`, or put storage behind its own service/StatefulSet rather than
sidecar-attaching a node-local volume to a rolling Deployment.

> ⚠️ An earlier version of this note said this "deadlocks every redeploy after the first."
> That was false — three recorded redeploys with an incumbent holding the volume all succeeded.
> It is a latent scheduling hazard, not a blocker.

**Pin the storage image.** `minio/minio:latest` (`backend-deployment.yaml:177`) was the only
unpinned image in an otherwise pinned manifest — and it held all participant audio.

---

## Running it locally

The root `docker-compose.yml` is the only compose file (the second one, `backend/docker-compose.yml`,
was deleted — it never set `STORAGE_BACKEND`, so its MinIO service received zero bytes).

**You must create `.env` first.** The root compose declares `env_file: - .env`, which Compose
treats as required by default, so the stack errors out before starting if it is absent. Only
`.env.example` is committed:

```bash
cp .env.example .env
docker compose up --build
```

Two known gaps, recorded in `AUDIT.md` and not yet fixed:

- `frontend/Dockerfile` serves on **3000** (adapter-node) while `docker-compose.yml` still maps
  `5173:5173`.
- No compose file starts MinIO any more, so the `minio` storage backend has no local exercise
  path. The `filesystem` backend is the default and works.

---

## Recovering any of this

Nothing was lost. The full deploy stack lives on `origin/ci-cd-gke-deploy`, and every pre-merge
branch tip is tagged on origin.

> 🔴 **Restore paths from that ref, never the ref itself.** `ci-cd-gke-deploy` is 26 commits
> behind `main` and never received `e4418f0`, the Allosaurus → wav2vec2 recognizer migration —
> it still pins `allosaurus==1.0.2`. A checkout, merge or cherry-pick of the *branch* silently
> reverts audio-to-IPA recognition. A path-scoped `git checkout <tag> -- infra/k8s/` is safe.

| Tag | Commit | Was |
|---|---|---|
| `archive/ci-cd-gke-deploy` | `e458469` | the GKE stack removed here |
| `archive/main-pre-merge` | `0157986` | `main` before consolidation |
| `archive/backend` | `413531c` | |
| `archive/frontend` | `acb9ef4` | |
| `archive/fixes` | `e777f6d` | |
| `archive/deployment` | `14abc32` | |
| `archive/gemma-fixes` | `e4418f0` | |
| `archive/hatred` | `ee98251` | |

```bash
git show archive/ci-cd-gke-deploy:infra/k8s/backend-deployment.yaml
git checkout archive/ci-cd-gke-deploy -- infra/k8s/   # restore wholesale
```

### How the consolidation merge was settled

Branch topology and commit tips are all reconstructable from git. These four judgement calls are
not — they are why the tree looks the way it does:

- **`frontend/Dockerfile`** — took `ci-cd-gke-deploy`'s multi-stage adapter-node build verbatim.
  `main`'s ran `npm run dev` as production. The multi-stage build is the one that was actually
  deployed, so it was taken unmodified rather than inventing an untested third variant.
- **`frontend/vite.config.ts`** — took `main`'s `'/auth': backend`. `ci-cd-gke-deploy` had
  hardcoded `http://localhost:8000`, which would have ignored `BACKEND_URL`.
- **`backend/Dockerfile`** — `ci-cd-gke-deploy`'s header comment (it documents *why* 3.12) plus
  `main`'s multi-line `pip install`. Same resulting image.
- **`.gitignore`** — union of both, keeping `ci-cd-gke-deploy`'s anchored `/lib/`. The unanchored
  `lib/` inherited from the GitHub Python template had silently excluded `frontend/src/lib/` —
  the frontend's entire data layer — from version control.
- **`backend/app/main.py`** — auto-merged cleanly; no judgement call was needed.
