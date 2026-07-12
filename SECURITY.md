# Security Policy

## Reporting

Do not open a public issue for a vulnerability or suspected data exposure. Report
it privately to the repository owner through GitHub's private vulnerability
reporting channel. Include affected revision, reproduction steps, impact and any
known mitigation. Do not include live customer data or credentials.

## Supported versions

Only the current `main` revision is supported before the first production release.
After versioned releases begin, the newest production release receives fixes.

## Operational response

For a suspected secret or PII leak: stop affected services, revoke and rotate the
credential, preserve redacted audit evidence, remove public artifacts, assess data
scope, notify the responsible owner and document corrective actions. Rewriting git
history is not a substitute for rotating an exposed secret.

## Security baseline

These are release requirements. Their current evidence status is tracked in
`docs/verification.md`; several controls remain pending and must not be represented
as production guarantees.

- Secrets remain outside git and are encrypted at rest where tenant-specific.
- Provider keys never enter browsers, prompts, logs, exports or screenshots.
- Tenant isolation is enforced in application authorization and Postgres RLS.
- Privileged reads and all admin mutations are audit logged.
- Production images are immutable and dependency/image scans gate release.
- Backups are access controlled, copied off-host and restore tested.
