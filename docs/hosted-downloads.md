# Hosted downloads

Public installs come from a folder inside the maintainer's existing
Hugging Face bucket — no new account, no server, and nothing touches the
home network: GitHub CI uploads with a scoped token, users download
straight from Hugging Face.

## For users (once the folder is live)

```bash
pip install --find-links \
  https://huggingface.co/datasets/<user>/<bucket>/resolve/main/<prefix>/simple/duel-api/index.html \
  duel-api
```

A specific version, straight from the file:

```bash
pip install \
  https://huggingface.co/datasets/<user>/<bucket>/resolve/main/<prefix>/dist/duel-api-0.2.1-py3-none-any.whl
```

Still supported as a fallback: `uvx --from git+https://github.com/kingkillery/Duel-api duel-api doctor`.

> Status: automation is merged (`.github/workflows/hosted-index.yml`);
> the bucket folder + token are pending the maintainer setup below.
> Until then, use the git+https fallback.

## For the maintainer: bucket setup (~10 minutes)

1. Pick the bucket repo, e.g. `<user>/<bucket>`.
2. Hugging Face → **Settings → Access Tokens** → create a
   **fine-grained** token scoped to **only that repo** with **Write**
   permission.
3. GitHub repo → **Settings → Secrets and variables → Actions** → add:
   `HF_TOKEN` (the token — never paste it in chat or logs),
   `HF_REPO` (e.g. `<user>/<bucket>`),
   `HF_REPO_TYPE` (`dataset`, or `model`),
   `HF_PREFIX` (`duel-api-index` unless you want another folder name).
   Or from your own terminal:
   `gh secret set HF_TOKEN --repo kingkillery/Duel-api`
4. Push a `v*` tag. The workflow runs the full suite + smoke, uploads the
   new sdist/wheel under `<prefix>/dist/`, and regenerates
   `<prefix>/simple/duel-api/index.html` from the whole remote listing
   (old versions stay installable).

## Isolation contract (what the bucket is guaranteed)

- Every written path is asserted in-code to start with `<prefix>/`
  (default `duel-api-index`). Anything else aborts the run.
- Uploads are **additive only**: individual file uploads, never a
  sync-with-delete. Nothing outside the prefix is listed for writing,
  let alone modified or removed. Bucket settings are never touched.
- The `.whl` / `.tar.gz` / `.html` files are inert to the dataset
  viewer — no previews, no conversions, no interference with other data.
- Openly stated footprint: one folder plus additive commit history in
  the bucket repo. If the bucket must show zero trace of this project,
  use a separate repo instead — say so and the workflow works unchanged
  with a different `HF_REPO`.
