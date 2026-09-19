# Hosted downloads

Public installs come from a **public Hugging Face dataset repo** —
`pkkidking/duel-api-dl`. No server, no new account, nothing touching the
home network: GitHub CI uploads with a scoped token, users download from
Hugging Face's CDN.

**Status: live.** `v0.2.2` was published and installed end-to-end
(`2026-09-19`).

## For users (verified)

One line, no account, no repository access:

```bash
uvx --from "https://huggingface.co/datasets/pkkidking/duel-api-dl/resolve/main/duel-api-index/dist/duel_api-0.2.2-py3-none-any.whl" duel-api doctor
```

or plain pip:

```bash
pip install "https://huggingface.co/datasets/pkkidking/duel-api-dl/resolve/main/duel-api-index/dist/duel_api-0.2.2-py3-none-any.whl"
```

All released files — every version, wheel and sdist — are listed at
<https://huggingface.co/datasets/pkkidking/duel-api-dl/tree/main/duel-api-index/dist>.
Swap the filename to pin a different version; the pinned URL *is* the
reproducible install.

## Why not `pip --find-links`

The workflow also publishes a standard PEP 503 index page
(`duel-api-index/simple/duel-api/index.html`), and it is publicly readable —
but **pip refuses it**. Hugging Face deliberately serves `.html` files as
`text/plain` (anti-phishing policy for hosted repos), and pip only accepts
`text/html` or the PEP 691 JSON types for an index page:

```text
WARNING: Skipping page ... because the GET request got Content-Type:
text/plain; charset=utf-8. The only supported Content-Types are
application/vnd.pypi.simple.v1+json, application/vnd.pypi.simple.v1+html,
and text/html
```

Consequences, honestly stated:

- The index page is kept as a human-browsable listing, not as a pip index.
- There is **no** stable "always-latest" URL: a wheel copy named
  `duel_api-latest-*.whl` is rejected by pip (`Invalid wheel filename
  (invalid version)`), verified.
- A true one-line index (`pip install --extra-index-url … duel-api`) needs
  a host that serves `text/html` — e.g. Cloudflare R2 or a static HF Space.
  Neither is set up. The direct wheel URL above works today.

## Maintainer setup (done, for reference)

1. Public **dataset repo** `pkkidking/duel-api-dl` was created for this
   purpose (the original `pkkidking/privatepk` is a *private* bucket and is
   deliberately not used).
2. A Hugging Face **write token** (`ally`) is stored locally in `.env`
   (gitignored) and in the repo's Actions secrets.
3. Actions secrets: `HF_TOKEN`, `HF_REPO=pkkidking/duel-api-dl`,
   `HF_REPO_TYPE=dataset`, `HF_PREFIX=duel-api-index`.
   Set locally with `tools/set_hf_token.py` (masked input box) or in the
   GitHub UI.
4. Push a `v*` tag → the workflow runs the suite + smoke, uploads the new
   sdist/wheel, and rebuilds the index listing.

## Isolation contract

- Every written path is asserted in-code to start with `<prefix>/`. Other
  paths abort the run.
- Uploads are additive file puts only — never a sync-with-delete, never a
  read-modify of anything outside the prefix.
- `pkkidking/privatepk` (the maintainer's private bucket, holding existing
  data) is **never written to** — different repo entirely.
- Footprint in the hosting repo: one `duel-api-index/` folder plus additive
  commit history.