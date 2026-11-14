#!/usr/bin/env python3
"""Apply portable UI refinements to Chromium 141 source."""
from __future__ import annotations

import re
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
    if "// PORTABLE_UI_MENU_DENSITY" not in source:
        old = """  item_vertical_margin = 6;
  item_horizontal_border_padding = 12;
  arrow_size = 16;
  separator_height = 17;
  separator_spacing_height = 4;
"""
        new = """  // PORTABLE_UI_MENU_DENSITY: compact menus without shrinking their fonts.
  item_vertical_margin = 3;
  item_horizontal_border_padding = 0;
  arrow_size = 16;
  separator_height = 13;
  separator_spacing_height = 2;
"""
        source = replace_once_or_present(source, old, new, rel)
    else:
        # Earlier UI builds already had the marker but retained a 12px side gutter.
        source, count = re.subn(
            r"(?m)^(\s*item_horizontal_border_padding\s*=\s*)12(;)",
            r"\g<1>0\2",
            source,
        )
        if count > 1:
            raise RuntimeError(f"{rel}: found multiple horizontal border padding settings")
        if count == 0 and not re.search(
            r"(?m)^\s*item_horizontal_border_padding\s*=\s*0;", source
        ):
            raise RuntimeError(f"{rel}: could not verify horizontal border padding setting")
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
    # Both commands are always tied to the tab whose context menu was opened.
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
    lifecycle_include = (
        '#include "chrome/browser/resource_coordinator/tab_lifecycle_unit_external.h"'
    )
    if lifecycle_include not in source:
        anchor = '#include "chrome/browser/lifetime/browser_shutdown.h"\n'
        source = replace_once_or_present(
            source, anchor, anchor + lifecycle_include + "\n",
            f"{rel_cc}: TabLifecycleUnitExternal include",
        )
    lifecycle_state_include = (
        '#include "chrome/browser/resource_coordinator/lifecycle_unit_state.mojom.h"'
    )
    if lifecycle_state_include not in source:
        anchor = lifecycle_include + "\n"
        source = replace_once_or_present(
            source, anchor, anchor + lifecycle_state_include + "\n",
            f"{rel_cc}: lifecycle discard reason include",
        )

    enabled_marker = "PORTABLE_UI_DISCARD_ENABLED_CASES"
    enabled_cases = '''    // PORTABLE_UI_DISCARD_ENABLED_CASES
    case CommandPortableDiscardTab: {
      if (!ContainsIndex(context_index)) {
        return false;
      }
      // Chromium does not discard the only active tab in a window.
      if (active_index() == context_index && count() < 2) {
        return false;
      }
      content::WebContents* contents = GetWebContentsAt(context_index);
      return contents &&
             resource_coordinator::TabLifecycleUnitExternal::FromWebContents(
                 contents);
    }
    case CommandPortableDiscardOthers: {
      if (!ContainsIndex(context_index) || count() < 2) {
        return false;
      }
      for (int candidate = 0; candidate < count(); ++candidate) {
        if (candidate == context_index) {
          continue;
        }
        content::WebContents* contents = GetWebContentsAt(candidate);
        if (contents &&
            resource_coordinator::TabLifecycleUnitExternal::FromWebContents(
                contents)) {
          return true;
        }
      }
      return false;
    }
'''
    if enabled_marker in source:
        pattern = re.compile(
            r"(?ms)^    // PORTABLE_UI_DISCARD_ENABLED_CASES\n.*?(?=^    case CommandReload:)"
        )
        source, count = pattern.subn(enabled_cases, source, count=1)
        if count != 1:
            raise RuntimeError(f"{rel_cc}: could not update discard enabled-state cases")
    else:
        enabled_anchor = """    case CommandReload:
      return delegate_->CanReload();
"""
        source = replace_once_or_present(
            source, enabled_anchor, enabled_cases + enabled_anchor,
            f"{rel_cc}: context-menu enabled-state switch",
        )

    execute_marker = "PORTABLE_UI_DISCARD_EXECUTE_CASES"
    execute_cases = '''    // PORTABLE_UI_DISCARD_EXECUTE_CASES
    case CommandPortableDiscardTab: {
      if (!ContainsIndex(context_index)) {
        break;
      }

      // The clicked tab is the target even when it is currently active.
      // Switch away first because Chromium cannot discard the active tab.
      if (active_index() == context_index) {
        if (count() < 2) {
          break;
        }
        const int replacement_index = context_index > 0 ? context_index - 1 : 1;
        ActivateTabAt(replacement_index, TabStripUserGestureDetails(
            TabStripUserGestureDetails::GestureType::kNone));
      }

      if (content::WebContents* contents = GetWebContentsAt(context_index)) {
        if (auto* lifecycle_unit =
                resource_coordinator::TabLifecycleUnitExternal::FromWebContents(
                    contents)) {
          lifecycle_unit->DiscardTab(
              ::mojom::LifecycleUnitDiscardReason::EXTERNAL);
        }
      }
      break;
    }

    case CommandPortableDiscardOthers: {
      if (!ContainsIndex(context_index) || count() < 2) {
        break;
      }

      // Keep the tab that was right-clicked selected; unload every other tab.
      if (active_index() != context_index) {
        ActivateTabAt(context_index, TabStripUserGestureDetails(
            TabStripUserGestureDetails::GestureType::kNone));
      }

      // Discarding preserves tab-strip indices because tabs remain open.
      for (int candidate = count() - 1; candidate >= 0; --candidate) {
        if (candidate == context_index) {
          continue;
        }
        if (content::WebContents* contents = GetWebContentsAt(candidate)) {
          if (auto* lifecycle_unit =
                  resource_coordinator::TabLifecycleUnitExternal::FromWebContents(
                      contents)) {
            lifecycle_unit->DiscardTab(
                ::mojom::LifecycleUnitDiscardReason::EXTERNAL);
          }
        }
      }
      break;
    }
'''
    if execute_marker in source:
        pattern = re.compile(
            r"(?ms)^    // PORTABLE_UI_DISCARD_EXECUTE_CASES\n.*?(?=^    case CommandNewTabToRight:)"
        )
        source, count = pattern.subn(execute_cases, source, count=1)
        if count != 1:
            raise RuntimeError(f"{rel_cc}: could not update discard execution cases")
    else:
        execute_anchor = """  switch (command_id) {
    case CommandNewTabToRight: {
"""
        source = replace_once_or_present(
            source, execute_anchor,
            "  switch (command_id) {\n" + execute_cases + "    case CommandNewTabToRight: {\n",
            f"{rel_cc}: context-menu execution switch",
        )
    write_if_changed(path_cc, path_cc.read_text(encoding="utf-8"), source)

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

    # The target already depends on chrome/browser public dependencies, which
    # expose the tab lifecycle API. Keep the previous explicit user_tuning dep
    # intact for compatibility with existing source checkpoints.
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


def apply_hovercard_overrides(root: Path) -> None:
    rel = "chrome/browser/ui/views/tabs/tab_hover_card_bubble_view.cc"
    path, source = read_text(root, rel)

    alert_include = '#include "components/tabs/public/tab_alert.h"'
    if alert_include not in source:
        anchor = '#include "components/url_formatter/url_formatter.h"\n'
        source = replace_once_or_present(
            source, anchor, alert_include + "\n" + anchor,
            f"{rel}: TabAlert definition include",
        )

    alert_marker = "PORTABLE_UI_HIDE_HOVERCARD_AUDIO"
    alert_anchor = "    alert_state_ = Tab::GetAlertStateToShow(tab_data.alert_state);"
    if alert_marker not in source:
        alert_block = alert_anchor + """
    // PORTABLE_UI_HIDE_HOVERCARD_AUDIO: hide the audio activity footer only.
    if (alert_state_ == tabs::TabAlert::kAudioPlaying ||
        alert_state_ == tabs::TabAlert::kAudioMuting) {
      alert_state_.reset();
    }
"""
        source = replace_once_or_present(
            source, alert_anchor, alert_block, f"{rel}: hover-card audio state"
        )

    memory_marker = "// PORTABLE_UI_HIDE_HOVERCARD_MEMORY"
    memory_pattern = re.compile(
        r"(?ms)^(?P<indent>[ \t]*)const base::ByteCount tab_memory_usage =.*?"
        r"^(?P=indent)const bool show_footer ="
    )
    if memory_marker not in source:
        replacement = lambda m: (
            f"{m.group('indent')}{memory_marker}\n"
            f"{m.group('indent')}const bool show_memory_usage = false;\n\n"
            f"{m.group('indent')}const bool show_footer ="
        )
        source, count = memory_pattern.subn(replacement, source, count=1)
        if count != 1:
            raise RuntimeError(f"{rel}: could not locate the hover-card memory calculation block")
    else:
        # Normalize any previous memory block as well as making repeat runs safe.
        replacement = lambda m: (
            f"{m.group('indent')}{memory_marker}\n"
            f"{m.group('indent')}const bool show_memory_usage = false;\n\n"
            f"{m.group('indent')}const bool show_footer ="
        )
        source, count = memory_pattern.subn(replacement, source, count=1)
        if count == 0 and not re.search(
            r"(?m)^\s*const bool show_memory_usage = false;", source
        ):
            raise RuntimeError(f"{rel}: memory-hiding marker exists without disabled memory state")

    old_alert_data = """  footer_view_->SetAlertData(
      {alert_state_, show_discard_status, tab_data.discarded_memory_savings});
"""
    new_alert_data = """  footer_view_->SetAlertData(
      {alert_state_, show_discard_status, base::ByteCount(0)});
"""
    source = replace_once_or_present(
        source, old_alert_data, new_alert_data, f"{rel}: memory savings footer data"
    )

    old_performance_data = """  footer_view_->SetPerformanceData(
      {show_memory_usage, is_high_memory_usage, tab_memory_usage});
"""
    new_performance_data = """  footer_view_->SetPerformanceData(
      {false, false, base::ByteCount(0)});
"""
    source = replace_once_or_present(
        source, old_performance_data, new_performance_data,
        f"{rel}: hover-card performance footer data",
    )
    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def apply_task_manager_stable_order(root: Path) -> None:
    rel = "chrome/browser/ui/views/task_manager_view.cc"
    path, source = read_text(root, rel)

    # Avoid default CPU sorting, which makes rows jump as usage is sampled.
    source = replace_once_or_present(
        source,
        ".sort_on_cpu_by_default = tm_refresh_enabled,",
        ".sort_on_cpu_by_default = false,",
        f"{rel}: default sort",
    )

    stable_marker = "// PORTABLE_UI_TASK_MANAGER_STABLE_ORDER"
    if stable_marker not in source:
        anchor = """  table_model_->RetrieveSavedColumnsSettingsAndUpdateTable(
      table_config_.sort_on_cpu_by_default);
"""
        replacement = anchor + """  // PORTABLE_UI_TASK_MANAGER_STABLE_ORDER
  // Ignore a previously saved metric sort when opening Task Manager.
  tab_table_->SetSortDescriptors({});
"""
        source = replace_once_or_present(
            source, anchor, replacement, f"{rel}: reset saved dynamic sort"
        )

    fixed_marker = "PORTABLE_UI_TASK_MANAGER_FIXED_ORDER"
    if fixed_marker not in source:
        method_pattern = re.compile(
            r"(?ms)^void TaskManagerView::SetSortDescriptor\("
            r"const TableSortDescriptor& descriptor\) \{.*?^\}"
        )
        fixed_method = """void TaskManagerView::SetSortDescriptor(const TableSortDescriptor&) {
  // PORTABLE_UI_TASK_MANAGER_FIXED_ORDER: keep the model's process/task order.
  tab_table_->SetSortDescriptors({});
}"""
        source, count = method_pattern.subn(fixed_method, source, count=1)
        if count != 1:
            raise RuntimeError(
                f"{rel}: could not lock Task Manager to the stable process/task order"
            )
    write_if_changed(path, path.read_text(encoding="utf-8"), source)


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
        apply_hovercard_overrides(root)
        apply_task_manager_stable_order(root)
        print(
            "Applied compact menu spacing, lifecycle-managed clicked-tab discard, "
            "clean hover cards and stable Task Manager ordering."
        )
        return 0
    except Exception as exc:
        print(f"ERROR applying portable UI features: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
