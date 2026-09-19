# Hosted downloads

Public installs are served two ways:

1. **Preferred — GitHub Pages PEP 503 index** (`text/html`):
   `https://kingkillery.github.io/Duel-api/simple/`
2. **Mirror — Hugging Face dataset** `pkkidking/duel-api-dl` (direct wheel URLs).

Nothing touches the home network: GitHub Actions builds and publishes;
users download from GitHub Pages / Hugging Face CDN.

## For users

One-line index install:

```bash
pip install --extra-index-url https://kingkillery.github.io/Duel-api/simple/ duel-api
```

**Verified (2026-09-19):** a clean venv ran that exact command, installed
`duel-api==0.2.2` from the Pages index (`Looking in indexes: …/Duel-api/simple/`),
and `duel-api doctor` printed the packaged spec. The name is not on PyPI, so
`--extra-index-url` cannot collide with a different package of the same name.

Direct wheel (pin a version by filename):

```bash
uvx --from "https://huggingface.co/datasets/pkkidking/duel-api-dl/resolve/main/duel-api-index/dist/duel_api-0.2.2-py3-none-any.whl" duel-api doctor
```

Source:

```bash
pipx install git+https://github.com/kingkillery/Duel-api
```

## Why Pages for the index

Hugging Face deliberately serves `.html` as `text/plain` (anti-phishing),
and pip rejects that Content-Type for indexes. GitHub Pages serves real
`text/html`, so `--extra-index-url` works. The HF tree remains a browsable
mirror of every released wheel/sdist.

## Maintainer automation

- Tag `v*` → `Pages index` workflow builds, tests, assembles `site/`, deploys
  to `kingkillery.github.io/Duel-api/`.
- Same tag → `Hosted index` workflow mirrors artifacts into
  `pkkidking/duel-api-dl` under `duel-api-index/` (isolation-scoped).
- Manual re-publish: Actions → Pages index → Run workflow.

## Isolation contract (HF mirror)

- Every written path is asserted in-code to start with `<prefix>/`.
- Uploads are additive file puts only — never sync-with-delete.
- `pkkidking/privatepk` is never written to.
