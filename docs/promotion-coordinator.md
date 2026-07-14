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
private, fsync-backed coordinator journal before that handoff. The journal includes
the prior non-secret auth selectors and a hash of the environment canonicalized to
the recorded image/auth boundary; it stores only the non-secret selected verifier
path, never the PIN, verifier bytes or a verifier fingerprint. This lets a
same-digest configuration promotion prove that rollback restored disabled login,
not merely the old image pointers. A crash or boundary mismatch leaves the journal and the
`maintenance-required` interlock in place; future promotions and the scheduled
backup fail closed until an operator reconciles the checkout, release record,
environment pointers, running image digests, and database migration state.

If target validation, fetch, or worker-bundle extraction fails before the target
child starts, the stable coordinator does not wait for target code to repair the
staged environment. It renders the recorded prior image/auth boundary to a private
same-directory file, preserves ownership and mode, fsyncs it, atomically replaces
`.env.production`, and only archives `PREVIOUS_RESTORED` after the complete prior
environment and release-record hashes match. An incomplete restore instead leaves
the maintenance interlock in place.

Successful `COMMITTED`, exact `PREVIOUS_RESTORED`, and verified
`HYBRID_PERSISTED` terminal journals are retained with mode `0600` under
`/var/lib/bumpabestie/promotion-history`. The installed launcher is root-owned so
a Git checkout cannot replace the process that owns locking and crash detection.
