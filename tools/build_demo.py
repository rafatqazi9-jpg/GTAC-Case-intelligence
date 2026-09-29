"""Inline the exported brief data into the demo page.

Keeps the demo honest: the page is a template plus JSON that came out of the
running modules. There is no hand-authored copy of the tool's output anywhere
in the HTML.

    python tools/export_demo.py && python tools/build_demo.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "demo" / "template.html"
DATA = ROOT / "demo" / "demo_data.json"
OUT = ROOT / "demo" / "index.html"
TOKEN = "/*__DEMO_DATA__*/"


def main() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    if TOKEN not in html:
        raise SystemExit(f"placeholder {TOKEN} not found in {TEMPLATE}")

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    # Compact, and with "</script" defanged so the JSON cannot close its own
    # host tag.
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    OUT.write_text(html.replace(TOKEN, blob), encoding="utf-8")
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB, "
          f"{len(payload['cases'])} cases)")


if __name__ == "__main__":
    main()
