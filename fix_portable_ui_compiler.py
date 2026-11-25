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


def ensure_include(source: str, include: str, anchors: tuple[str, ...], label: str) -> str:
    """Add an include once, using the first known stable include as its anchor."""
    if include in source:
        return source

    for anchor in anchors:
        count = source.count(anchor)
        if count == 1:
            return source.replace(anchor, include + anchor, 1)
        if count > 1:
            raise RuntimeError(f"{label}: include anchor is ambiguous ({count} matches): {anchor.strip()}")

    raise RuntimeError(f"{label}: no known include anchor was found for {include.strip()}")


def fix_bookmark_icons(root: Path) -> None:
    relative = "chrome/browser/ui/views/bookmarks/bookmark_bar_view.cc"
    path, source = read_source(root, relative)
    required_marker = "PORTABLE_UI_BOOKMARK_SCHEME_COLORS"
    if required_marker not in source:
        raise RuntimeError(f"{relative}: portable bookmark color block was not found")

    source = ensure_include(
        source,
        '#include "components/vector_icons/vector_icons.h"\n',
        (
            '#include "chrome/browser/ui/views/bookmarks/bookmark_bar_view.h"\n',
            '#include "components/url_formatter/elide_url.h"\n',
        ),
        relative,
    )

    custom_icon = "kPortableJavascriptBookmarkIcon"
    legacy_icon = "vector_icons::kCodeIcon"
    if custom_icon in source:
        # Current project UI uses a custom vector lettermark that draws JS,
        # rather than Chromium's generic angle-bracket/code icon. Its types
        # come from vector_icon_types.h, not from the generated icon catalogue.
        source = ensure_include(
            source,
            '#include "ui/gfx/vector_icon_types.h"\n',
            (
                '#include "components/vector_icons/vector_icons.h"\n',
                '#include "chrome/browser/ui/views/bookmarks/bookmark_bar_view.h"\n',
            ),
            relative,
        )
        custom_requirements = (
            "gfx::PathElement kPortableJavascriptBookmarkPath",
            "gfx::VectorIconRep kPortableJavascriptBookmarkReps",
            "gfx::VectorIcon kPortableJavascriptBookmarkIcon",
            "ui::ImageModel::FromVectorIcon(kPortableJavascriptBookmarkIcon",
        )
        for item in custom_requirements:
            if item not in source:
                raise RuntimeError(f"{relative}: custom JS bookmark icon is incomplete: {item}")

        # Chromium 141's gfx::VectorIconRep contains one raw_span<const
        # PathElement> member. A pointer-plus-size aggregate initializer
        # compiles on older Chromium branches but is invalid here.
        rep_pattern = re.compile(
            r"(?s)static\s+const\s+gfx::VectorIconRep\s+"
            r"kPortableJavascriptBookmarkReps\[\]\s*=\s*"
            r"\{\s*\{\s*kPortableJavascriptBookmarkPath\s*\}\s*,?\s*\};"
        )
        if not rep_pattern.search(source):
            raise RuntimeError(
                f"{relative}: JS bookmark VectorIconRep must use the Chromium 141 "
                "single-field raw_span initializer {kPortableJavascriptBookmarkPath}"
            )
    elif legacy_icon not in source:
        raise RuntimeError(
            f"{relative}: neither the custom JS lettermark ({custom_icon}) nor the legacy "
            f"{legacy_icon} reference was found"
        )

    if "vector_icons::kDatabaseIcon" not in source:
        raise RuntimeError(f"{relative}: expected data: bookmark icon is missing: vector_icons::kDatabaseIcon")

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


def fix_bookmark_context_menu_browser_window(root: Path) -> None:
    relative = "chrome/browser/ui/bookmarks/bookmark_context_menu_controller.cc"
    path, source = read_source(root, relative)

    marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_EXECUTE"
    call = "browser_->window()->BookmarkBarStateChanged("
    if marker not in source or call not in source:
        raise RuntimeError(
            f"{relative}: portable bookmark-bar auto-hide command was not found"
        )
    include = '#include "chrome/browser/ui/browser_window.h"\n'
    if include not in source:
        anchor = '#include "chrome/browser/ui/browser.h"\n'
        count = source.count(anchor)
        if count != 1:
            raise RuntimeError(
                f"{relative}: expected one browser.h include anchor, found {count}"
            )
        source = source.replace(anchor, anchor + include, 1)
    elif source.count(include) != 1:
        raise RuntimeError(f"{relative}: duplicate browser_window.h includes found")
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
        fix_bookmark_context_menu_browser_window(root)
        print(
            "Applied Chromium 141 portable UI compile fixes: bookmark vector icon headers "
            "and JS lettermark validation, scoped mouse event types, redundant EventHandler "
            "base cleanup, and the BrowserWindow definition include."
        )
        return 0
    except Exception as exc:
        print(f"ERROR applying portable UI compiler fixes: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
