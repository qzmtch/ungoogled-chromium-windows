#!/usr/bin/env python3
"""Install the portable OSCrypt and tracked-preference post-patch steps."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HOOK_MARKER = "# PORTABLE_OSCRYPT_POST_PATCH_HOOK"
SUBSTITUTE_MARKER = "        # Substitute domains\n"

HOOK_BLOCK = '''        # PORTABLE_OSCRYPT_POST_PATCH_HOOK
        subprocess.run([sys.executable, str(_ROOT_DIR / 'apply_portable_oscrypt.py'), str(source_tree)], check=True)
        subprocess.run([sys.executable, str(_ROOT_DIR / 'apply_portable_device_id.py'), str(source_tree)], check=True)
'''


def validate_secret() -> None:
    value = os.environ.get("PORTABLE_OSCRYPT_KEY", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise RuntimeError(
            "PORTABLE_OSCRYPT_KEY must be configured as a 64-character hexadecimal GitHub Actions secret."
        )


def main() -> int:
    try:
        validate_secret()
        root = Path(__file__).resolve().parent
        build_py = root / "build.py"
        required = [root / "apply_portable_oscrypt.py", root / "apply_portable_device_id.py"]

        for path in required:
            if not path.is_file():
                raise RuntimeError(f"Required post-processor is missing: {path.name}")
        if not build_py.is_file():
            raise RuntimeError(f"build.py not found in working copy: {build_py}")

        source = build_py.read_text(encoding="utf-8")
        if HOOK_MARKER in source:
            # Replace the previous one-step hook (if present) with the current
            # two-step hook. This also keeps repeated execution idempotent.
            start = source.index(HOOK_MARKER)
            line_start = source.rfind("\n", 0, start) + 1
            next_marker = source.find(SUBSTITUTE_MARKER, start)
            if next_marker < 0:
                raise RuntimeError("Existing portable post-patch hook has no following Substitute domains marker.")
            source = source[:line_start] + HOOK_BLOCK + source[next_marker:]
            build_py.write_text(source, encoding="utf-8", newline="\n")
            print("Updated portable OSCrypt + device-ID post-patch hook in the build working copy.")
            return 0

        if source.count(SUBSTITUTE_MARKER) != 1:
            raise RuntimeError(
                "Could not find exactly one build.py marker '        # Substitute domains'. "
                "The build.py layout may have changed."
            )

        source = source.replace(SUBSTITUTE_MARKER, HOOK_BLOCK + SUBSTITUTE_MARKER, 1)
        build_py.write_text(source, encoding="utf-8", newline="\n")
        print("Installed portable OSCrypt + device-ID post-patch hook in the build working copy.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
