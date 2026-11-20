#!/usr/bin/env python3
"""Fix Chromium 141 C++ compatibility issues introduced by portable UI hooks.

Run after apply_portable_ui_features.py against the Chromium source tree.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

EXPECTED_VERSION = (141, 0, 7390, 122)


def read_source(root: Path, relative: str) -> tuple[Path, str]:
    path = root / relative
    if not path.is_file():
        raise RuntimeError(f"Chromium source file not found: {relative}")
    return path, path.read_text(encoding="utf-8")


def write_if_changed(path: Path, before: str, after: str) -> None:
    if before != after:
        path.write_text(after, encoding="utf-8", newline="\n")


def verify_chromium_version(root: Path) -> None:
    path = root / "chrome/VERSION"
    if not path.is_file():
        raise RuntimeError("chrome/VERSION not found; refusing to apply a version-specific fix")

    values: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"(MAJOR|MINOR|BUILD|PATCH)=(\d+)", line.strip())
        if match:
            values[match.group(1)] = int(match.group(2))

    version = tuple(values.get(key, -1) for key in ("MAJOR", "MINOR", "BUILD", "PATCH"))
    if version != EXPECTED_VERSION:
        formatted = ".".join(str(part) for part in version)
        expected = ".".join(str(part) for part in EXPECTED_VERSION)
        raise RuntimeError(
            f"This compatibility fix targets Chromium {expected}, but source tree is {formatted}."
        )


def fix_bookmark_icons(root: Path) -> None:
    relative = "chrome/browser/ui/views/bookmarks/bookmark_bar_view.cc"
    path, source = read_source(root, relative)
    required_marker = "PORTABLE_UI_BOOKMARK_SCHEME_COLORS"
    if required_marker not in source:
        raise RuntimeError(f"{relative}: portable bookmark color block was not found")

    include = '#include "components/vector_icons/vector_icons.h"\n'
    if include not in source:
        anchor = '#include "components/url_formatter/elide_url.h"\n'
        count = source.count(anchor)
        if count != 1:
            raise RuntimeError(
                f"{relative}: expected one URL formatter include anchor, found {count}"
            )
        source = source.replace(anchor, include + anchor, 1)

    for icon in ("vector_icons::kCodeIcon", "vector_icons::kDatabaseIcon"):
        if icon not in source:
            raise RuntimeError(f"{relative}: expected icon reference is missing: {icon}")

    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def fix_bookmark_bar_mouse_events(root: Path) -> None:
    relative = "chrome/browser/ui/views/frame/browser_view.cc"
    path, source = read_source(root, relative)
    marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_MOUSE_EVENT"
    if marker not in source:
        raise RuntimeError(f"{relative}: bookmark-bar auto-hide mouse handler was not found")

    replacements = (
        ("ui::ET_MOUSE_MOVED", "ui::EventType::kMouseMoved"),
        ("ui::ET_MOUSE_ENTERED", "ui::EventType::kMouseEntered"),
    )
    for old, new in replacements:
        old_count = source.count(old)
        new_count = source.count(new)
        if old_count == 1 and new_count == 0:
            source = source.replace(old, new, 1)
        elif old_count == 0 and new_count == 1:
            # Already corrected; keep the operation idempotent.
            pass
        else:
            raise RuntimeError(
                f"{relative}: expected one occurrence of either {old!r} or {new!r}; "
                f"found old={old_count}, new={new_count}"
            )

    if "ui::ET_MOUSE_MOVED" in source or "ui::ET_MOUSE_ENTERED" in source:
        raise RuntimeError(f"{relative}: obsolete unscoped mouse event identifiers remain")

    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def fix_redundant_event_handler_base(root: Path) -> None:
    relative = "chrome/browser/ui/views/frame/browser_view.h"
    path, source = read_source(root, relative)

    if "public views::ClientView," not in source:
        raise RuntimeError(f"{relative}: expected views::ClientView base was not found")
    if "void OnMouseEvent(ui::MouseEvent* event) override;" not in source:
        raise RuntimeError(f"{relative}: portable OnMouseEvent override was not found")

    pattern = re.compile(r"(?m)^(?P<indent>[ \t]*)public ui::EventHandler,\r?\n")
    matches = list(pattern.finditer(source))
    if len(matches) == 1:
        source = pattern.sub("", source, count=1)
    elif len(matches) > 1:
        raise RuntimeError(
            f"{relative}: found {len(matches)} direct ui::EventHandler base declarations"
        )
    # Zero matches means this cleanup has already been applied.

    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise RuntimeError("Usage: fix_portable_ui_compiler.py <chromium-source-tree>")
        root = Path(sys.argv[1]).resolve()
        verify_chromium_version(root)
        fix_bookmark_icons(root)
        fix_bookmark_bar_mouse_events(root)
        fix_redundant_event_handler_base(root)
        print(
            "Applied Chromium 141 portable UI compile fixes: vector icon header, "
            "scoped mouse event types and redundant EventHandler base cleanup."
        )
        return 0
    except Exception as exc:
        print(f"ERROR applying portable UI compiler fixes: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
