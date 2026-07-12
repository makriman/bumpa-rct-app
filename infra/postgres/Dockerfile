FROM golang:1.26-alpine@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS gosu-builder

ENV CGO_ENABLED=0 \
    GOTOOLCHAIN=local

WORKDIR /src/gosu
ADD --checksum=sha256:cd9719b775dbfedae53923c9b0dc792b66d42c51e0b36652ed6f747fbadc0164 \
    https://github.com/tianon/gosu/archive/refs/tags/1.19.tar.gz /tmp/gosu.tar.gz
RUN mkdir -p /out \
    && tar -xzf /tmp/gosu.tar.gz -C /src/gosu --strip-components=1 \
    && go mod download \
    && go build -trimpath -buildvcs=false -ldflags='-s -w' -o /out/gosu . \
    && /out/gosu --version

FROM postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777 AS upstream

COPY --from=gosu-builder /out/gosu /usr/local/bin/gosu
COPY infra/postgres/init-app-role.sh /docker-entrypoint-initdb.d/10-app-role.sh
RUN chmod 0755 /usr/local/bin/gosu \
    && chmod 0555 /docker-entrypoint-initdb.d/10-app-role.sh \
    && gosu postgres id >/dev/null

# Re-materialising the final filesystem removes the vulnerable upstream gosu
# binary from the distributable layers as well as from the runtime filesystem.
FROM scratch AS runtime

COPY --from=upstream / /

ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    GOSU_VERSION=1.19 \
    LANG=en_US.utf8 \
    PG_MAJOR=16 \
    PG_VERSION=16.14 \
    PGDATA=/var/lib/postgresql/data

LABEL org.opencontainers.image.title="BumpaBestie PostgreSQL" \
      org.opencontainers.image.description="PostgreSQL 16.14 with gosu rebuilt using patched Go 1.26.5" \
      org.opencontainers.image.licenses="PostgreSQL" \
      org.opencontainers.image.source="https://github.com/makriman/bumpa-rct-app"

ENTRYPOINT ["docker-entrypoint.sh"]
STOPSIGNAL SIGINT
EXPOSE 5432
VOLUME ["/var/lib/postgresql/data"]
CMD ["postgres"]

FROM runtime AS backup

RUN apk add --no-cache \
        coreutils=9.11-r0 \
        tar=1.35-r5

COPY infra/backup/entrypoint.sh /usr/local/bin/backup-entrypoint.sh
COPY infra/backup/backup.sh /usr/local/bin/backup.sh
COPY infra/backup/restore.sh /usr/local/bin/restore.sh
RUN chmod 0555 \
    /usr/local/bin/backup-entrypoint.sh \
    /usr/local/bin/backup.sh \
    /usr/local/bin/restore.sh

ENTRYPOINT ["/usr/local/bin/backup-entrypoint.sh"]
CMD ["/usr/local/bin/backup.sh"]
