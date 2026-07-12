FROM postgres:16.9-alpine

RUN apk add --no-cache bash coreutils su-exec tar
COPY infra/backup/entrypoint.sh /usr/local/bin/backup-entrypoint.sh
COPY infra/backup/backup.sh /usr/local/bin/backup.sh
COPY infra/backup/restore.sh /usr/local/bin/restore.sh
RUN chmod 0555 /usr/local/bin/backup-entrypoint.sh /usr/local/bin/backup.sh /usr/local/bin/restore.sh

ENTRYPOINT ["/usr/local/bin/backup-entrypoint.sh"]
CMD ["/usr/local/bin/backup.sh"]
