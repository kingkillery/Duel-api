"""Masked input box: save the Vercel AI Gateway key into the repo .env.

Same pattern as set_hf_token.py: a tkinter dialog collects the secret
out-of-band (never through chat or stdout) and upserts it into the
project .env, preserving every existing line. Prints only a masked
fingerprint on success.
"""

import pathlib
import re
import tkinter as tk

KEY = "AI_GATEWAY_API_KEY"
ENV_PATH = pathlib.Path(__file__).resolve().parent.parent / ".env"


def upsert_env(key: str, secret: str) -> None:
    escaped = secret.replace("\\", "\\\\").replace('"', '\\"')
    formatted = f'{key}="{escaped}"'
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            prefix = "export " if line.strip().startswith("export ") else ""
            lines[i] = f"{prefix}{formatted}"
            break
    else:
        lines.append(formatted)
    ENV_PATH.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def mask(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]} ({len(secret)} chars)"


class Box:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"Secure Credential Input - {KEY}")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        tk.Label(
            root,
            text=f"Enter {KEY} (saved to {ENV_PATH.name}, never echoed):",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 4))

        row = tk.Frame(root)
        row.pack(fill="x", padx=12)
        self.var = tk.StringVar()
        self.entry = tk.Entry(row, textvariable=self.var, show="*", width=44)
        self.entry.pack(side="left", fill="x", expand=True)
        self.show = tk.BooleanVar(value=False)
        tk.Checkbutton(
            row, text="Show", variable=self.show,
            command=lambda: self.entry.config(show="" if self.show.get() else "*"),
        ).pack(side="left", padx=(6, 0))

        self.status = tk.Label(root, text="", anchor="w")
        self.status.pack(fill="x", padx=12, pady=(4, 0))

        btns = tk.Frame(root)
        btns.pack(fill="x", padx=12, pady=12)
        tk.Button(btns, text="Save", width=10, command=self.save).pack(side="right", padx=(6, 0))
        tk.Button(btns, text="Cancel", width=10, command=root.destroy).pack(side="right")

        self.entry.bind("<Return>", lambda _e: self.save())
        self.entry.focus_set()

    def save(self) -> None:
        secret = self.var.get().strip()
        if not secret:
            self.status.config(text="Empty value - nothing saved.", fg="red")
            return
        try:
            upsert_env(KEY, secret)
        except Exception as exc:
            self.status.config(text=f"Write failed: {exc}", fg="red")
            return
        print(f"STATUS:SAVED {KEY}={mask(secret)}")
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    Box(root)
    root.mainloop()


if __name__ == "__main__":
    main()
