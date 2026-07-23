#!/usr/bin/env python3
"""Flip `draft = true` posts to `draft = false` once their `date` has arrived.

Runs daily in CI (see .github/workflows/deploy.yml). Prints what it publishes
and sets `changed=true` in $GITHUB_OUTPUT so the workflow knows to commit the
flip back to `main`. Pass --dry-run to preview without writing.

Only TOML front matter (delimited by `+++`) is understood, which is what this
site uses. Dates are compared by calendar day in UTC, so a post becomes visible
on the first run on or after its `date`.
"""

import datetime as dt
import os
import pathlib
import re
import sys
import tomllib

dry = "--dry-run" in sys.argv
today = dt.datetime.now(dt.timezone.utc).date()
changed = []

for p in sorted(pathlib.Path("content").rglob("*.md")):
    text = p.read_text(encoding="utf-8")
    m = re.match(r"\+\+\+\s*\n(.*?)\n\+\+\+\s*\n", text, re.DOTALL)  # TOML front matter
    if not m:
        continue
    try:
        meta = tomllib.loads(m.group(1))
    except tomllib.TOMLDecodeError:
        continue
    if meta.get("draft") is not True or meta.get("date") is None:
        continue
    d = meta["date"]
    day = d.date() if isinstance(d, dt.datetime) else d  # date or datetime
    if day <= today:
        changed.append(str(p))
        if not dry:
            fm = re.sub(r"(?m)^draft\s*=\s*true\s*$", "draft = false", m.group(1))
            p.write_text(text[: m.start(1)] + fm + text[m.end(1) :], encoding="utf-8")

if changed:
    print("Publishing:\n  " + "\n  ".join(changed))
else:
    print(f"Nothing due (today is {today} UTC).")

out = os.environ.get("GITHUB_OUTPUT")
if out and changed and not dry:
    with open(out, "a") as f:
        f.write("changed=true\n")
