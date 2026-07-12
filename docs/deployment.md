# Deployment

Production deployment is intentionally gated until the Droplet and live provider
configuration are available. Local success is not production evidence.

## Host prerequisites

- Ubuntu 24.04 LTS on `linux/amd64`, reserved IP and provider backups enabled.
- Cloud firewall: public TCP 80/443; SSH 22 only from a trusted `/32` or VPN CIDR.
- SSH keys only, non-root `bumpabestie` deploy user and `/opt/bumpabestie` mode 0750.
- DNS A/AAAA records for public, API, admin and research hosts.
- Encrypted off-host backup target and alert destination.

Run `ADMIN_SSH_CIDR=203.0.113.10/32 sudo ./scripts/bootstrap_server.sh` once on a
fresh supported host. Review the script before execution.

## Production environment

Create `/opt/bumpabestie/.env.production` directly on the server with mode `0600`.
Start from `.env.example`, remove development OTP controls, generate independent
high-entropy secrets, set HTTPS origins/domains, `CADDY_BIND_ADDRESS=0.0.0.0`, ports
80/443, non-mock provider backends, immutable `IMAGE_TAG` and immutable `DEPLOY_REF`.
Do not copy local `.env` or store product secrets in GitHub Actions.

Validate without printing values:

```bash
./scripts/validate_env.sh .env.production production
docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml config --quiet
```

## Image release

Pull-request CI builds `linux/amd64` API and web images without publishing them.
`.github/workflows/publish-images.yml` publishes both images to GHCR when a `v*`
tag is pushed or an authorized operator dispatches it manually. Its deployable tag
is always `sha-<full-commit-sha>`; release tags are convenience aliases only and
`latest` is never emitted. The job has read-only repository access plus
`packages:write`, and emits provenance/SBOM. Image vulnerability scanning is still
a pending release control.

The web app now uses a same-origin `/api/backend` proxy and resolves the internal API
origin at runtime, so API routing no longer depends on a compiled public origin.
`NEXT_PUBLIC_DEMO_MODE` is enabled only by the local Compose build argument. The
Dockerfile defaults it off, release images use that secure default, and middleware
validates privileged sessions and platform roles against FastAPI. A published image
is still a release candidate—not production evidence—until live-provider and
infrastructure gates pass.

## Deploy sequence

1. Confirm CI, live-provider canaries, backup freshness and free disk.
2. Set `DEPLOY_REF` to the desired commit/tag and `IMAGE_TAG` to
   `sha-<resolved-full-commit-sha>` in `.env.production`.
3. Run a pre-deploy backup and verify its checksum/off-host copy.
4. Run `./scripts/deploy.sh` as the deploy user.
5. The script pulls images, runs the one-shot migration, updates services and checks
   all public surfaces over HTTPS.
6. Record the revision/digests and complete the production verification ledger.

Database migrations must be backward compatible with the previous app during the
rollout. Destructive changes use expand/migrate/contract across separate releases.
Application rollback does not automatically reverse a database migration.

## GitHub controls before deployment

Enable secret scanning and push protection, Dependabot security updates, private
vulnerability reporting and protected `main`. Require all CI jobs, one review,
conversation resolution, linear history and protection against force-push/deletion.
Restrict the image-publish workflow/tag creation to trusted maintainers. Deployment
will use a protected GitHub Environment with manual approval and a least-privilege
deploy credential when the Droplet phase begins; there is no remote deploy workflow
yet.
