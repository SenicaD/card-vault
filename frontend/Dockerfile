FROM node:22-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# Nuxt bakes the /api and /images proxy targets in at build time
ARG NUXT_API_BASE=http://backend:8000
ENV NUXT_API_BASE=${NUXT_API_BASE}
RUN npm run build

FROM node:22-slim
WORKDIR /app
COPY --from=build /app/.output ./.output
ENV NITRO_HOST=0.0.0.0 NITRO_PORT=3000 NODE_ENV=production
USER node
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
