#!/usr/bin/env python3
"""Align one compact-UI source anchor with Chromium 141 BrowserView formatting.

Chromium 141's browser_view.h uses two spaces before the
'Do not friend BrowserViewLayout.' comment. The portable UI post-processor
currently searches for the same source line with three spaces. This helper
updates that literal anchor in the Python post-processor before it runs; it
does not modify Chromium C++ source files.
"""
from __future__ import annotations

import sys
from pathlib import Path

POSTPROCESSOR = Path("apply_portable_ui_features.py")
OLD_ANCHOR = r'anchor = " private:\n   // Do not friend BrowserViewLayout."'
NEW_ANCHOR = r'anchor = " private:\n  // Do not friend BrowserViewLayout."'


def normalize(project_root: Path) -> bool:
    path = project_root / POSTPROCESSOR
    if not path.is_file():
        raise RuntimeError(f"Required UI post-processor not found: {POSTPROCESSOR}")

    source = path.read_text(encoding="utf-8")
    old_count = source.count(OLD_ANCHOR)
    new_count = source.count(NEW_ANCHOR)

    if old_count == 1 and new_count == 0:
        updated = source.replace(OLD_ANCHOR, NEW_ANCHOR, 1)
        path.write_text(updated, encoding="utf-8", newline="\n")
        print(
            "Adjusted the compact-UI BrowserView private-section anchor "
            "to Chromium 141's two-space comment indentation."
        )
        return True

    if old_count == 0 and new_count == 1:
        print(
            "Compact-UI BrowserView private-section anchor already matches "
            "Chromium 141."
        )
        return False

    raise RuntimeError(
        "apply_portable_ui_features.py: expected exactly one old or normalized "
        f"BrowserView private-section anchor; found old={old_count}, new={new_count}. "
        "Refusing to edit an unknown post-processor version."
    )


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise RuntimeError(
                "Usage: normalize_portable_ui_postprocessor.py <repository-root>"
            )
        normalize(Path(sys.argv[1]).resolve())
        return 0
    except Exception as exc:
        print(f"ERROR normalizing portable UI post-processor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
