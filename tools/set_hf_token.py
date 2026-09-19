"""Masked input box: save the Hugging Face token into the repo .env.

Opens a small always-on-top window, takes the token in a password field, and
writes it to the project's `.env` (alongside HF_REPO / HF_REPO_TYPE /
HF_PREFIX). Optionally pushes all four values to GitHub Actions secrets using
the authenticated `gh` CLI.

The token is never printed, never echoed anywhere, and never passed as a
process argument — `gh secret set` receives it over stdin.

Run: py -3.13 tools/set_hf_token.py
"""

import json
import pathlib
import subprocess
import threading
import tkinter as tk
import urllib.request

REPO_SLUG = "kingkillery/Duel-api"
ENV_PATH = pathlib.Path(__file__).resolve().parent.parent / ".env"

DEFAULTS = {
    "HF_REPO": "pkkidking/privatepk",
    "HF_REPO_TYPE": "dataset",
    "HF_PREFIX": "duel-api-index",
}


def read_env() -> dict:
    values = dict(DEFAULTS)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    return values


def write_env(values: dict) -> None:
    lines = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            key = line.partition("=")[0].strip()
            if key and key in values:
                continue
            lines.append(line)
    for key in ("HF_TOKEN", "HF_REPO", "HF_REPO_TYPE", "HF_PREFIX"):
        if values.get(key):
            lines.append(f"{key}={values[key]}")
    ENV_PATH.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def mask(secret: str) -> str:
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]} ({len(secret)} chars)"


def push_secret(name: str, value: str) -> str:
    proc = subprocess.run(
        ["gh", "secret", "set", name, "--repo", REPO_SLUG],
        input=value,
        text=True,
        capture_output=True,
    )
    if proc.returncode == 0:
        return f"{name}: set"
    detail = (proc.stderr or proc.stdout).strip().splitlines()
    return f"{name}: FAILED ({detail[-1] if detail else 'unknown error'})"


class Box:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("duel-api — Hugging Face token")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        pad = {"padx": 14, "pady": 6}

        tk.Label(
            root,
            text="Paste the Hugging Face token for pkkidking/privatepk",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", **pad)
        tk.Label(
            root,
            text="Fine-grained, scoped to that repo, Write. Stays on this machine.",
            font=("Segoe UI", 8),
            fg="#666",
        ).pack(anchor="w", padx=14)

        self.entry = tk.Entry(root, show="\u2022", width=52, font=("Consolas", 10))
        self.entry.pack(**pad)
        self.entry.focus_force()

        self.push = tk.BooleanVar(value=True)
        tk.Checkbutton(
            root,
            text=f"Also set GitHub Actions secrets on {REPO_SLUG} (gh CLI)",
            variable=self.push,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=14)

        row = tk.Frame(root)
        row.pack(pady=10)
        tk.Button(row, text="Save", width=12, command=self.save).pack(side="left", padx=6)
        tk.Button(row, text="Validate token", width=14, command=self.validate).pack(
            side="left", padx=6
        )
        tk.Button(row, text="Cancel", width=10, command=root.destroy).pack(
            side="left", padx=6
        )

        self.status = tk.Label(root, text="", font=("Segoe UI", 9), wraplength=420, justify="left")
        self.status.pack(anchor="w", padx=14, pady=(0, 12))
        root.bind("<Return>", lambda _e: self.save())

    def token(self) -> str:
        return self.entry.get().strip()

    def say(self, text: str, ok: bool = True) -> None:
        self.status.config(text=text, fg="#0a7d28" if ok else "#b3261e")

    def save(self) -> None:
        token = self.token()
        if not token:
            self.say("Nothing entered — paste the token first.", ok=False)
            return
        values = read_env()
        values["HF_TOKEN"] = token
        write_env(values)
        report = [f"Saved to {ENV_PATH.name}: HF_TOKEN {mask(token)}"]

        if self.push.get():
            for key in ("HF_TOKEN", "HF_REPO", "HF_REPO_TYPE", "HF_PREFIX"):
                report.append(push_secret(key, values[key]))
        self.entry.delete(0, tk.END)
        self.say("\n".join(report))

    def validate(self) -> None:
        token = self.token()
        if not token:
            self.say("Nothing entered — paste the token first.", ok=False)
            return
        self.say("Checking with huggingface.co…")
        threading.Thread(target=self._validate, args=(token,), daemon=True).start()

    def _validate(self, token: str) -> None:
        req = urllib.request.Request(
            "https://huggingface.co/api/whoami-v2",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                name = json.load(resp).get("name", "(unknown)")
            self.say(f"Token is valid — Hugging Face account: {name}")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            self.say(f"Token check failed: {exc}", ok=False)


def main() -> None:
    root = tk.Tk()
    Box(root)
    root.mainloop()


if __name__ == "__main__":
    main()