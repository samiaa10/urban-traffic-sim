# The Norwich Urban Traffic Simulator frontend: built by Vite, served by nginx. Build from
# the frontend folder:
#
#     docker build -t norwich-sim-frontend frontend
#
# nginx also forwards /api/... to the backend (see nginx.conf), so the
# browser only ever talks to this one address.

# ---- Build the static site ----
FROM node:22-alpine AS build

WORKDIR /app

# Exactly what package-lock.json says (cached until it changes)
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Where the built site sends API requests. Vite copies it into the
# JavaScript at build time. "/api" = the same address, via nginx.
ARG VITE_API_URL=/api
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build


# ---- Serve it ----
# The unprivileged nginx image runs as a normal user and listens on 8080
FROM nginxinc/nginx-unprivileged:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
