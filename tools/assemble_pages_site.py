"""Assemble the GitHub Pages site for the duel-api PEP 503 index.

Copies local dist/ artifacts into site/, optionally merges any additional
wheels already mirrored on Hugging Face so older versions stay installable
when Pages deploys replace the whole site.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
SITE = ROOT / "site"


def _copy_local(site_dist: Path) -> None:
    wheels = sorted(DIST.glob("*.whl"))
    sdists = sorted(DIST.glob("*.tar.gz"))
    assert wheels or sdists, f"no distributions in {DIST}"
    for path in [*wheels, *sdists]:
        (site_dist / path.name).write_bytes(path.read_bytes())


def _merge_hf_mirror(site_dist: Path) -> None:
    """Pull existing release files from the HF mirror (additive, best-effort)."""
    token = os.environ.get("HF_TOKEN")
    repo = os.environ.get("HF_REPO") or "pkkidking/duel-api-dl"
    repo_type = os.environ.get("HF_REPO_TYPE") or "dataset"
    prefix = (os.environ.get("HF_PREFIX") or "duel-api-index").strip("/")
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        print("huggingface_hub not installed; skipping HF merge")
        return
    api = HfApi(token=token)
    try:
        remote = api.list_repo_files(repo_id=repo, repo_type=repo_type)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"HF list failed ({exc!r}); skipping merge")
        return
    for path in remote:
        if not path.startswith(prefix + "/dist/"):
            continue
        name = path.rsplit("/", 1)[-1]
        if not name.endswith((".whl", ".tar.gz")):
            continue
        dest = site_dist / name
        if dest.exists():
            continue
        try:
            local = hf_hub_download(
                repo_id=repo,
                repo_type=repo_type,
                filename=path,
                token=token,
            )
            dest.write_bytes(Path(local).read_bytes())
            print("merged from HF:", name)
        except Exception as exc:  # noqa: BLE001
            print(f"skip {name}: {exc!r}")


def main() -> int:
    site_dist = SITE / "dist"
    site_pkg = SITE / "simple" / "duel-api"
    site_dist.mkdir(parents=True, exist_ok=True)
    site_pkg.mkdir(parents=True, exist_ok=True)

    _copy_local(site_dist)
    _merge_hf_mirror(site_dist)

    listing = "\n".join(p.name for p in sorted(site_dist.iterdir())) + "\n"
    index = subprocess.check_output(
        ["python", str(ROOT / "tools" / "build_simple_index.py"), "duel-api"],
        input=listing,
        text=True,
        cwd=ROOT,
    )
    (site_pkg / "index.html").write_text(index, encoding="utf-8")

    (SITE / "index.html").write_text(
        """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>duel-api index</title></head>
<body>
<h1>duel-api</h1>
<p>PEP 503 simple index: <a href="simple/duel-api/">simple/duel-api/</a></p>
<p>Install:</p>
<pre>pip install --extra-index-url https://kingkillery.github.io/Duel-api/simple/ duel-api</pre>
</body></html>
""",
        encoding="utf-8",
    )
    (SITE / "simple" / "index.html").write_text(
        """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Simple Index</title></head>
<body><a href="duel-api/">duel-api</a></body></html>
""",
        encoding="utf-8",
    )

    files = sorted(str(p.relative_to(SITE)) for p in SITE.rglob("*") if p.is_file())
    print("assembled:")
    for f in files:
        print(" ", f)
    print("--- index ---")
    print(index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
