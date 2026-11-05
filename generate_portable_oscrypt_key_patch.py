#!/usr/bin/env python3
"""Create the ephemeral build-key patch for portable OSCrypt.

The PORTABLE_OSCRYPT_KEY secret is read only from the process environment.
The raw hexadecimal secret is never printed or written to a standalone file.
The generated Quilt patch replaces the same unique wrapping-key placeholder in
both the synchronous and asynchronous Windows OSCrypt key providers.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SERIES_STATIC_PATCH = "ungoogled-chromium/windows/windows-portable-oscrypt-encryption.patch"
SERIES_LEGACY_PATCH_NAME = "windows-portable-symmetric-encryption.patch"
SERIES_GENERATED_PATCH = "ungoogled-chromium/windows/zz-portable-oscrypt-build-key.patch"
TARGET_SOURCES = (
    "components/os_crypt/sync/os_crypt_win.cc",
    "components/os_crypt/async/browser/dpapi_key_provider.cc",
)
PLACEHOLDER = "PORTABLE_OSCRYPT_KEY_PLACEHOLDER"


def _series_path_from_line(line: str) -> str | None:
    value = line.strip()
    if not value or value.startswith("#"):
        return None
    value = value.split(" #", 1)[0].strip()
    return value.replace("\\", "/")


def _update_series(series_file: Path, static_patch_file: Path) -> None:
    if not series_file.is_file():
        raise FileNotFoundError(f"Quilt series file was not found: {series_file}")
    if not static_patch_file.is_file():
        raise FileNotFoundError(
            "The permanent OSCrypt patch is missing: "
            f"{static_patch_file}. Add it to the repository before running the workflow."
        )

    original_lines = series_file.read_text(encoding="utf-8-sig").splitlines()
    retained: list[str] = []
    has_static = False

    for line in original_lines:
        entry = _series_path_from_line(line)
        if entry is None:
            retained.append(line)
            continue
        filename = Path(entry).name
        if filename == SERIES_LEGACY_PATCH_NAME:
            continue
        if entry == SERIES_GENERATED_PATCH:
            continue
        if entry == SERIES_STATIC_PATCH:
            has_static = True
        retained.append(line)

    if not has_static:
        retained.append(SERIES_STATIC_PATCH)
    retained.append(SERIES_GENERATED_PATCH)
    series_file.write_text(
        "\n".join(retained).rstrip("\n") + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _make_key_patch(key_bytes: bytes) -> str:
    values = ", ".join(f"0x{value:02X}" for value in key_bytes)
    chunks: list[str] = []
    for target in TARGET_SOURCES:
        chunks.append(
            f"--- a/{target}\n"
            f"+++ b/{target}\n"
            "@@ -1,3 +1,3 @@\n"
            " constexpr unsigned char kPortableOsCryptWrappingKey[] = {\n"
            f"-    {PLACEHOLDER}\n"
            f"+    {values}\n"
            " };\n"
        )
    return "".join(chunks)


def main() -> int:
    key_hex = os.environ.get("PORTABLE_OSCRYPT_KEY", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key_hex):
        print(
            "PORTABLE_OSCRYPT_KEY is missing or invalid. "
            "Set the GitHub Actions secret to exactly 64 hexadecimal characters (32 random bytes).",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(__file__).resolve().parent
    patches_root = repo_root / "patches"
    windows_patch_dir = patches_root / "ungoogled-chromium" / "windows"
    if not windows_patch_dir.is_dir():
        print(f"Windows patch directory was not found: {windows_patch_dir}", file=sys.stderr)
        return 2

    series_file = patches_root / "series"
    static_patch_file = patches_root / Path(*SERIES_STATIC_PATCH.split("/"))
    generated_patch_file = patches_root / Path(*SERIES_GENERATED_PATCH.split("/"))

    try:
        key_bytes = bytes.fromhex(key_hex)
        patch_text = _make_key_patch(key_bytes)
        generated_patch_file.write_text(patch_text, encoding="ascii", newline="\n")
        _update_series(series_file, static_patch_file)
    except Exception as exc:
        print(f"Could not prepare portable OSCrypt patch: {exc}", file=sys.stderr)
        return 2

    print("Prepared portable OSCrypt build-key patch for sync and async providers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
