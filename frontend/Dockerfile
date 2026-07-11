# RollCaller frontend — SvelteKit SSR via adapter-node.
#
# Multi-stage: build with Node, then copy only .output into a slim runtime.
# The server reads BACKEND_URL from env (set in the k8s Deployment to the
# in-cluster Service URL).
FROM node:20-slim AS builder
WORKDIR /app

COPY package.json package-lock.json* ./
# --ignore-scripts skips the prepare script (svelte-kit sync) which needs
# svelte.config.js — not copied yet. Sync runs during `npm run build` instead.
RUN npm install --ignore-scripts

COPY . .
RUN npm run build
# Debug: find where the adapter put the output
RUN ls -la /app/build/ 2>/dev/null && ls -la /app/.output/ 2>/dev/null || true
RUN find /app -maxdepth 3 -name "index.js" -path "*/server/*" 2>/dev/null || true

FROM node:20-slim
WORKDIR /app

COPY --from=builder /app/build/ ./build/

EXPOSE 3000
ENV NODE_ENV=production
ENV PORT=3000

CMD ["node", "build/server/index.js"]
