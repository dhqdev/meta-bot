# Frontend do Meta-Bot: o escritório (React) servido pelo nginx, que também faz
# o proxy de /api e /ws para o backend e de /mt5/ para o painel do MetaTrader 5.
FROM --platform=$BUILDPLATFORM node:22-slim AS build
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
ENV BACKEND_UPSTREAM=http://backend:8000 \
    MT5_UPSTREAM=http://mt5:3000 \
    NGINX_RESOLVER=127.0.0.11
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/headers.conf /etc/nginx/snippets/headers.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
