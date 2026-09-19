"""Build a PEP 503 simple index page for one project from a file listing.

Reads distribution filenames (one per line on stdin, e.g. from
`aws s3api list-objects` over the bucket's dist/ prefix) and writes
`index.html` with relative links (`../../dist/<file>`), so the page works
under any public base URL (r2.dev or a custom domain) with zero config.

Only sdist (.tar.gz/.zip) and wheel (.whl) files for PROJECT are linked.
Usage: list-files | python tools/build_simple_index.py duel-api > index.html
"""

import html
import re
import sys
WHEEL_RE = re.compile(r"^(.+)-([^-]+?)(-[^-]+)?-[^-]+-[^-]+-[^.]+\.whl$")
SDIST_RE = re.compile(r"^(.+)-([^-]+)\.(tar\.gz|zip)$")


def project_of(name):
    m = WHEEL_RE.match(name) or SDIST_RE.match(name)
    if not m:
        return None
    return m.group(1).lower().replace("_", "-").replace(".", "-")

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: build_simple_index.py <project>", file=sys.stderr)
        return 2
    project = sys.argv[1].lower().replace("_", "-")
    files = []
    for line in sys.stdin:
        name = line.strip().rsplit("/", 1)[-1]
        if not name:
            continue
        if project_of(name) == project:
            files.append(name)
    files.sort()
    print("<!DOCTYPE html>")
    print("<html><head><meta charset=\"utf-8\">")
    print(f"<title>Links for {html.escape(project)}</title></head><body>")
    print(f"<h1>Links for {html.escape(project)}</h1>")
    for name in files:
        print(f"<a href=\"../../dist/{html.escape(name)}\">{html.escape(name)}</a><br/>")
    print("</body></html>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
