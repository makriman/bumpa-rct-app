FROM golang:1.26-alpine@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS builder

ENV CGO_ENABLED=0 \
    GOTOOLCHAIN=local

WORKDIR /src/caddy
ADD --checksum=sha256:33777097f666d60d78bfb74df06978c933f32aa5a0d4ce0b0c5d028489984187 \
    https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_buildable-artifact.tar.gz \
    /tmp/caddy.tar.gz
RUN mkdir -p /out \
    && tar -xzf /tmp/caddy.tar.gz -C /src/caddy \
    && go build -mod=vendor -trimpath -buildvcs=false \
      -tags=nobadger,nomysql,nopgx \
      -ldflags='-s -w' \
      -o /out/caddy . \
    && /out/caddy version

FROM alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40 AS runtime

RUN apk add --no-cache \
        ca-certificates=20260611-r0 \
        libcap=2.78-r0 \
        mailcap=2.1.54-r0 \
    && addgroup -S -g 10001 caddy \
    && adduser -S -D -H -u 10001 -G caddy caddy \
    && install -d -m 0755 -o caddy -g caddy /config /data /srv \
    && install -d -m 0755 /etc/caddy

COPY --from=builder /out/caddy /usr/bin/caddy
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
RUN setcap cap_net_bind_service=+ep /usr/bin/caddy \
    && caddy version

ENV CADDY_VERSION=v2.11.4 \
    XDG_CONFIG_HOME=/config \
    XDG_DATA_HOME=/data

LABEL org.opencontainers.image.title="BumpaBestie Caddy" \
      org.opencontainers.image.description="Caddy 2.11.4 rebuilt with patched Go 1.26.5" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/makriman/bumpa-rct-app"

WORKDIR /srv
EXPOSE 80 443 443/udp 2019
VOLUME ["/data", "/config"]
USER 10001:10001
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
