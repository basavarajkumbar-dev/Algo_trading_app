# GAINZALGO PRO MAX — frontend image
#
# Phase 1 scope: single-stage image running `next start` against a
# production build, good enough for local Docker Compose usage. A
# multi-stage build trimming devDependencies from the final image is a
# reasonable later optimization, not required for this phase.

FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

# NEXT_PUBLIC_* values must be present at build time because Next.js
# inlines them into the client bundle. The docker-compose build args
# below wire this through from .env.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
