#!/usr/bin/env python3
"""Normalize Chromium 141's ChromeOS #endif comment for the portable UI post-processor.

Chromium 141.0.7390.122 ends the ChromeOS-only include block with a plain
'#endif'. The current portable UI post-processor uses the equivalent spelling
'#endif  // BUILDFLAG(IS_CHROMEOS)' as its insertion anchor. This script makes
that comment-only spelling consistent before the post-processor runs. It does
not change preprocessor behavior or any compiled code.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RELATIVE_PATH = Path("chrome/browser/ui/layout_constants.cc")
BLOCK_PATTERN = re.compile(
    r"(?m)^(?P<if_line>[ \t]*#if[ \t]+BUILDFLAG\(IS_CHROMEOS\)[ \t]*\n)"
    r"(?P<include_line>[ \t]*#include[ \t]+\"chromeos/constants/chromeos_features\.h\"[ \t]*\n)"
    r"(?P<endif_line>[ \t]*#endif(?:[ \t]*//[ \t]*BUILDFLAG\(IS_CHROMEOS\))?)$"
)
EXPECTED_COMMENT = "// BUILDFLAG(IS_CHROMEOS)"


def normalize(source_root: Path) -> bool:
    source_path = source_root / RELATIVE_PATH
    if not source_path.is_file():
        raise RuntimeError(f"Chromium source file not found: {RELATIVE_PATH.as_posix()}")

    source = source_path.read_text(encoding="utf-8")
    matches = list(BLOCK_PATTERN.finditer(source))
    if len(matches) != 1:
        raise RuntimeError(
            f"{RELATIVE_PATH.as_posix()}: expected one ChromeOS include guard, "
            f"found {len(matches)}; refusing to modify an uncertain source layout."
        )

    match = matches[0]
    current_endif = match.group("endif_line")
    if EXPECTED_COMMENT in current_endif:
        print(
            f"Portable layout anchor already normalized: {RELATIVE_PATH.as_posix()}"
        )
        return False

    normalized_endif = current_endif + "  " + EXPECTED_COMMENT
    replacement = (
        match.group("if_line")
        + match.group("include_line")
        + normalized_endif
    )
    updated = source[:match.start()] + replacement + source[match.end():]
    source_path.write_text(updated, encoding="utf-8", newline="\n")
    print(
        "Normalized comment-only Chromium 141 layout anchor in "
        f"{RELATIVE_PATH.as_posix()} (no C++ behavior changed)."
    )
    return True


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise RuntimeError(
                "Usage: normalize_portable_layout_constants.py <chromium-source-root>"
            )
        normalize(Path(sys.argv[1]))
        return 0
    except Exception as exc:
        print(f"ERROR normalizing portable layout anchor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
