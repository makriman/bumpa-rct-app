FROM golang:1.27.3-alpine@sha256:b8c9b82affbbb82a020bbcf998dad2ba9516944ac7e883524642a481a9505e9c AS builder

ENV CGO_ENABLED=0 \
    GOTOOLCHAIN=local

WORKDIR /src/caddy
ADD --checksum=sha256:b430516910839fbaf35c0a9e9df80d1e2e30aa792530293c39f4a97a1b2c9060 \
    https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_buildable-artifact.tar.gz \
    /tmp/caddy.tar.gz
RUN mkdir -p /out \
    && tar -xzf /tmp/caddy.tar.gz -C /src/caddy \
    && go get google.golang.org/grpc@v1.84.0 golang.org/x/net@v0.61.0 golang.org/x/crypto@v0.58.0 \
    && go mod vendor \
    && go build -mod=vendor -trimpath -buildvcs=false \
      -tags=nobadger,nomysql,nopgx \
      -ldflags='-s -w' \
      -o /out/caddy . \
    && go version -m /out/caddy \
      | grep -F 'google.golang.org/grpc' \
      | grep -F 'v1.84.0' \
    && /out/caddy version

FROM alpine:3.24@sha256:71e9b7bae160ba167fa17414401b94cbcdaf3c32feeab9298bd794a2a0614c35 AS runtime

RUN apk add --no-cache \
        ca-certificates=20261020-r0 \
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

ENV CADDY_VERSION=v2.11.7 \
    XDG_CONFIG_HOME=/config \
    XDG_DATA_HOME=/data

LABEL org.opencontainers.image.title="BumpaBestie Caddy" \
      org.opencontainers.image.description="Caddy 2.11.7 rebuilt with patched Go 1.27.3 and gRPC-Go 1.84.0" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/makriman/bumpa-rct-app"

WORKDIR /srv
EXPOSE 80 443 443/udp 2019
VOLUME ["/data", "/config"]
USER 10001:10001
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
