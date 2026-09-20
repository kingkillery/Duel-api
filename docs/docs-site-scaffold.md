# docs-site scaffold (mkdocs draft — week 6)

`mkdocs.yml` at repo root is a minimal draft: bundled `readthedocs` theme (no new dependency), nav over existing `docs/` pages only.

## Deploy collision — read before enabling

`hosted-index.yml` deploys `./public` to the `gh-pages` branch with `clean: true`. A mkdocs site published to the same branch **would be wiped** by every package-index deploy. Do NOT add a docs deploy workflow until one of these is chosen:

- **Option A (recommended):** extend `hosted-index.yml` to build mkdocs into `./public/docs/` in the same job, so one deploy publishes index + docs atomically.
- **Option B:** publish docs to a separate branch/subdomain and link it from the Pages index.

Until then this scaffold is local-preview only: `mkdocs serve` / `mkdocs build --strict` (strict = broken nav links fail the build).
