#!/usr/bin/env python3
"""Make Windows tracked-preference machine ID portable for Chromium profiles.

This is applied after Chromium and ungoogled-chromium patches, alongside the
portable OSCrypt post-processing step. The Windows implementation returns
NOT_IMPLEMENTED, matching profiles previously created with
--disable-machine-id.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FUNCTION_PATTERN = re.compile(
    r"MachineIdStatus\s+GetDeterministicMachineSpecificId\s*\(\s*"
    r"std::string\s*\*\s*machine_id\s*\)\s*\{",
    re.MULTILINE,
)

REPLACEMENT = (
    "MachineIdStatus GetDeterministicMachineSpecificId(std::string* machine_id) {\n"
    "  // Keep tracked preference hashes portable between Windows computers.\n"
    "  // This matches profiles created with --disable-machine-id.\n"
    "  (void)machine_id;\n"
    "  return MachineIdStatus::NOT_IMPLEMENTED;\n"
    "}"
)


def find_function_end(source: str, opening_brace: int) -> int:
    """Return index just after the matching closing brace, ignoring comments/strings."""
    depth = 0
    i = opening_brace
    state = "code"
    quote = ""
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""

        if state == "line_comment":
            if ch == "\n":
                state = "code"
            i += 1
            continue
        if state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
            else:
                i += 1
            continue
        if state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                state = "code"
            i += 1
            continue

        if ch == "/" and nxt == "/":
            state = "line_comment"
            i += 2
            continue
        if ch == "/" and nxt == "*":
            state = "block_comment"
            i += 2
            continue
        if ch in ('"', "'"):
            state = "string"
            quote = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1

    raise RuntimeError("Unbalanced braces in GetDeterministicMachineSpecificId().")


def transform(source: str) -> tuple[str, bool]:
    matches = list(FUNCTION_PATTERN.finditer(source))
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one GetDeterministicMachineSpecificId(std::string*), "
            f"found {len(matches)}. Chromium source layout may have changed."
        )

    match = matches[0]
    end = find_function_end(source, match.end() - 1)
    current = source[match.start():end]
    if "return MachineIdStatus::NOT_IMPLEMENTED;" in current:
        return source, False

    updated = source[:match.start()] + REPLACEMENT + source[end:]
    return updated, True


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: apply_portable_device_id.py <chromium-src>", file=sys.stderr)
        return 2

    source_tree = Path(sys.argv[1]).resolve()
    target = source_tree / "services/preferences/tracked/device_id_win.cc"
    if not target.is_file():
        print(f"ERROR: Chromium source file not found: {target}", file=sys.stderr)
        return 2

    try:
        original = target.read_text(encoding="utf-8")
        updated, changed = transform(original)
        if changed:
            target.write_text(updated, encoding="utf-8", newline="\n")
            print(
                "Applied portable tracked-preferences device ID transformation: "
                "MachineIdStatus::NOT_IMPLEMENTED."
            )
        else:
            print("Portable tracked-preferences device ID transformation already present.")
        return 0
    except Exception as exc:
        print(f"Could not apply portable device ID transformation: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
