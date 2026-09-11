# Upgrading the Coolify Paperclip deployment

This deployment consumes **prebuilt upstream images** from GHCR. It does not
build from source. Upgrading is changing one environment variable and
redeploying — no build, no fork rebase, no source patch to carry forward.

## Why there's no build step

Upstream's `.github/workflows/docker.yml` publishes a public, multi-arch
(amd64 + arm64) image to `ghcr.io/paperclipai/paperclip` for:

| Tag pattern | Published on | Mutable? |
|---|---|---|
| `sha-<short>` | every master commit | no — **use these** |
| `vX.Y.Z`, `X.Y.Z`, `latest` | every tagged release | `latest` moves |
| `nightly`, `beta`, `canary` | channel builds | yes, moves |

Building it ourselves on the VPS reproduced an image that already existed:
~13 minutes of CPU and a 5.06 GB local image, against a ~1.6 GB pull. The
registry is anonymous-pullable, so no GHCR credentials are needed on the VPS.

## The one rule: never point at `latest`

`latest` tracks the last tagged **release**, which lags master badly.

At the time of writing, `latest` = `v2026.831.1` ships **229** migrations,
while this deployment's database has **270** applied. Deploying `latest` here
is a schema *downgrade* against a live database.

**Before every upgrade, check the candidate image's migration count is `>=`
what the running instance has already applied.** Upstream stamps this into an
image label, readable straight from the registry without pulling:

```sh
# Read schema labels for a candidate tag, without pulling the image.
TAG=sha-d56be3f
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:paperclipai/paperclip:pull&service=ghcr.io" \
  | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
DIGEST=$(curl -s -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.index.v1+json" \
  "https://ghcr.io/v2/paperclipai/paperclip/manifests/$TAG" \
  | sed -n 's/.*"digest": *"\(sha256:[a-f0-9]*\)".*/\1/p' | head -1)
CFG=$(curl -s -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.manifest.v1+json" \
  "https://ghcr.io/v2/paperclipai/paperclip/manifests/$DIGEST" \
  | sed -n 's/.*"config":{[^}]*"digest": *"\(sha256:[a-f0-9]*\)".*/\1/p')
curl -sL -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/paperclipai/paperclip/blobs/$CFG"
# look for io.github.paperclipai.schema.migration-count / .last-migration
```

Compare against what the live instance has applied:

```sh
docker exec <paperclip-container> \
  sh -c 'ls /app/packages/db/src/migrations/*.sql | wc -l'
```

Migrations run automatically on boot and are forward-only. A newer image
applying more migrations is fine; an older image is not, and **rolling back to
an older tag after a redeploy has already migrated the DB will not work** —
restore the database from a backup instead (backups land in
`/paperclip/instances/default/data/backups`, kept 7d).

## Upgrade procedure

1. Pick a target tag — a `sha-<short>` from upstream master. Confirm its
   migration count with the snippet above.
2. In Coolify → the Paperclip app → **Environment Variables**, set
   `PAPERCLIP_IMAGE_TAG` to that tag. (Absent, it defaults to `sha-d56be3f`,
   pinned in the compose file.)
3. Redeploy. Takes ~90s (pull + restart) instead of ~13min (build).
4. Verify from the VPS, not from the Coolify status badge — a green deploy
   only means the container started:
   ```sh
   docker ps --filter name=paperclip           # Up, no restart churn
   docker logs <container> 2>&1 | tail -30     # startup banner, "Auth ready"
   curl -sI https://<domain>/api/health        # expect HTTP 200
   ```

## Rollback

Set `PAPERCLIP_IMAGE_TAG` back to the previous tag and redeploy. The old image
is still in the registry and likely still in the VPS's local image cache.

**Caveat:** this only cleanly rolls back *code*. If the newer image applied
migrations, the schema has already moved forward — see the warning above.

## What this fork branch carries

Deliberately **one file**: `docker/docker-compose.coolify.yml`. There are no
source patches, so rebasing onto newer upstream cannot conflict with
application code.

The Coolify-domain fallback that previously lived as a patch to
`scripts/docker-entrypoint.sh` is now an `entrypoint:` wrapper in the compose
file. Keeping it out of the image is what allows consuming upstream images
unmodified.

### Why the wrapper exists (don't "simplify" it away)

Coolify injects `SERVICE_FQDN_*` / `SERVICE_URL_*` / `COOLIFY_URL` as plain,
already-resolved keys at **runtime**. Compose only interpolates `${...}` at
**parse** time, before that injection — so referencing
`"${SERVICE_URL_PAPERCLIP_3100}"` as another variable's value silently yields
an empty string, and Paperclip then refuses to start with:

```
Error: authenticated public exposure requires auth.baseUrlMode=explicit
```

The wrapper reads `COOLIFY_URL` from inside the container, where the value
actually exists. Verified behaviour:

| `PAPERCLIP_PUBLIC_URL` | `COOLIFY_URL` | Result |
|---|---|---|
| unset | set | falls back to `COOLIFY_URL` ✅ |
| set | set | explicit value wins ✅ |
| unset | unset | stays empty; server exits 1 with the error above ✅ |

Two further details that will break the container if changed carelessly:

- **`tini` stays PID 1.** It matches the image's own `ENTRYPOINT` and reaps
  orphaned agent descendants. Verified: `/proc/1/comm` == `tini`.
- **`command:` must restate the image's `CMD` verbatim.** Overriding
  `entrypoint:` clears `CMD` (documented Compose behaviour); without it, tini
  starts with nothing to run and the container exits immediately.

Note that `docker exec <container> echo $PAPERCLIP_PUBLIC_URL` prints empty
even when everything is correct — `exec` bypasses the entrypoint. Read the
server process's own environment, or just trust the startup banner.
