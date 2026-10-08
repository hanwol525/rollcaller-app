# Deploy notes

RollCaller had a GKE/GCP deploy stack — 889 lines of Kubernetes manifests and a GitHub Actions
workflow — that arrived via a branch merge rather than a decision. It did not work (four
independently verified blockers), and it deployed on every push to any non-`main` branch with no
test gate. It was removed in Brief 01.

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

### 2. adapter-node needs `ORIGIN` behind any TLS-terminating proxy ⚠️ highest-value note

This is the one that will bite again. The SvelteKit frontend runs under `@sveltejs/adapter-node`,
which derives the request origin from `ORIGIN`, or else `PROTOCOL_HEADER` + `HOST_HEADER`,
defaulting to `http://` + the `Host` header.

Put it behind anything that terminates TLS and forwards plain HTTP — an ingress, a load balancer,
Cloudflare, nginx — and the Node server computes `http://your-host` while the browser sends
`Origin: https://your-host`. SvelteKit's CSRF origin check compares them and returns
**403 "Cross-site POST form submissions are forbidden" on every form action**: login, create
space, add participant, CSV import, reorder, render, pacing. The app comes up, looks healthy,
and cannot be logged into.

Set one of:

```
ORIGIN=https://your-host
# or, when the proxy sends the standard headers:
PROTOCOL_HEADER=x-forwarded-proto
HOST_HEADER=x-forwarded-host
```

> *The deleted manifest set only `BACKEND_URL` and `NODE_ENV` (`frontend-deployment.yaml:42-46`),
> and a repo-wide grep for `ORIGIN|PROTOCOL_HEADER|HOST_HEADER` returned nothing — which is why
> the deployed app was unusable.*

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

### 5. Clip storage must persist across restarts — and naive persistence deadlocks

Rendered clips and participant recordings must survive a restart, or every ceremony has to be
re-rendered. But the obvious fix deadlocks:

A `ReadWriteOnce` volume can attach to one node at a time. Under a rolling update with
`maxUnavailable: 0` / `maxSurge: 1` at `replicas: 1`, the new pod must start before the old one
drains — and it cannot attach the volume. The surge pod sits `Pending`, the incumbent never
terminates, and the rollout hangs until it times out. **Every redeploy after the first.**

Either use `Recreate` instead of `RollingUpdate`, or put storage behind a service rather than a
node-attached volume.

> *Verified at `backend-deployment.yaml:25-30` (`replicas: 1`, `maxSurge: 1`, `maxUnavailable: 0`)
> and `:229-230` (`accessModes: [ReadWriteOnce]`).*

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
branch tip is tagged on origin:

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
