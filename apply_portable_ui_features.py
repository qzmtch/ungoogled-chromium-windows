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

    # The source-side wrapper lets the UI use Chromium's normal safety policy.
    rel_source_h = "chrome/browser/resource_coordinator/tab_lifecycle_unit_source.h"
    path_source_h, source_h = read_text(root, rel_source_h)
    can_discard_decl = "  static bool CanDiscardTab(content::WebContents* web_contents);"
    if can_discard_decl not in source_h:
        getter_decl = (
            "  static TabLifecycleUnitExternal* GetTabLifecycleUnitExternal(\n"
            "      content::WebContents* web_contents);"
        )
        if getter_decl not in source_h:
            raise RuntimeError(
                f"{rel_source_h}: GetTabLifecycleUnitExternal declaration not found"
            )
        source_h = replace_once_or_present(
            source_h,
            getter_decl,
            getter_decl
            + "\n\n  // Uses Chromium's normal proactive-discard safety checks.\n"
            + can_discard_decl,
            f"{rel_source_h}: CanDiscardTab declaration",
        )
    write_if_changed(path_source_h, path_source_h.read_text(encoding="utf-8"), source_h)

    rel_source_cc = "chrome/browser/resource_coordinator/tab_lifecycle_unit_source.cc"
    path_source_cc, source_cc = read_text(root, rel_source_cc)
    can_discard_marker = "PORTABLE_UI_CAN_DISCARD_TAB_HELPER"
    if can_discard_marker not in source_cc:
        getter_impl = (
            "TabLifecycleUnitExternal* TabLifecycleUnitSource::GetTabLifecycleUnitExternal(\n"
            "    content::WebContents* web_contents) {\n"
            "  auto* lu = GetTabLifecycleUnit(web_contents);\n"
            "  if (!lu) {\n"
            "    return nullptr;\n"
            "  }\n"
            "  return lu->AsTabLifecycleUnitExternal();\n"
            "}\n"
        )
        getter_impl_compact = (
            "TabLifecycleUnitExternal* TabLifecycleUnitSource::GetTabLifecycleUnitExternal(\n"
            "    content::WebContents* web_contents) {\n"
            "  auto* lu = GetTabLifecycleUnit(web_contents);\n"
            "  if (!lu)\n"
            "    return nullptr;\n"
            "  return lu->AsTabLifecycleUnitExternal();\n"
            "}\n"
        )
        if getter_impl not in source_cc:
            if getter_impl_compact not in source_cc:
                raise RuntimeError(
                    f"{rel_source_cc}: GetTabLifecycleUnitExternal implementation not found"
                )
            getter_impl = getter_impl_compact
        can_discard_impl = getter_impl + (
            "\n// PORTABLE_UI_CAN_DISCARD_TAB_HELPER\n"
            "bool TabLifecycleUnitSource::CanDiscardTab(\n"
            "    content::WebContents* web_contents) {\n"
            "  auto* lifecycle_unit = GetTabLifecycleUnit(web_contents);\n"
            "  if (!lifecycle_unit) {\n"
            "    return false;\n"
            "  }\n"
            "  DecisionDetails decision_details;\n"
            "  return lifecycle_unit->CanDiscard(LifecycleUnitDiscardReason::PROACTIVE,\n"
            "                                    &decision_details);\n"
            "}\n"
        )
        source_cc = source_cc.replace(getter_impl, can_discard_impl, 1)
    write_if_changed(path_source_cc, path_source_cc.read_text(encoding="utf-8"), source_cc)

    rel_cc = "chrome/browser/ui/tabs/tab_strip_model.cc"
    path_cc, source = read_text(root, rel_cc)

    includes = [
        '#include "chrome/browser/resource_coordinator/tab_lifecycle_unit_external.h"',
        '#include "chrome/browser/resource_coordinator/lifecycle_unit_state.mojom.h"',
        '#include "chrome/browser/resource_coordinator/tab_lifecycle_unit_source.h"',
        '#include "chrome/browser/ui/performance_controls/tab_resource_usage_tab_helper.h"',
    ]
    for include in includes:
        if include not in source:
            anchor = '#include "chrome/browser/lifetime/browser_shutdown.h"\n'
            if anchor not in source:
                raise RuntimeError(f"{rel_cc}: include anchor missing for {include}")
            source = source.replace(anchor, anchor + include + "\n", 1)

    memory_helper = (
        "// PORTABLE_UI_DISCARD_MEMORY_ESTIMATE\n"
        "uint64_t GetPortableTabMemoryUsageEstimateKiB(\n"
        "    content::WebContents* contents) {\n"
        "  tabs::TabInterface* const tab =\n"
        "      tabs::TabInterface::GetFromContents(contents);\n"
        "  if (!tab) {\n"
        "    return 0;\n"
        "  }\n"
        "  const auto* const resource_usage_helper =\n"
        "      tab->GetTabFeatures()->resource_usage_helper();\n"
        "  if (!resource_usage_helper) {\n"
        "    return 0;\n"
        "  }\n"
        "  const auto resource_usage = resource_usage_helper->resource_usage();\n"
        "  if (!resource_usage || !resource_usage->memory_usage().is_positive()) {\n"
        "    return 0;\n"
        "  }\n"
        "  return static_cast<uint64_t>(resource_usage->memory_usage().InKiB());\n"
        "}\n\n"
    )
    if "PORTABLE_UI_DISCARD_MEMORY_ESTIMATE" not in source:
        anon_anchor = "namespace {\n\nTabGroupModelFactory* factory_instance = nullptr;\n"
        if anon_anchor not in source:
            raise RuntimeError(f"{rel_cc}: anonymous namespace helper anchor not found")
        source = source.replace(
            anon_anchor,
            "namespace {\n\n" + memory_helper + "TabGroupModelFactory* factory_instance = nullptr;\n",
            1,
        )

    enabled_cases = '''    // PORTABLE_UI_DISCARD_ENABLED_CASES
    case CommandPortableDiscardTab: {
      if (!ContainsIndex(context_index)) {
        return false;
      }
      // A visible tab cannot be proactively discarded; activation is changed
      // before the discard request in the execution path below.
      if (active_index() == context_index && count() < 2) {
        return false;
      }
      content::WebContents* contents = GetWebContentsAt(context_index);
      return contents &&
             resource_coordinator::TabLifecycleUnitSource::GetTabLifecycleUnitExternal(
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
            resource_coordinator::TabLifecycleUnitSource::GetTabLifecycleUnitExternal(
                contents)) {
          return true;
        }
      }
      return false;
    }
'''
    enabled_marker = "PORTABLE_UI_DISCARD_ENABLED_CASES"
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

    execute_cases = '''    // PORTABLE_UI_DISCARD_EXECUTE_CASES
    case CommandPortableDiscardTab: {
      if (!ContainsIndex(context_index)) {
        break;
      }

      // The clicked tab is the target even when it is currently active.
      // Switch away first because Chromium cannot proactively discard a visible tab.
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
                resource_coordinator::TabLifecycleUnitSource::GetTabLifecycleUnitExternal(
                    contents);
            lifecycle_unit &&
            resource_coordinator::TabLifecycleUnitSource::CanDiscardTab(
                contents)) {
          lifecycle_unit->DiscardTab(
              ::mojom::LifecycleUnitDiscardReason::PROACTIVE,
              GetPortableTabMemoryUsageEstimateKiB(contents));
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
                  resource_coordinator::TabLifecycleUnitSource::GetTabLifecycleUnitExternal(
                      contents);
              lifecycle_unit &&
              resource_coordinator::TabLifecycleUnitSource::CanDiscardTab(
                  contents)) {
            lifecycle_unit->DiscardTab(
                ::mojom::LifecycleUnitDiscardReason::PROACTIVE,
                GetPortableTabMemoryUsageEstimateKiB(contents));
          }
        }
      }
      break;
    }
'''
    execute_marker = "PORTABLE_UI_DISCARD_EXECUTE_CASES"
    if execute_marker in source:
        pattern = re.compile(
            r"(?ms)^    // PORTABLE_UI_DISCARD_EXECUTE_CASES\n.*?(?=^    case CommandNewTabToRight:)"
        )
        source, count = pattern.subn(execute_cases, source, count=1)
        if count != 1:
            raise RuntimeError(f"{rel_cc}: could not update discard execution cases")
    else:
        execute_anchor = "  switch (command_id) {\n    case CommandNewTabToRight: {\n"
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

    # Dependencies for lifecycle checks and the same memory estimate used by hover cards.
    rel_gn = "chrome/browser/ui/tabs/BUILD.gn"
    path_gn, gn = read_text(root, rel_gn)
    target_start = gn.find('  source_set("tab_strip_impl") {')
    if target_start < 0:
        raise RuntimeError(f"{rel_gn}: source_set(\"tab_strip_impl\") not found")
    target_end = gn.find("\n  source_set(", target_start + 1)
    if target_end < 0:
        target_end = len(gn)
    block = gn[target_start:target_end]
    dependencies = [
        '      "//chrome/browser/performance_manager/public/user_tuning",\n',
        '      "//chrome/browser/resource_coordinator",\n',
        '      "//chrome/browser/ui/performance_controls",\n',
    ]
    profile_anchor = '      "//chrome/browser/profiles:profile",\n'
    if block.count(profile_anchor) != 1:
        raise RuntimeError(
            f"{rel_gn}: couldn't identify unique profile dependency in tab_strip_impl"
        )
    insert = "".join(dep for dep in dependencies if dep.strip() not in block)
    if insert:
        block = block.replace(profile_anchor, profile_anchor + insert, 1)
        gn = gn[:target_start] + block + gn[target_end:]
    write_if_changed(path_gn, path_gn.read_text(encoding="utf-8"), gn)

def apply_hovercard_overrides(root: Path) -> None:
    rel = "chrome/browser/ui/views/tabs/tab_hover_card_bubble_view.cc"
    path, source = read_text(root, rel)

    # Keep native audio indicators on normal tabs. Suppress stale audio state
    # only when the hovered tab is currently discarded.
    alert_marker = "PORTABLE_UI_HIDE_DISCARDED_HOVERCARD_AUDIO"
    alert_anchor = "    alert_state_ = Tab::GetAlertStateToShow(tab_data.alert_state);"
    if alert_marker not in source:
        old_global_marker = "PORTABLE_UI_HIDE_HOVERCARD_AUDIO"
        if old_global_marker in source:
            pattern = re.compile(
                r"(?ms)^\s*// PORTABLE_UI_HIDE_HOVERCARD_AUDIO:.*?\n\s*}\n"
            )
            source, removed = pattern.subn("", source, count=1)
            if removed != 1:
                raise RuntimeError(f"{rel}: could not remove previous global audio suppression")
        source = replace_once_or_present(
            source,
            alert_anchor,
            alert_anchor + "\n    // PORTABLE_UI_HIDE_DISCARDED_HOVERCARD_AUDIO\n"
            "    if (tab_data.is_tab_discarded) {\n"
            "      alert_state_.reset();\n"
            "    }",
            f"{rel}: discarded-only audio alert suppression",
        )

    # No screenshot preview for discarded tabs; normal tabs retain previews.
    preview_new = "if (tab->IsActive() || tab->IsDiscarded()) {"
    preview_old = "if (tab->IsActive() || (tab->IsDiscarded() && !tab->HasThumbnail())) {"
    if preview_new not in source:
        source = replace_once_or_present(
            source, preview_old, preview_new,
            f"{rel}: suppress previews only for discarded tabs",
        )

    # Restore Chromium's native memory accounting. It already hides live memory
    # for discarded tabs and shows the saved-memory value in its inactive footer.
    native_memory_block = """        const base::ByteCount tab_memory_usage =
            tab_data.tab_resource_usage ? tab_data.tab_resource_usage->memory_usage()
                                       : base::ByteCount(0);
        const bool is_high_memory_usage =
            tab_data.tab_resource_usage
                ? tab_data.tab_resource_usage->is_high_memory_usage()
                : false;
        // High memory usage notification is considered a tab alert. Show it even
        // if the memory usage in hover cards pref is disabled.
        // However, collaboration messaging takes precedence over memory usage for
        // shared tabs.
        const bool show_memory_usage =
            !show_collaboration_messaging && !show_discard_status &&
            ((bubble_params_.show_memory_usage && tab_memory_usage.is_positive()) ||
             is_high_memory_usage);

        const bool show_footer ="""
    memory_pattern = re.compile(
        r"(?ms)^(?P<indent>[ \t]*)// PORTABLE_UI_HIDE_HOVERCARD_MEMORY\n"
        r".*?^(?P=indent)const bool show_footer ="
    )
    if memory_pattern.search(source):
        source, count = memory_pattern.subn(native_memory_block, source, count=1)
        if count != 1:
            raise RuntimeError(f"{rel}: could not restore native hover-card memory calculation")
    elif "const bool show_memory_usage = false;" in source:
        # Defensive recovery for a prior checkpoint where the marker was removed.
        old_block = re.compile(
            r"(?ms)^(?P<indent>[ \t]*)const bool show_memory_usage = false;\n"
            r"\s*\n(?P=indent)const bool show_footer ="
        )
        source, count = old_block.subn(native_memory_block, source, count=1)
        if count != 1:
            raise RuntimeError(f"{rel}: found global memory suppression but couldn't restore native logic")

    # Restore native memory-savings and current-memory payloads.
    source = source.replace(
        "{alert_state_, show_discard_status, base::ByteCount(0)}",
        "{alert_state_, show_discard_status, tab_data.discarded_memory_savings}",
        1,
    )
    source = source.replace(
        "{false, false, base::ByteCount(0)}",
        "{show_memory_usage, is_high_memory_usage, tab_memory_usage}",
        1,
    )

    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def apply_web_contents_discard(root: Path) -> None:
    rel = "content/public/common/content_features.cc"
    path, source = read_text(root, rel)
    marker = "PORTABLE_UI_PRESERVE_WEB_CONTENTS_ON_DISCARD"
    if marker not in source:
        patterns = [
            re.compile(
                r'BASE_FEATURE\(kWebContentsDiscard,\s*"WebContentsDiscard",\s*'
                r'base::FEATURE_DISABLED_BY_DEFAULT\);'
            ),
            re.compile(
                r'BASE_FEATURE\(WebContentsDiscard,\s*base::FEATURE_DISABLED_BY_DEFAULT\);'
            ),
        ]
        replacement = (
            "// PORTABLE_UI_PRESERVE_WEB_CONTENTS_ON_DISCARD: retain WebContentsTaskProvider "
            "tracking while a discarded tab is reloaded.\n"
            'BASE_FEATURE(kWebContentsDiscard, "WebContentsDiscard", '
            "base::FEATURE_ENABLED_BY_DEFAULT);"
        )
        for pattern in patterns:
            source, count = pattern.subn(replacement, source, count=1)
            if count:
                break
        else:
            raise RuntimeError(
                f"{rel}: could not locate disabled kWebContentsDiscard feature declaration"
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
        apply_web_contents_discard(root)
        apply_hovercard_overrides(root)
        apply_task_manager_stable_order(root)
        print(
            "Applied compact menus, native proactive tab discard, discarded-only hover-card "
            "changes, retained WebContents tracking and stable Task Manager ordering."
        )
        return 0
    except Exception as exc:
        print(f"ERROR applying portable UI features: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
