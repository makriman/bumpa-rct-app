# Stable production promotion coordinator

Production releases start through the root-owned launcher installed by
`scripts/bootstrap_server.sh`:

```bash
sudo -u bumpabestie /usr/local/sbin/bumpabestie-promote \
  <revision> <infra-image-tag> <api-digest> <web-digest> <caddy-digest> \
  <postgres-digest> <backup-digest> <hermes-digest>
```

Do not invoke `scripts/promote_release.sh` or `scripts/deploy.sh` directly. The
stable launcher acquires `/var/lib/bumpabestie/maintenance.lock` before reading
the mutable checkout or `.env.production`, then hands the verified lock descriptor
to a private worker bundle extracted from the reviewed `origin/main` target; it
never executes promotion code from the prior mutable checkout. It records a
private, fsync-backed coordinator journal before that handoff. A crash or boundary mismatch leaves the journal and the
`maintenance-required` interlock in place; future promotions and the scheduled
backup fail closed until an operator reconciles the checkout, release record,
environment pointers, running image digests, and database migration state.

Successful `COMMITTED`, exact `PREVIOUS_RESTORED`, and verified
`HYBRID_PERSISTED` terminal journals are retained with mode `0600` under
`/var/lib/bumpabestie/promotion-history`. The installed launcher is root-owned so
a Git checkout cannot replace the process that owns locking and crash detection.
