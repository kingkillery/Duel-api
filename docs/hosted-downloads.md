# Hosted downloads

Public installs come from a Cloudflare R2 bucket — static file hosting
with no egress fees and no server to maintain. Nothing in this path
touches the home network: GitHub CI uploads with a scoped API token, and
users download straight from Cloudflare's edge.

## For users (once the bucket is live)

```bash
pip install --extra-index-url https://<public-host>/simple/ duel-api
```

A specific version, straight from the file:

```bash
pip install https://<public-host>/dist/duel-api-0.2.1-py3-none-any.whl
```

Still supported as a fallback: `uvx --from git+https://github.com/kingkillery/Duel-api duel-api doctor`.

> Status: automation is merged (`.github/workflows/hosted-index.yml`);
> the bucket itself is pending the maintainer setup below. Until then,
> use the git+https fallback.

## For the maintainer: bucket setup (~10 minutes)

1. Cloudflare dashboard → **R2 Object Storage** → **Create bucket**
   (e.g. `duel-api-dl`).
2. Bucket → **Settings** → public access — pick one:
   - **r2.dev subdomain** ("Allow Access"): fastest, fine to start.
   - **Custom Domain** you own: recommended — stable, brandable, and
     permanent even if you later move clouds. This is the "forever URL".
3. **R2 → Manage API tokens** → create a token with **Object Read & Write**
   scoped to *only this bucket*. Save the Access Key ID and Secret.
4. GitHub repo → **Settings → Secrets and variables → Actions** → add:
   `R2_ACCOUNT_ID` (32-hex ID in your dashboard URL),
   `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`.
5. Push a `v*` tag. The workflow runs the full suite + smoke, uploads the
   new sdist/wheel under `dist/`, and regenerates
   `simple/duel-api/index.html` from the whole bucket listing (old
   versions stay available; the index uses relative links so it works
   under either public URL shape).

## Design notes

- **Why not a Worker:** a Worker is code in front of files — useful for
  auth or routing, pointless for public downloads. Static R2 is cheaper
  (free tier covers this many times over) and has fewer moving parts.
- **Why not self-host:** a home server means a public IP pointed at your
  LAN, uptime you personally guarantee, and bandwidth you pay. R2
  removes all three.
- **Why not PyPI only:** PyPI remains a fine later addition, but the
  hosted index gives you versioned direct-download URLs for videos and
  posts today, with zero review queues.
- Token scope is deliberately bucket-only; CI secrets never leave
  GitHub, and the workflow fails fast with a pointer here if any secret
  is missing.
