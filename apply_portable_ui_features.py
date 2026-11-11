#!/usr/bin/env python3
"""Apply compact menu metrics and clicked-tab discard commands to Chromium 141."""
from __future__ import annotations

import sys
from pathlib import Path


def read_text(root: Path, rel: str) -> tuple[Path, str]:
    path = root / rel
    if not path.is_file():
        raise RuntimeError(f"Chromium source file not found: {rel}")
    return path, path.read_text(encoding="utf-8")


def write_if_changed(path: Path, old: str, new: str) -> None:
    if old != new:
        path.write_text(new, encoding="utf-8", newline="\n")


def replace_once_or_present(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one source anchor, found {count}. "
            "Chromium source layout may have changed."
        )
    return text.replace(old, new, 1)


def apply_menu_density(root: Path) -> None:
    rel = "ui/views/controls/menu/menu_config.cc"
    path, source = read_text(root, rel)
    if "// PORTABLE_UI_MENU_DENSITY" in source:
        return
    old = """  item_vertical_margin = 6;
  item_horizontal_border_padding = 12;
  arrow_size = 16;
  separator_height = 17;
  separator_spacing_height = 4;
"""
    new = """  // PORTABLE_UI_MENU_DENSITY: compact menus without shrinking their fonts.
  item_vertical_margin = 3;
  item_horizontal_border_padding = 12;
  arrow_size = 16;
  separator_height = 13;
  separator_spacing_height = 2;
"""
    source = replace_once_or_present(source, old, new, rel)
    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def apply_app_menu_density(root: Path) -> None:
    rel = "chrome/browser/ui/views/toolbar/app_menu.cc"
    path, source = read_text(root, rel)
    if "// PORTABLE_UI_APP_MENU_DENSITY" in source:
        return

    profile_old = """        add_menu_row_background(ChromeLayoutProvider::Get()->GetDistanceMetric(
                                    DISTANCE_CONTENT_LIST_VERTICAL_MULTI),
                                ui::kColorAppMenuProfileRowBackground);
"""
    profile_new = """        // PORTABLE_UI_APP_MENU_DENSITY
        add_menu_row_background(6, ui::kColorAppMenuProfileRowBackground);
"""
    source = replace_once_or_present(
        source, profile_old, profile_new, f"{rel}: profile row margin"
    )

    upgrade_old = "        add_menu_row_background(12, ui::kColorAppMenuUpgradeRowBackground);"
    upgrade_new = "        add_menu_row_background(6, ui::kColorAppMenuUpgradeRowBackground);"
    if upgrade_new not in source:
        count = source.count(upgrade_old)
        if count != 2:
            raise RuntimeError(
                f"{rel}: expected two upgrade row margin anchors, found {count}"
            )
        source = source.replace(upgrade_old, upgrade_new)

    default_old = """          add_menu_row_background(
              views::LayoutProvider::Get()->GetDistanceMetric(
                  views::DISTANCE_CONTROL_VERTICAL_TEXT_PADDING),
              ui::kColorAppMenuUpgradeRowBackground);
"""
    default_new = """          add_menu_row_background(
              6, ui::kColorAppMenuUpgradeRowBackground);
"""
    source = replace_once_or_present(
        source, default_old, default_new, f"{rel}: set-default row margin"
    )
    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def apply_tab_discard(root: Path) -> None:
    # Add two independent context-menu commands. Both are always present on the
    # tab that was right-clicked; neither command follows the active tab or the
    # multi-selection. The clicked index is the sole target/reference.
    rel_h = "chrome/browser/ui/tabs/tab_strip_model.h"
    path_h, header = read_text(root, rel_h)
    legacy_markers = (
        "CommandPortableDiscard,",
        "PORTABLE_UI_DISCARD_ENABLED_CASE",
        "PORTABLE_UI_DISCARD_EXECUTE_CASE",
    )
    if any(marker in header for marker in legacy_markers):
        raise RuntimeError(
            f"{rel_h}: obsolete one-command tab-discard patch detected. "
            "Use the corrected post-processor on a clean Chromium source checkpoint."
        )
    if "CommandPortableDiscardTab," not in header:
        header = replace_once_or_present(
            header,
            "    CommandCommerceProductSpecifications,\n",
            "    CommandCommerceProductSpecifications,\n"
            "    CommandPortableDiscardTab,\n"
            "    CommandPortableDiscardOthers,\n",
            f"{rel_h}: command enum",
        )
    write_if_changed(path_h, path_h.read_text(encoding="utf-8"), header)

    rel_cc = "chrome/browser/ui/tabs/tab_strip_model.cc"
    path_cc, source = read_text(root, rel_cc)
    needed_includes = [
        '#include "chrome/browser/performance_manager/public/user_tuning/user_tuning_utils.h"',
        '#include "chrome/browser/resource_coordinator/lifecycle_unit_state.mojom.h"',
        '#include "components/performance_manager/public/performance_manager.h"',
    ]
    if "// PORTABLE_UI_TAB_DISCARD_INCLUDES" not in source:
        include_anchor = '#include "chrome/browser/lifetime/browser_shutdown.h"\n'
        include_block = (
            include_anchor
            + "// PORTABLE_UI_TAB_DISCARD_INCLUDES\n"
            + "\n".join(needed_includes)
            + "\n"
        )
        source = replace_once_or_present(
            source, include_anchor, include_block,
            f"{rel_cc}: performance-manager includes",
        )
    else:
        for include in needed_includes:
            if include not in source:
                raise RuntimeError(f"{rel_cc}: discard include marker exists but {include} is missing")

    enabled_marker = "PORTABLE_UI_DISCARD_ENABLED_CASES"
    enabled_anchor = """    case CommandReload:
      return delegate_->CanReload();
"""
    enabled_cases = """    case CommandPortableDiscardTab: {
      // The command always refers to the tab that was right-clicked.
      if (!performance_manager::PerformanceManager::IsAvailable() ||
          !ContainsIndex(context_index)) {
        return false;
      }
      // Chromium cannot discard the only active tab in a window.
      if (active_index() == context_index && count() < 2) {
        return false;
      }
      return !!performance_manager::PerformanceManager::GetPrimaryPageNodeForWebContents(
          GetWebContentsAt(context_index));
    }
    case CommandPortableDiscardOthers: {
      if (!performance_manager::PerformanceManager::IsAvailable() ||
          !ContainsIndex(context_index) || count() < 2) {
        return false;
      }
      for (int candidate = 0; candidate < count(); ++candidate) {
        if (candidate == context_index) {
          continue;
        }
        if (performance_manager::PerformanceManager::GetPrimaryPageNodeForWebContents(
                GetWebContentsAt(candidate))) {
          return true;
        }
      }
      return false;
    }
"""
    if enabled_marker not in source:
        enabled_cases = enabled_cases.replace(
            "    case CommandPortableDiscardTab: {",
            "    // " + enabled_marker + "\n    case CommandPortableDiscardTab: {",
            1,
        )
        source = replace_once_or_present(
            source, enabled_anchor, enabled_anchor + enabled_cases,
            f"{rel_cc}: context-menu enabled-state switch",
        )

    execute_marker = "PORTABLE_UI_DISCARD_EXECUTE_CASES"
    execute_anchor = """  switch (command_id) {
    case CommandNewTabToRight: {
"""
    execute_cases = """  switch (command_id) {
    // PORTABLE_UI_DISCARD_EXECUTE_CASES
    case CommandPortableDiscardTab: {
      if (!ContainsIndex(context_index) ||
          !performance_manager::PerformanceManager::IsAvailable()) {
        break;
      }

      // Discarding an active tab is not supported by Chromium's lifecycle
      // manager. Move to a neighboring tab first, then discard the clicked tab.
      if (active_index() == context_index) {
        if (count() < 2) {
          break;
        }
        const int replacement_index = context_index > 0 ? context_index - 1 : 1;
        ActivateTabAt(replacement_index, /*user_gesture=*/false);
      }

      if (content::WebContents* contents = GetWebContentsAt(context_index)) {
        auto page_node =
            performance_manager::PerformanceManager::GetPrimaryPageNodeForWebContents(
                contents);
        if (page_node) {
          performance_manager::user_tuning::DiscardPage(
              page_node.get(), ::mojom::LifecycleUnitDiscardReason::EXTERNAL,
              /*ignore_minimum_time_in_background=*/true);
        }
      }
      break;
    }

    case CommandPortableDiscardOthers: {
      if (!ContainsIndex(context_index) || count() < 2 ||
          !performance_manager::PerformanceManager::IsAvailable()) {
        break;
      }

      // Keep the right-clicked tab as the selected tab. Every other tab is
      // therefore in the background before the discard requests are issued.
      if (active_index() != context_index) {
        ActivateTabAt(context_index, /*user_gesture=*/false);
      }

      // Iterate by index; discarding does not remove tabs from the strip.
      for (int candidate = count() - 1; candidate >= 0; --candidate) {
        if (candidate == context_index) {
          continue;
        }
        content::WebContents* contents = GetWebContentsAt(candidate);
        if (!contents) {
          continue;
        }
        auto page_node =
            performance_manager::PerformanceManager::GetPrimaryPageNodeForWebContents(
                contents);
        if (page_node) {
          performance_manager::user_tuning::DiscardPage(
              page_node.get(), ::mojom::LifecycleUnitDiscardReason::EXTERNAL,
              /*ignore_minimum_time_in_background=*/true);
        }
      }
      break;
    }

    case CommandNewTabToRight: {
"""
    if execute_marker not in source:
        source = replace_once_or_present(
            source, execute_anchor, execute_cases,
            f"{rel_cc}: context-menu execution switch",
        )

    write_if_changed(path_cc, path_cc.read_text(encoding="utf-8"), source)

    # Add both entries together to every normal tab context menu. They are not
    # conditionally renamed based on which tab is currently active.
    rel_menu = "chrome/browser/ui/tabs/tab_menu_model.cc"
    path_menu, menu = read_text(root, rel_menu)
    menu_marker = "PORTABLE_UI_TAB_DISCARD_MENU_ITEMS"
    if menu_marker not in menu:
        anchor = """  AddItemWithStringId(TabStripModel::CommandDuplicate,
                      IDS_TAB_CXMENU_DUPLICATE);
"""
        replacement = anchor + """  AddSeparator(ui::NORMAL_SEPARATOR);
  // PORTABLE_UI_TAB_DISCARD_MENU_ITEMS
  AddItem(TabStripModel::CommandPortableDiscardTab, u"Выгрузить вкладку");
  SetEnabledAt(GetItemCount() - 1,
               tab_strip->IsContextMenuCommandEnabled(
                   index, TabStripModel::CommandPortableDiscardTab));
  AddItem(TabStripModel::CommandPortableDiscardOthers,
          u"Выгрузить остальные вкладки");
  SetEnabledAt(GetItemCount() - 1,
               tab_strip->IsContextMenuCommandEnabled(
                   index, TabStripModel::CommandPortableDiscardOthers));
"""
        menu = replace_once_or_present(
            menu, anchor, replacement, f"{rel_menu}: context-menu items"
        )
    write_if_changed(path_menu, path_menu.read_text(encoding="utf-8"), menu)

    # tab_strip_model.cc is compiled by tab_strip_impl. This single dependency
    # exposes the user-tuning discard API, PerformanceManager, and lifecycle mojom.
    rel_gn = "chrome/browser/ui/tabs/BUILD.gn"
    path_gn, gn = read_text(root, rel_gn)
    target_start = gn.find('  source_set("tab_strip_impl") {')
    if target_start < 0:
        raise RuntimeError(f"{rel_gn}: source_set(\"tab_strip_impl\") not found")
    target_end = gn.find("\n  source_set(", target_start + 1)
    if target_end < 0:
        target_end = len(gn)
    block = gn[target_start:target_end]
    dependency = '      "//chrome/browser/performance_manager/public/user_tuning",\n'
    if dependency.strip() not in block:
        profile_anchor = '      "//chrome/browser/profiles:profile",\n'
        if block.count(profile_anchor) != 1:
            raise RuntimeError(
                f"{rel_gn}: couldn't identify unique profile dependency in tab_strip_impl"
            )
        block = block.replace(profile_anchor, profile_anchor + dependency, 1)
        gn = gn[:target_start] + block + gn[target_end:]
    write_if_changed(path_gn, path_gn.read_text(encoding="utf-8"), gn)


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise RuntimeError("Usage: apply_portable_ui_features.py <chromium-source-tree>")
        root = Path(sys.argv[1]).resolve()
        if not (root / "chrome/browser/ui/tabs/tab_strip_model.cc").is_file():
            raise RuntimeError(f"Not a Chromium source tree: {root}")
        apply_menu_density(root)
        apply_app_menu_density(root)
        apply_tab_discard(root)
        print(
            "Applied compact menu metrics and two clicked-tab discard commands "
            "(discard clicked tab / discard all other tabs)."
        )
        return 0
    except Exception as exc:
        print(f"ERROR applying portable UI features: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
