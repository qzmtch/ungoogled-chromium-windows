#!/usr/bin/env python3
"""Install a one-time post-patch hook into the Windows build.py working copy."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def main() -> int:
    key_hex = os.environ.get("PORTABLE_OSCRYPT_KEY", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key_hex):
        print(
            "PORTABLE_OSCRYPT_KEY is missing or invalid; expected exactly 64 hexadecimal characters.",
            file=sys.stderr,
        )
        return 2

    root = Path(__file__).resolve().parent
    build_py = root / "build.py"
    postprocessor = root / "apply_portable_oscrypt.py"
    if not postprocessor.is_file():
        print(f"Portable OSCrypt postprocessor was not found: {postprocessor}", file=sys.stderr)
        return 2
    if not build_py.is_file():
        print(f"build.py was not found: {build_py}", file=sys.stderr)
        return 2

    source = build_py.read_text(encoding="utf-8")
    hook = (
        "        # Apply portable OSCrypt after all upstream and Windows patches.\n"
        "        subprocess.run([sys.executable, str(_ROOT_DIR / 'apply_portable_oscrypt.py'), str(source_tree)], check=True)\n"
    )
    if hook in source:
        print("Portable OSCrypt post-patch hook is already installed.")
        return 0

    marker = "        # Substitute domains\n"
    if source.count(marker) != 1:
        print(
            "Could not locate the unique post-patch insertion point in build.py. "
            "The expected marker is '# Substitute domains'.",
            file=sys.stderr,
        )
        return 2

    build_py.write_text(source.replace(marker, hook + marker, 1), encoding="utf-8", newline="\n")
    print("Installed portable OSCrypt post-patch hook in the build working copy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
