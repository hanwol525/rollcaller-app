# RollCaller frontend — SvelteKit SSR via adapter-node.
#
# Multi-stage: build with Node, then copy only .output into a slim runtime.
# The server reads BACKEND_URL from env (set in the k8s Deployment to the
# in-cluster Service URL).
FROM node:20-slim AS builder
WORKDIR /app

COPY package.json package-lock.json* ./
# npm install (not ci) so the lock file regenerates with adapter-node
RUN npm install

COPY . .
RUN npm run build

FROM node:20-slim
WORKDIR /app

COPY --from=builder /app/.output ./.output

EXPOSE 3000
ENV NODE_ENV=production
ENV PORT=3000

CMD ["node", ".output/server/index.js"]
