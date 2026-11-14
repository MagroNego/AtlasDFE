FROM node:24-bookworm-slim AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG API_ORIGIN=http://api:8080
ENV API_ORIGIN=$API_ORIGIN NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /src/.next/standalone/ ./
COPY --from=build --chown=node:node /src/.next/static/ ./.next/static/
USER node
CMD ["node", "server.js"]
