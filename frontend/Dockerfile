# RollCaller frontend — runs the Vite dev server (the app's known-good config),
# with the backend URL injected via BACKEND_URL. Serves on 5173.
FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm install

COPY . .

EXPOSE 5173
# --host binds to 0.0.0.0 so the container is reachable from the host.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]