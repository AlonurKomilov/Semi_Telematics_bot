"""Tests for keyboard builders — role-aware menus, sub-menus, and navigation."""


from adapters.storage import Role
from interfaces.bot.keyboards import (
    main_menu_kb,
    submenu_reports_kb,
    submenu_tools_kb,
    submenu_costs_kb,
    submenu_mgmt_kb,
    back_kb,
    faults_format_kb,
    fuel_format_kb,
    health_format_kb,
    efficiency_format_kb,
    scorecard_format_kb,
    fuelcost_menu_kb,
    costmile_format_kb,
    maintenance_menu_kb,
    livemap_refresh_kb,
    route_date_kb,
    geofence_list_kb,
    unregistered_kb,
    invite_kb,
    scheduled_reports_menu_kb,
    maint_company_picker_kb,
    maint_vehicle_list_kb,
    maint_type_kb,
    maint_due_kb,
    maint_miles_kb,
    maint_desc_kb,
    maint_task_detail_kb,
    maint_edit_kb,
    maint_delete_confirm_kb,
    maint_task_list_kb,
)


# ── Helpers ───────────────────────────────────────────────────────

def _all_callbacks(markup) -> list[str]:
    """Extract all callback_data strings from an InlineKeyboardMarkup."""
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data
    ]


def _all_labels(markup) -> list[str]:
    """Extract all button text labels from an InlineKeyboardMarkup."""
    return [
        btn.text
        for row in markup.inline_keyboard
        for btn in row
    ]


def _has_callback(markup, data: str) -> bool:
    return data in _all_callbacks(markup)


def _has_label_containing(markup, text: str) -> bool:
    return any(text in lbl for lbl in _all_labels(markup))


# ══════════════════════════════════════════════════════════════════
# MAIN MENU
# ══════════════════════════════════════════════════════════════════

class TestMainMenu:
    """Main menu keyboard — grouped sub-menus, role-aware."""

    def test_owner_sees_all_submenus(self):
        kb = main_menu_kb(Role.OWNER, ["CO1"])
        callbacks = _all_callbacks(kb)
        assert "submenu_reports" in callbacks
        assert "submenu_tools" in callbacks
        assert "submenu_costs" in callbacks
        assert "cmd_alerts" in callbacks
        assert "submenu_mgmt" in callbacks

    def test_driver_sees_my_truck(self):
        kb = main_menu_kb(Role.DRIVER, ["CO1"])
        assert _has_callback(kb, "cmd_myvehicle")

    def test_driver_no_my_truck_when_no_api(self):
        kb = main_menu_kb(Role.DRIVER, [])
        assert not _has_callback(kb, "cmd_myvehicle")

    def test_no_api_shows_integration_prompt_for_owner(self):
        kb = main_menu_kb(Role.OWNER, [])
        assert _has_callback(kb, "cmd_integrate_guide")

    def test_no_api_shows_waiting_for_driver(self):
        kb = main_menu_kb(Role.DRIVER, [])
        assert _has_callback(kb, "cmd_no_api_info")

    def test_driver_no_management(self):
        kb = main_menu_kb(Role.DRIVER, ["CO1"])
        assert not _has_callback(kb, "submenu_mgmt")

    def test_all_roles_see_settings(self):
        for role in (Role.OWNER, Role.ADMIN, Role.DISPATCHER, Role.DRIVER):
            kb = main_menu_kb(role, ["CO1"])
            assert _has_callback(kb, "cmd_settings"), f"{role} should see settings"

    def test_multi_company_shows_company_buttons(self):
        kb = main_menu_kb(Role.OWNER, ["CO1", "CO2"])
        callbacks = _all_callbacks(kb)
        assert "co_CO1" in callbacks
        assert "co_CO2" in callbacks

    def test_single_company_no_company_buttons(self):
        kb = main_menu_kb(Role.OWNER, ["CO1"])
        callbacks = _all_callbacks(kb)
        assert "co_CO1" not in callbacks

    def test_dispatcher_sees_limited_menu(self):
        kb = main_menu_kb(Role.DISPATCHER, ["CO1"])
        callbacks = _all_callbacks(kb)
        # Dispatcher has fuel + truck, so reports exist
        assert "submenu_reports" in callbacks
        # No management
        assert "submenu_mgmt" not in callbacks


# ══════════════════════════════════════════════════════════════════
# SUB-MENUS
# ══════════════════════════════════════════════════════════════════

class TestSubMenuReports:

    def test_owner_sees_all_report_options(self):
        kb = submenu_reports_kb(Role.OWNER)
        callbacks = _all_callbacks(kb)
        assert "cmd_faults" in callbacks
        assert "cmd_fuel" in callbacks
        assert "cmd_health" in callbacks
        assert "cmd_efficiency" in callbacks
        assert "cmd_events" in callbacks
        assert "cmd_vehicle_prompt" in callbacks
        assert "cmd_auto_reports" in callbacks

    def test_has_back_button(self):
        kb = submenu_reports_kb(Role.OWNER)
        assert _has_callback(kb, "cmd_menu")

    def test_driver_sees_only_events_and_back(self):
        """Drivers can see events (can_events_own) + back."""
        kb = submenu_reports_kb(Role.DRIVER)
        callbacks = _all_callbacks(kb)
        assert "cmd_events" in callbacks
        assert "cmd_menu" in callbacks


class TestSubMenuTools:

    def test_owner_sees_all_tools(self):
        kb = submenu_tools_kb(Role.OWNER)
        callbacks = _all_callbacks(kb)
        assert "cmd_scorecards" in callbacks
        assert "cmd_livemap" in callbacks
        assert "cmd_route" in callbacks
        assert "cmd_geofences" in callbacks
        assert "cmd_maintenance" in callbacks

    def test_has_back_button(self):
        kb = submenu_tools_kb(Role.OWNER)
        assert _has_callback(kb, "cmd_menu")

    def test_driver_sees_own_only(self):
        """Driver has scorecard_own, location_own, route_own, geofence_own."""
        kb = submenu_tools_kb(Role.DRIVER)
        callbacks = _all_callbacks(kb)
        assert "cmd_scorecards" in callbacks
        assert "cmd_livemap" in callbacks
        assert "cmd_route" in callbacks
        assert "cmd_geofences" in callbacks


class TestSubMenuCosts:

    def test_owner_sees_all_cost_options(self):
        kb = submenu_costs_kb(Role.OWNER)
        callbacks = _all_callbacks(kb)
        assert "cmd_fuelcost" in callbacks
        assert "cmd_costmile" in callbacks

    def test_driver_sees_no_cost_options(self):
        kb = submenu_costs_kb(Role.DRIVER)
        callbacks = _all_callbacks(kb)
        assert "cmd_fuelcost" not in callbacks
        assert "cmd_costmile" not in callbacks

    def test_has_back_button(self):
        kb = submenu_costs_kb(Role.OWNER)
        assert _has_callback(kb, "cmd_menu")


class TestSubMenuMgmt:

    def test_owner_sees_all_mgmt_options(self):
        kb = submenu_mgmt_kb(Role.OWNER, has_api=True)
        callbacks = _all_callbacks(kb)
        assert "cmd_account" in callbacks
        assert "cmd_users" in callbacks
        # Invite, Groups, Add Company, API Status are now inside sub-views
        assert "cmd_invite_pick" not in callbacks
        assert "cmd_addcompany_prompt" not in callbacks
        assert "cmd_api_status" not in callbacks

    def test_admin_no_company_management(self):
        kb = submenu_mgmt_kb(Role.ADMIN, has_api=True)
        callbacks = _all_callbacks(kb)
        assert "cmd_account" not in callbacks
        # But can manage users
        assert "cmd_users" in callbacks

    def test_has_back_button(self):
        kb = submenu_mgmt_kb(Role.OWNER)
        assert _has_callback(kb, "cmd_menu")


# ══════════════════════════════════════════════════════════════════
# BACK NAVIGATION
# ══════════════════════════════════════════════════════════════════

class TestBackNavigation:
    """Every keyboard must have a back button leading to a valid target."""

    def test_back_kb_goes_to_main(self):
        kb = back_kb()
        assert _has_callback(kb, "cmd_menu")

    def test_report_keyboards_back_to_submenu(self):
        """Report-related keyboards should go back to submenu_reports."""
        for kb_fn in (faults_format_kb, fuel_format_kb, health_format_kb, efficiency_format_kb):
            kb = kb_fn()
            assert _has_callback(kb, "submenu_reports"), (
                f"{kb_fn.__name__} should have back to submenu_reports"
            )

    def test_tool_keyboards_back_to_submenu(self):
        """Tool keyboards should go back to submenu_tools."""
        for kb_fn in (scorecard_format_kb, livemap_refresh_kb):
            kb = kb_fn()
            assert _has_callback(kb, "submenu_tools"), (
                f"{kb_fn.__name__} should have back to submenu_tools"
            )

    def test_cost_keyboards_back_to_submenu(self):
        """Cost keyboards should go back to submenu_costs."""
        for kb_fn in (fuelcost_menu_kb, costmile_format_kb):
            kb = kb_fn()
            assert _has_callback(kb, "submenu_costs"), (
                f"{kb_fn.__name__} should have back to submenu_costs"
            )

    def test_maintenance_back_to_tools(self):
        """Maintenance keyboard should go back to submenu_tools."""
        kb = maintenance_menu_kb()
        assert _has_callback(kb, "submenu_tools")

    def test_route_date_kb_back_to_tools(self):
        kb = route_date_kb("Truck101", "CO1")
        assert _has_callback(kb, "submenu_tools")

    def test_geofence_list_kb_back_to_tools(self):
        kb = geofence_list_kb([{"id": "g1", "name": "Yard"}])
        assert _has_callback(kb, "submenu_tools")


# ══════════════════════════════════════════════════════════════════
# SPECIAL KEYBOARDS
# ══════════════════════════════════════════════════════════════════

class TestSpecialKeyboards:

    def test_unregistered_kb_has_request_access(self):
        kb = unregistered_kb()
        labels = _all_labels(kb)
        assert any("Request Access" in lbl for lbl in labels)
        # Should be a URL button pointing to Telegram contact
        btn = kb.inline_keyboard[0][0]
        assert btn.url and "t.me/" in btn.url

    def test_invite_kb_has_share_button(self):
        kb = invite_kb(invite_link="https://t.me/testbot?start=ABCD-1234")
        labels = _all_labels(kb)
        assert any("Send to Team Member" in lbl for lbl in labels)

    def test_invite_kb_without_link(self):
        kb = invite_kb()
        assert _has_callback(kb, "cmd_users")
        # No share button
        assert not _has_label_containing(kb, "Send to Team Member")

    def test_scheduled_reports_menu_without_subscription(self):
        kb = scheduled_reports_menu_kb(current_sub=None)
        callbacks = _all_callbacks(kb)
        assert "ar_freq_daily" in callbacks
        assert "ar_freq_weekly" in callbacks
        assert "ar_freq_monthly" in callbacks

    def test_scheduled_reports_menu_with_subscription(self):
        kb = scheduled_reports_menu_kb(current_sub={"frequency": "daily", "send_hour": 7, "report_type": "faults"})
        callbacks = _all_callbacks(kb)
        assert "ar_unsub" in callbacks
        assert "ar_freq_daily" not in callbacks

    def test_geofence_list_max_15(self):
        """geofence_list_kb should cap at 15 items."""
        geofences = [{"id": f"g{i}", "name": f"Zone {i}"} for i in range(20)]
        kb = geofence_list_kb(geofences)
        # 15 geofences + 1 back button = 16 rows
        assert len(kb.inline_keyboard) == 16


# ══════════════════════════════════════════════════════════════════
# MAINTENANCE KEYBOARDS (new truck picker + CRUD)
# ══════════════════════════════════════════════════════════════════

class TestMaintCompanyPicker:

    def test_shows_all_companies(self):
        kb = maint_company_picker_kb(["CO1", "CO2", "CO3"])
        callbacks = _all_callbacks(kb)
        assert "maint_co_CO1" in callbacks
        assert "maint_co_CO2" in callbacks
        assert "maint_co_CO3" in callbacks

    def test_has_back_to_maintenance(self):
        kb = maint_company_picker_kb(["CO1"])
        assert _has_callback(kb, "cmd_maintenance")


class TestMaintVehicleList:

    def test_first_page(self):
        vehicles = [{"name": f"T{i}", "_org": "CO1"} for i in range(12)]
        kb = maint_vehicle_list_kb(vehicles, page=0, company_filter="CO1")
        callbacks = _all_callbacks(kb)
        # 8 trucks on first page
        truck_cbs = [c for c in callbacks if c.startswith("maint_vehicle_")]
        assert len(truck_cbs) == 8
        # Has Next, no Prev
        assert any("Next" in lbl for lbl in _all_labels(kb))

    def test_second_page(self):
        vehicles = [{"name": f"T{i}", "_org": "CO1"} for i in range(12)]
        kb = maint_vehicle_list_kb(vehicles, page=1, company_filter="CO1")
        callbacks = _all_callbacks(kb)
        truck_cbs = [c for c in callbacks if c.startswith("maint_vehicle_")]
        assert len(truck_cbs) == 4  # remaining 4
        assert any("Prev" in lbl for lbl in _all_labels(kb))

    def test_single_page_no_nav(self):
        vehicles = [{"name": f"T{i}", "_org": "CO1"} for i in range(3)]
        kb = maint_vehicle_list_kb(vehicles, page=0)
        labels = _all_labels(kb)
        assert not any("Prev" in lbl or "Next" in lbl for lbl in labels)

    def test_has_back_to_add(self):
        vehicles = [{"name": "T1", "_org": "CO1"}]
        kb = maint_vehicle_list_kb(vehicles, page=0)
        assert _has_callback(kb, "maint_add")


class TestMaintTypeKb:

    def test_has_all_ten_types(self):
        kb = maint_type_kb()
        callbacks = _all_callbacks(kb)
        expected = ["oil", "tires", "brakes", "inspection", "transmission",
                     "electrical", "dot_inspection", "dpf_regen", "def_refill", "custom"]
        for t in expected:
            assert f"maint_type_{t}" in callbacks

    def test_two_column_layout(self):
        kb = maint_type_kb()
        # 10 types in pairs of 2 = 5 rows + 1 cancel row = 6
        assert len(kb.inline_keyboard) == 6

    def test_has_cancel(self):
        kb = maint_type_kb()
        assert _has_callback(kb, "cmd_maintenance")


class TestMaintSkipButtons:

    def test_due_kb_has_skip(self):
        kb = maint_due_kb()
        assert _has_callback(kb, "maint_skip_date")

    def test_miles_kb_has_skip(self):
        kb = maint_miles_kb()
        assert _has_callback(kb, "maint_skip_miles")

    def test_desc_kb_has_skip(self):
        kb = maint_desc_kb()
        assert _has_callback(kb, "maint_skip_desc")

    def test_all_have_cancel(self):
        for fn in (maint_due_kb, maint_miles_kb, maint_desc_kb):
            kb = fn()
            assert _has_callback(kb, "cmd_maintenance"), f"{fn.__name__} missing cancel"


class TestMaintTaskDetail:

    def test_pending_shows_done_and_edit(self):
        kb = maint_task_detail_kb(42, "pending")
        callbacks = _all_callbacks(kb)
        assert "maint_done_42" in callbacks
        assert "maint_edit_42" in callbacks
        assert "maint_del_42" in callbacks

    def test_overdue_shows_done_and_edit(self):
        kb = maint_task_detail_kb(7, "overdue")
        callbacks = _all_callbacks(kb)
        assert "maint_done_7" in callbacks
        assert "maint_edit_7" in callbacks

    def test_done_hides_done_and_edit(self):
        kb = maint_task_detail_kb(7, "done")
        callbacks = _all_callbacks(kb)
        assert "maint_done_7" not in callbacks
        assert "maint_edit_7" not in callbacks
        # Delete still available
        assert "maint_del_7" in callbacks

    def test_back_to_task_list(self):
        kb = maint_task_detail_kb(99, "pending")
        assert _has_callback(kb, "maint_view")


class TestMaintEditKb:

    def test_has_all_fields(self):
        kb = maint_edit_kb(10)
        callbacks = _all_callbacks(kb)
        assert "maint_etype_10" in callbacks
        assert "maint_edate_10" in callbacks
        assert "maint_emiles_10" in callbacks
        assert "maint_edesc_10" in callbacks

    def test_back_to_detail(self):
        kb = maint_edit_kb(10)
        assert _has_callback(kb, "maint_detail_10")


class TestMaintDeleteConfirm:

    def test_confirm_and_cancel(self):
        kb = maint_delete_confirm_kb(5)
        callbacks = _all_callbacks(kb)
        assert "maint_delok_5" in callbacks
        assert "maint_detail_5" in callbacks


class TestMaintTaskList:

    def test_shows_tasks(self):
        tasks = [
            {"id": 1, "vehicle_name": "T100", "task_type": "oil", "status": "pending"},
            {"id": 2, "vehicle_name": "T200", "task_type": "brakes", "status": "done"},
        ]
        kb = maint_task_list_kb(tasks)
        callbacks = _all_callbacks(kb)
        assert "maint_detail_1" in callbacks
        assert "maint_detail_2" in callbacks

    def test_pagination(self):
        tasks = [
            {"id": i, "vehicle_name": f"T{i}", "task_type": "oil", "status": "pending"}
            for i in range(12)
        ]
        kb = maint_task_list_kb(tasks, page=0)
        labels = _all_labels(kb)
        assert any("Next" in lbl for lbl in labels)

    def test_back_to_maintenance(self):
        kb = maint_task_list_kb([])
        assert _has_callback(kb, "cmd_maintenance")
