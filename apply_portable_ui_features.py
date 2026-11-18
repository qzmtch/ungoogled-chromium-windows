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
    can_discard_decl = (
        "  static bool CanDiscardTab(\n"
        "      content::WebContents* web_contents,\n"
        "      bool* blocked_only_by_visibility = nullptr);"
    )
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
    decision_details_include = '#include "chrome/browser/resource_coordinator/decision_details.h"\n'
    if decision_details_include not in source_cc:
        source_header = '#include "chrome/browser/resource_coordinator/tab_lifecycle_unit_source.h"\n'
        source_cc = replace_once_or_present(
            source_cc, source_header, source_header + decision_details_include,
            f"{rel_source_cc}: DecisionDetails include",
        )
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
            "    content::WebContents* web_contents,\n"
            "    bool* blocked_only_by_visibility) {\n"
            "  if (blocked_only_by_visibility) {\n"
            "    *blocked_only_by_visibility = false;\n"
            "  }\n"
            "  auto* lifecycle_unit = GetTabLifecycleUnit(web_contents);\n"
            "  if (!lifecycle_unit) {\n"
            "    return false;\n"
            "  }\n"
            "  DecisionDetails decision_details;\n"
            "  const bool can_discard = lifecycle_unit->CanDiscard(\n"
            "      LifecycleUnitDiscardReason::PROACTIVE, &decision_details);\n"
            "  if (blocked_only_by_visibility && !can_discard &&\n"
            "      decision_details.reasons().size() == 1 &&\n"
            "      decision_details.FailureReason() ==\n"
            "          DecisionFailureReason::LIVE_STATE_VISIBLE) {\n"
            "    *blocked_only_by_visibility = true;\n"
            "  }\n"
            "  return can_discard;\n"
            "}\n"
        )
        source_cc = source_cc.replace(getter_impl, can_discard_impl, 1)
    write_if_changed(path_source_cc, path_source_cc.read_text(encoding="utf-8"), source_cc)

    rel_cc = "chrome/browser/ui/tabs/tab_strip_model.cc"
    path_cc, source = read_text(root, rel_cc)

    includes = [
        '#include "chrome/browser/resource_coordinator/decision_details.h"',
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

      const bool target_was_active = active_index() == context_index;
      if (target_was_active && count() < 2) {
        break;
      }

      content::WebContents* target_contents = GetWebContentsAt(context_index);
      if (!target_contents) {
        break;
      }

      // Check every safety rule before changing the selection. For an active
      // tab, switch away only when visibility is the sole reason preventing
      // discard. Media playback, form input, capture, PDF and other protections
      // therefore leave the currently selected tab untouched.
      if (target_was_active) {
        bool blocked_only_by_visibility = false;
        resource_coordinator::TabLifecycleUnitSource::CanDiscardTab(
            target_contents, &blocked_only_by_visibility);
        if (!blocked_only_by_visibility) {
          break;
        }
        const int replacement_index = context_index > 0 ? context_index - 1 : 1;
        ActivateTabAt(replacement_index, TabStripUserGestureDetails(
            TabStripUserGestureDetails::GestureType::kNone));
      }

      bool discarded = false;
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
          discarded = true;
        }
      }

      if (target_was_active && !discarded && ContainsIndex(context_index)) {
        ActivateTabAt(context_index, TabStripUserGestureDetails(
            TabStripUserGestureDetails::GestureType::kNone));
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


def apply_menu_roundness(root: Path) -> None:
    rel = "ui/views/controls/menu/menu_config.cc"
    path, source = read_text(root, rel)
    marker = "PORTABLE_UI_MENU_ROUNDNESS"
    if marker not in source:
        old = """MenuConfig::MenuConfig() {
  InitCommon();
  InitPlatform();
}"""
        new = """MenuConfig::MenuConfig() {
  InitCommon();
  InitPlatform();
  // PORTABLE_UI_MENU_ROUNDNESS: slightly reduce outer menu corner radii.
  corner_radius = 6;
  auxiliary_corner_radius = 5;
  touchable_corner_radius = 6;
}"""
        source = replace_once_or_present(source, old, new, rel)
    else:
        for required in (
            "corner_radius = 6;",
            "auxiliary_corner_radius = 5;",
            "touchable_corner_radius = 6;",
        ):
            if required not in source:
                raise RuntimeError(f"{rel}: incomplete roundness override: {required}")
    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def apply_compact_toolbar(root: Path) -> None:
    rel = "chrome/browser/ui/layout_constants.cc"
    path, source = read_text(root, rel)
    marker = "PORTABLE_UI_COMPACT_TOOLBAR"
    replacements = [
        (
            "const int bookmark_bar_attached_vertical_margin = 6;",
            "const int bookmark_bar_attached_vertical_margin = 5;",
            "bookmark bar vertical margin",
        ),
        (
            "case BOOKMARK_BAR_BUTTON_HEIGHT:\n      return touch_ui ? 36 : 28;",
            "case BOOKMARK_BAR_BUTTON_HEIGHT:\n      return touch_ui ? 36 : 26;",
            "bookmark button height",
        ),
        (
            "case BOOKMARK_BAR_BUTTON_IMAGE_LABEL_PADDING:\n      return 6;",
            "case BOOKMARK_BAR_BUTTON_IMAGE_LABEL_PADDING:\n      return 5;",
            "bookmark icon-label gap",
        ),
        (
            "case LOCATION_BAR_ELEMENT_PADDING:\n      return touch_ui ? 3 : 2;",
            "case LOCATION_BAR_ELEMENT_PADDING:\n      return touch_ui ? 3 : 1;",
            "location-bar element padding",
        ),
        (
            "case LOCATION_BAR_HEIGHT:\n      return touch_ui ? 36 : 34;",
            "case LOCATION_BAR_HEIGHT:\n      return touch_ui ? 36 : 33;",
            "location-bar height",
        ),
        (
            "case TOOLBAR_BUTTON_HEIGHT:\n      return touch_ui ? 48 : 34;",
            "case TOOLBAR_BUTTON_HEIGHT:\n      return touch_ui ? 48 : 32;",
            "toolbar button height",
        ),
        (
            "case TOOLBAR_ELEMENT_PADDING:\n      return touch_ui ? 0 : 4;",
            "case TOOLBAR_ELEMENT_PADDING:\n      return touch_ui ? 0 : 3;",
            "toolbar element padding",
        ),
        (
            "case TOOLBAR_ICON_DEFAULT_MARGIN:\n      return touch_ui ? 0 : 2;",
            "case TOOLBAR_ICON_DEFAULT_MARGIN:\n      return touch_ui ? 0 : 1;",
            "toolbar icon margin",
        ),
        (
            "case TOOLBAR_STANDARD_SPACING:\n      return touch_ui ? 12 : 9;",
            "case TOOLBAR_STANDARD_SPACING:\n      return touch_ui ? 12 : 8;",
            "toolbar horizontal spacing",
        ),
        (
            "case TOOLBAR_BUTTON:\n      return gfx::Insets(touch_ui ? 12 : 7);",
            "case TOOLBAR_BUTTON:\n      return gfx::Insets(touch_ui ? 12 : 6);",
            "toolbar button insets",
        ),
        (
            "case TOOLBAR_INTERIOR_MARGIN:\n      return touch_ui ? gfx::Insets::VH(4, 0) : gfx::Insets::VH(6, 5);",
            "case TOOLBAR_INTERIOR_MARGIN:\n      return touch_ui ? gfx::Insets::VH(4, 0) : gfx::Insets::VH(5, 4);",
            "toolbar interior margin",
        ),
    ]
    if marker not in source:
        for old, new, label in replacements:
            source = replace_once_or_present(source, old, new, f"{rel}: {label}")
        anchor = "int GetLayoutConstant(LayoutConstant constant) {\n"
        source = replace_once_or_present(
            source,
            anchor,
            anchor + "  // PORTABLE_UI_COMPACT_TOOLBAR: modest desktop-only tightening.\n",
            rel,
        )
    else:
        for _old, new, label in replacements:
            if new not in source:
                raise RuntimeError(
                    f"{rel}: compact toolbar marker exists but {label} is missing"
                )
    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def apply_bookmark_scheme_colors(root: Path) -> None:
    rel = "chrome/browser/ui/views/bookmarks/bookmark_bar_view.cc"
    path, source = read_text(root, rel)
    marker = "PORTABLE_UI_BOOKMARK_SCHEME_COLORS"
    if marker not in source:
        anchor = "    button->SetImageModel(views::Button::STATE_NORMAL, std::move(favicon));\n"
        replacement = anchor + '''
    // PORTABLE_UI_BOOKMARK_SCHEME_COLORS
    // Give bookmarklets and data: tools a visual identity without changing
    // ordinary bookmarks or their favicons.
    if (node->url().SchemeIs("javascript")) {
      constexpr SkColor kBookmarkletColor = SkColorSetRGB(80, 180, 255);
      button->SetEnabledTextColors(kBookmarkletColor);
      button->SetImageModel(
          views::Button::STATE_NORMAL,
          ui::ImageModel::FromVectorIcon(vector_icons::kCodeIcon,
                                         kBookmarkletColor));
    } else if (node->url().SchemeIs("data")) {
      constexpr SkColor kDataBookmarkColor = SkColorSetRGB(255, 170, 80);
      button->SetEnabledTextColors(kDataBookmarkColor);
      button->SetImageModel(
          views::Button::STATE_NORMAL,
          ui::ImageModel::FromVectorIcon(vector_icons::kDatabaseIcon,
                                         kDataBookmarkColor));
    }
'''
        source = replace_once_or_present(source, anchor, replacement, rel)
    else:
        if "vector_icons::kCodeIcon" not in source or "vector_icons::kDatabaseIcon" not in source:
            raise RuntimeError(f"{rel}: bookmark URL scheme styling block is incomplete")
    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def apply_data_ntp_exclusion(root: Path) -> None:
    rel = "components/ntp_tiles/most_visited_sites.cc"
    path, source = read_text(root, rel)

    visited_marker = "PORTABLE_NTP_SKIP_DATA_MOST_VISITED"
    if visited_marker not in source:
        anchor = "    const history::MostVisitedURL& visited = visited_list[i];\n"
        insert = anchor + (
            "    // PORTABLE_NTP_SKIP_DATA_MOST_VISITED: data: pages are local tools, not shortcuts.\n"
            "    if (visited.url.SchemeIs(\"data\"))\n"
            "      continue;\n"
        )
        source = replace_once_or_present(
            source, anchor, insert, f"{rel}: most-visited filter"
        )

    custom_marker = "PORTABLE_NTP_SKIP_DATA_CUSTOM_LINKS"
    if custom_marker not in source:
        anchor = "    const CustomLinksManager::Link& link = links.at(i);\n"
        insert = anchor + (
            "    // PORTABLE_NTP_SKIP_DATA_CUSTOM_LINKS\n"
            "    if (link.url.SchemeIs(\"data\"))\n"
            "      continue;\n"
        )
        source = replace_once_or_present(source, anchor, insert, f"{rel}: custom-link filter")

    # Reject new data: URLs at the API boundary too, not only in display filtering.
    method_specs = [
        (
            "bool MostVisitedSites::AddCustomLinkTo(",
            "url",
            "PORTABLE_NTP_REJECT_DATA_ADD_TO",
        ),
        (
            "bool MostVisitedSites::AddCustomLink(",
            "url",
            "PORTABLE_NTP_REJECT_DATA_ADD",
        ),
        (
            "bool MostVisitedSites::UpdateCustomLink(",
            "new_url",
            "PORTABLE_NTP_REJECT_DATA_UPDATE",
        ),
    ]
    for signature, arg, marker in method_specs:
        if re.search(rf"(?m)^\s*// {re.escape(marker)}\s*$", source):
            continue
        start = source.find(signature)
        if start < 0:
            raise RuntimeError(f"{rel}: method not found: {signature}")
        brace = source.find("{", start)
        if brace < 0:
            raise RuntimeError(f"{rel}: body not found for {signature}")
        code = (
            f"\n  // {marker}\n"
            f"  if ({arg}.SchemeIs(\"data\"))\n"
            "    return false;\n"
        )
        source = source[: brace + 1] + code + source[brace + 1 :]

    write_if_changed(path, path.read_text(encoding="utf-8"), source)


def apply_bookmark_bar_autohide(root: Path) -> None:
    # Profile preference.
    rel = "components/bookmarks/common/bookmark_pref_names.h"
    path, source = read_text(root, rel)
    marker = "kPortableBookmarkBarAutoHide"
    if marker not in source:
        anchor = 'inline constexpr char kShowBookmarkBar[] = "bookmark_bar.show_on_all_tabs";\n'
        addition = anchor + (
            "// Whether the bookmark bar is revealed when the pointer enters the location bar.\n"
            "inline constexpr char kPortableBookmarkBarAutoHide[] =\n"
            '    "bookmark_bar.portable_auto_hide";\n'
        )
        source = replace_once_or_present(source, anchor, addition, rel)
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

    rel = "components/bookmarks/browser/bookmark_utils.cc"
    path, source = read_text(root, rel)
    marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_PREF"
    if marker not in source:
        anchor = "  registry->RegisterBooleanPref(prefs::kEditBookmarksEnabled, true);\n"
        addition = anchor + (
            "  // PORTABLE_BOOKMARK_BAR_AUTOHIDE_PREF\n"
            "  registry->RegisterBooleanPref(prefs::kPortableBookmarkBarAutoHide, false);\n"
        )
        source = replace_once_or_present(source, anchor, addition, rel)
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

    # New context-menu command.
    rel = "chrome/app/chrome_command_ids.h"
    path, source = read_text(root, rel)
    auto_hide_macro = re.compile(
        r"(?m)^#define\s+IDC_BOOKMARK_BAR_AUTO_HIDE\s+(\d+)\b"
    )
    auto_hide_match = auto_hide_macro.search(source)
    used_command_ids = {
        int(value)
        for value in re.findall(r"^#define\s+\w+\s+(\d+)\b", source, re.M)
    }
    if auto_hide_match:
        current_id = int(auto_hide_match.group(1))
        duplicate_id = re.search(
            rf"(?m)^#define\s+(?!IDC_BOOKMARK_BAR_AUTO_HIDE\b)\w+\s+{current_id}\b",
            source,
        )
        if duplicate_id:
            available_command_ids = [
                command_id for command_id in range(51024, 51030)
                if command_id not in used_command_ids
            ]
            if not available_command_ids:
                raise RuntimeError(f"{rel}: no unused bookmark-bar command ID in 51024-51029")
            source = auto_hide_macro.sub(
                f"#define IDC_BOOKMARK_BAR_AUTO_HIDE {available_command_ids[0]}",
                source,
                count=1,
            )
    else:
        anchor = "#define IDC_BOOKMARK_BAR_OPEN_SPLIT_VIEW 51023\n"
        available_command_ids = [
            command_id for command_id in range(51024, 51030)
            if command_id not in used_command_ids
        ]
        if not available_command_ids:
            raise RuntimeError(f"{rel}: no unused bookmark-bar command ID in 51024-51029")
        source = replace_once_or_present(
            source, anchor,
            anchor + f"#define IDC_BOOKMARK_BAR_AUTO_HIDE {available_command_ids[0]}\n", rel
        )
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

    # Pass an explicit flag through existing bookmark-menu objects. This avoids
    # guessing whether a one-item selection means a background context menu.
    rel = "chrome/browser/ui/bookmarks/bookmark_context_menu_controller.h"
    path, source = read_text(root, rel)
    old_sig = """      BookmarkLaunchLocation opened_from,
      const std::vector<raw_ptr<const bookmarks::BookmarkNode,
                                VectorExperimental>>& selection);"""
    new_sig = """      BookmarkLaunchLocation opened_from,
      const std::vector<raw_ptr<const bookmarks::BookmarkNode,
                                VectorExperimental>>& selection,
      bool is_bookmark_bar_background = false);"""
    source = replace_once_or_present(source, old_sig, new_sig, rel)
    if "is_bookmark_bar_background_" not in source:
        anchor = "  const BookmarkLaunchLocation opened_from_;\n"
        source = replace_once_or_present(
            source, anchor, anchor + "  const bool is_bookmark_bar_background_;\n", rel
        )
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

    rel = "chrome/browser/ui/bookmarks/bookmark_context_menu_controller.cc"
    path, source = read_text(root, rel)
    include = '#include "chrome/browser/ui/bookmarks/bookmark_bar.h"\n'
    if include not in source:
        anchor = '#include "chrome/browser/ui/bookmarks/bookmark_editor.h"\n'
        source = replace_once_or_present(source, anchor, anchor + include, rel)

    old_sig = """    BookmarkLaunchLocation opened_from,
    const std::vector<raw_ptr<const BookmarkNode, VectorExperimental>>&
        selection)
    : parent_window_(parent_window),"""
    new_sig = """    BookmarkLaunchLocation opened_from,
    const std::vector<raw_ptr<const BookmarkNode, VectorExperimental>>&
        selection,
    bool is_bookmark_bar_background)
    : parent_window_(parent_window),"""
    source = replace_once_or_present(source, old_sig, new_sig, rel)
    if "is_bookmark_bar_background_(is_bookmark_bar_background)" not in source:
        anchor = "      opened_from_(opened_from),\n"
        source = replace_once_or_present(
            source,
            anchor,
            anchor + "      is_bookmark_bar_background_(is_bookmark_bar_background),\n",
            rel,
        )

    menu_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_MENU_ITEM"
    if menu_marker not in source:
        anchor = "  AddCheckboxItem(IDC_BOOKMARK_BAR_ALWAYS_SHOW, IDS_SHOW_BOOKMARK_BAR);\n"
        addition = anchor + '''
  // PORTABLE_BOOKMARK_BAR_AUTOHIDE_MENU_ITEM
  if (is_bookmark_bar_background_) {
    AddSeparator();
    menu_model_->AddCheckItem(
        IDC_BOOKMARK_BAR_AUTO_HIDE, u"Автоскрытие панели закладок");
  }
'''
        source = replace_once_or_present(source, anchor, addition, rel)

    checked_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_CHECKED"
    if checked_marker not in source:
        sig = "bool BookmarkContextMenuController::IsCommandIdChecked(int command_id) const {\n"
        start = source.find(sig)
        if start < 0:
            raise RuntimeError(f"{rel}: IsCommandIdChecked not found")
        anchor = "  PrefService* prefs = profile_->GetPrefs();\n"
        pos = source.find(anchor, start)
        if pos < 0:
            raise RuntimeError(f"{rel}: PrefService anchor missing in IsCommandIdChecked")
        addition = anchor + (
            "  // PORTABLE_BOOKMARK_BAR_AUTOHIDE_CHECKED\n"
            "  if (command_id == IDC_BOOKMARK_BAR_AUTO_HIDE) {\n"
            "    return prefs->GetBoolean(bookmarks::prefs::kPortableBookmarkBarAutoHide);\n"
            "  }\n"
        )
        source = source[:pos] + addition + source[pos + len(anchor) :]

    enabled_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_ENABLED"
    if enabled_marker not in source:
        sig = "bool BookmarkContextMenuController::IsCommandIdEnabled(int command_id) const {\n"
        pos = source.find(sig)
        if pos < 0:
            raise RuntimeError(f"{rel}: IsCommandIdEnabled not found")
        brace = pos + len(sig)
        code = (
            "  // PORTABLE_BOOKMARK_BAR_AUTOHIDE_ENABLED\n"
            "  if (command_id == IDC_BOOKMARK_BAR_AUTO_HIDE) {\n"
            "    return is_bookmark_bar_background_;\n"
            "  }\n"
        )
        source = source[:brace] + code + source[brace:]

    execute_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_EXECUTE"
    if execute_marker not in source:
        anchor = "    case IDC_BOOKMARK_BAR_ALWAYS_SHOW:\n"
        addition = '''    // PORTABLE_BOOKMARK_BAR_AUTOHIDE_EXECUTE
    case IDC_BOOKMARK_BAR_AUTO_HIDE: {
      PrefService* prefs = profile_->GetPrefs();
      prefs->SetBoolean(
          bookmarks::prefs::kPortableBookmarkBarAutoHide,
          !prefs->GetBoolean(bookmarks::prefs::kPortableBookmarkBarAutoHide));
      if (browser_) {
        browser_->window()->BookmarkBarStateChanged(
            BookmarkBar::ANIMATE_STATE_CHANGE);
      }
      break;
    }
'''
        source = replace_once_or_present(source, anchor, addition + anchor, rel)
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

    rel = "chrome/browser/ui/views/bookmarks/bookmark_context_menu.h"
    path, source = read_text(root, rel)
    old_sig = """                      const std::vector<raw_ptr<const bookmarks::BookmarkNode,
                                                VectorExperimental>>& selection,
                      bool close_on_remove);"""
    new_sig = """                      const std::vector<raw_ptr<const bookmarks::BookmarkNode,
                                                VectorExperimental>>& selection,
                      bool close_on_remove,
                      bool is_bookmark_bar_background = false);"""
    source = replace_once_or_present(source, old_sig, new_sig, rel)
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

    rel = "chrome/browser/ui/views/bookmarks/bookmark_context_menu.cc"
    path, source = read_text(root, rel)
    old_sig = """    const std::vector<raw_ptr<const BookmarkNode, VectorExperimental>>&
        selection,
    bool close_on_remove)
    : controller_(new BookmarkContextMenuController("""
    new_sig = """    const std::vector<raw_ptr<const BookmarkNode, VectorExperimental>>&
        selection,
    bool close_on_remove,
    bool is_bookmark_bar_background)
    : controller_(new BookmarkContextMenuController("""
    source = replace_once_or_present(source, old_sig, new_sig, rel)
    anchor = "          opened_from,\n          selection)),\n"
    addition = "          opened_from,\n          selection,\n          is_bookmark_bar_background)),\n"
    source = replace_once_or_present(source, anchor, addition, rel)
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

    rel = "chrome/browser/ui/views/bookmarks/bookmark_bar_view.cc"
    path, source = read_text(root, rel)
    call_anchor = """      BookmarkLaunchLocation::kAttachedBar, ToRawPtrVector(nodes),
      close_on_remove);"""
    call_new = """      BookmarkLaunchLocation::kAttachedBar, ToRawPtrVector(nodes),
      close_on_remove, source == this);"""
    source = replace_once_or_present(source, call_anchor, call_new, rel)
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

    # Listen to window mouse movement before normal dispatch.
    rel = "chrome/browser/ui/views/frame/browser_view.h"
    path, source = read_text(root, rel)
    include = '#include "ui/events/event_handler.h"\n'
    if include not in source:
        anchor = '#include "ui/views/window/client_view.h"\n'
        source = replace_once_or_present(source, anchor, include + anchor, rel)
    if "public ui::EventHandler," not in source:
        source = replace_once_or_present(
            source,
            "                    public views::ClientView,\n",
            "                    public views::ClientView,\n                    public ui::EventHandler,\n",
            rel,
        )
    if "void OnMouseEvent(ui::MouseEvent* event) override;" not in source:
        anchor = "  void BookmarkBarStateChanged(\n      BookmarkBar::AnimateChangeType change_type) override;\n"
        source = replace_once_or_present(
            source, anchor,
            anchor + "  void OnMouseEvent(ui::MouseEvent* event) override;\n", rel
        )
    if "class EventTarget;" not in source:
        anchor = "namespace ui {\nclass NativeTheme;\n}  // namespace ui\n"
        source = replace_once_or_present(
            source, anchor,
            "namespace ui {\nclass EventTarget;\nclass NativeTheme;\n}  // namespace ui\n", rel
        )
    if "portable_bookmark_bar_event_target_" not in source:
        anchor = "  bool initialized_ = false;\n"
        members = (
            "  // PORTABLE_BOOKMARK_BAR_AUTOHIDE_STATE\n"
            "  raw_ptr<ui::EventTarget> portable_bookmark_bar_event_target_ = nullptr;\n"
            "  bool portable_bookmark_bar_revealed_ = false;\n"
        )
        source = replace_once_or_present(source, anchor, members + anchor, rel)
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

    rel = "chrome/browser/ui/views/frame/browser_view.cc"
    path, source = read_text(root, rel)
    for include in (
        '#include "components/bookmarks/common/bookmark_pref_names.h"',
        '#include "ui/events/event.h"',
        '#include "ui/events/event_target.h"',
    ):
        if include not in source:
            anchor = '#include "chrome/browser/profiles/profile.h"\n'
            source = replace_once_or_present(source, anchor, anchor + include + "\n", rel)

    event_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_MOUSE_EVENT"
    if event_marker not in source:
        anchor = "void BrowserView::AddedToWidget() {\n"
        code = '''void BrowserView::OnMouseEvent(ui::MouseEvent* event) {
  // PORTABLE_BOOKMARK_BAR_AUTOHIDE_MOUSE_EVENT
  if (!event || (event->type() != ui::ET_MOUSE_MOVED &&
                 event->type() != ui::ET_MOUSE_ENTERED)) {
    return;
  }
  if (!browser_ || !bookmark_bar_view_ ||
      !browser_->GetProfile()->GetPrefs()->GetBoolean(
          bookmarks::prefs::kPortableBookmarkBarAutoHide) ||
      bookmark_bar_state() != BookmarkBar::SHOW || !event->target()) {
    return;
  }

  const gfx::Point screen_point =
      event->target()->GetScreenLocation(*event);
  bool should_reveal = portable_bookmark_bar_revealed_;
  LocationBarView* location_bar = GetLocationBarView();
  if (!should_reveal && location_bar &&
      location_bar->GetBoundsInScreen().Contains(screen_point)) {
    should_reveal = true;
  } else if (should_reveal &&
             screen_point.y() > bookmark_bar_view_->GetBoundsInScreen().bottom()) {
    should_reveal = false;
  }

  if (should_reveal == portable_bookmark_bar_revealed_) {
    return;
  }
  portable_bookmark_bar_revealed_ = should_reveal;
  bookmark_bar_view_->SetBookmarkBarState(
      should_reveal ? BookmarkBar::SHOW : BookmarkBar::HIDDEN,
      BookmarkBar::ANIMATE_STATE_CHANGE);
  InvalidateLayout();
}

'''
        source = replace_once_or_present(source, anchor, code + anchor, rel)

    reg_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_REGISTER"
    if reg_marker not in source:
        anchor = "void BrowserView::AddedToWidget() {\n"
        addition = '''void BrowserView::AddedToWidget() {
  // PORTABLE_BOOKMARK_BAR_AUTOHIDE_REGISTER
  if (!portable_bookmark_bar_event_target_ && GetWidget() &&
      GetWidget()->GetNativeView()) {
    portable_bookmark_bar_event_target_ = GetWidget()->GetNativeView();
    portable_bookmark_bar_event_target_->AddPreTargetHandler(this);
  }
'''
        source = replace_once_or_present(source, anchor, addition, rel)

    unreg_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_UNREGISTER"
    if unreg_marker not in source:
        anchor = "void BrowserView::RemovedFromWidget() {\n"
        addition = '''void BrowserView::RemovedFromWidget() {
  // PORTABLE_BOOKMARK_BAR_AUTOHIDE_UNREGISTER
  if (portable_bookmark_bar_event_target_) {
    portable_bookmark_bar_event_target_->RemovePreTargetHandler(this);
    portable_bookmark_bar_event_target_ = nullptr;
  }
'''
        source = replace_once_or_present(source, anchor, addition, rel)

    # Respect the normal preference and state when tabs change or the view is created.
    state_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_STATE_CHANGED"
    if state_marker not in source:
        anchor = "    BookmarkBar::State new_state = bookmark_bar_state();\n"
        addition = anchor + '''    // PORTABLE_BOOKMARK_BAR_AUTOHIDE_STATE_CHANGED
    const bool auto_hide_bookmark_bar = browser_->GetProfile()->GetPrefs()->GetBoolean(
        bookmarks::prefs::kPortableBookmarkBarAutoHide);
    if (!auto_hide_bookmark_bar || new_state != BookmarkBar::SHOW) {
      portable_bookmark_bar_revealed_ = false;
    }
    if (auto_hide_bookmark_bar && new_state == BookmarkBar::SHOW &&
        !portable_bookmark_bar_revealed_) {
      new_state = BookmarkBar::HIDDEN;
    }
'''
        source = replace_once_or_present(source, anchor, addition, rel)

    tab_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_TAB_CHANGE"
    initial_marker = "PORTABLE_BOOKMARK_BAR_AUTOHIDE_INITIAL_STATE"
    anchor = '''    bookmark_bar_view_->SetBookmarkBarState(
        bookmark_bar_state(), BookmarkBar::DONT_ANIMATE_STATE_CHANGE);
'''
    if tab_marker not in source and initial_marker not in source:
        # Chromium 141 contains the same statement in both OnActiveTabChanged()
        # and MaybeShowBookmarkBar(). Replace in source order, preserving each
        # location's unique marker for safe reruns.
        count = source.count(anchor)
        if count != 2:
            raise RuntimeError(
                f"{rel}: expected two bookmark-bar state anchors, found {count}"
            )
        tab_addition = '''    // PORTABLE_BOOKMARK_BAR_AUTOHIDE_TAB_CHANGE
    BookmarkBar::State new_bookmark_state = bookmark_bar_state();
    const bool auto_hide_bookmark_bar = browser_->GetProfile()->GetPrefs()->GetBoolean(
        bookmarks::prefs::kPortableBookmarkBarAutoHide);
    if (!auto_hide_bookmark_bar || new_bookmark_state != BookmarkBar::SHOW) {
      portable_bookmark_bar_revealed_ = false;
    }
    if (auto_hide_bookmark_bar && new_bookmark_state == BookmarkBar::SHOW &&
        !portable_bookmark_bar_revealed_) {
      new_bookmark_state = BookmarkBar::HIDDEN;
    }
    bookmark_bar_view_->SetBookmarkBarState(
        new_bookmark_state, BookmarkBar::DONT_ANIMATE_STATE_CHANGE);
'''
        initial_addition = '''    // PORTABLE_BOOKMARK_BAR_AUTOHIDE_INITIAL_STATE
    BookmarkBar::State initial_bookmark_state = bookmark_bar_state();
    const bool auto_hide_bookmark_bar = browser_->GetProfile()->GetPrefs()->GetBoolean(
        bookmarks::prefs::kPortableBookmarkBarAutoHide);
    if (!auto_hide_bookmark_bar || initial_bookmark_state != BookmarkBar::SHOW) {
      portable_bookmark_bar_revealed_ = false;
    }
    if (auto_hide_bookmark_bar && initial_bookmark_state == BookmarkBar::SHOW &&
        !portable_bookmark_bar_revealed_) {
      initial_bookmark_state = BookmarkBar::HIDDEN;
    }
    bookmark_bar_view_->SetBookmarkBarState(
        initial_bookmark_state, BookmarkBar::DONT_ANIMATE_STATE_CHANGE);
'''
        source = source.replace(anchor, tab_addition, 1)
        source = source.replace(anchor, initial_addition, 1)
    elif tab_marker in source and initial_marker in source:
        pass
    else:
        raise RuntimeError(f"{rel}: partial bookmark-bar auto-hide state patch")
    write_if_changed(path, path.read_text(encoding="utf-8"), source)

def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise RuntimeError("Usage: apply_portable_ui_features.py <chromium-source-tree>")
        root = Path(sys.argv[1]).resolve()
        if not (root / "chrome/browser/ui/tabs/tab_strip_model.cc").is_file():
            raise RuntimeError(f"Not a Chromium source tree: {root}")
        apply_menu_density(root)
        apply_menu_roundness(root)
        apply_app_menu_density(root)
        apply_compact_toolbar(root)
        apply_tab_discard(root)
        apply_web_contents_discard(root)
        apply_hovercard_overrides(root)
        apply_task_manager_stable_order(root)
        apply_bookmark_scheme_colors(root)
        apply_data_ntp_exclusion(root)
        apply_bookmark_bar_autohide(root)
        print(
            "Applied compact UI, native proactive discard, discarded-only hover-card "
            "details, stable Task Manager ordering, bookmark URL styling, data-URL "
            "NTP filtering and optional bookmark-bar auto-hide."
        )
        return 0
    except Exception as exc:
        print(f"ERROR applying portable UI features: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
