"""Fleet efficiency report formatter."""

from datetime import datetime
from constants import TZ_ET as _TZ_ET
from capabilities.formatting.helpers import (
    _t, _engine_bar, _company_tag, _split_message,
)


def format_fleet_efficiency(
    vehicles: list[dict],
    days: int = 7,
    show_company: bool = False,
) -> list[str]:
    """Format merged efficiency data (engine hours + driver metrics).

    Each vehicle dict has engine-hours fields (always present) and
    optional driver fields (_driver_name, _fuel_gal, _mpg, etc.)
    which are None when no driver is assigned.
    """
    if not vehicles:
        return [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {_t('reports.efficiency_fmt_none')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {_t('reports.efficiency_fmt_no_data').replace('{days}', str(days))}"
        ]

    total_eng_s = sum(v.get("_engine_s", v["_engine_hours"] * 3600) for v in vehicles)
    total_drive_s = sum(v.get("_driving_s", v["_driving_hours"] * 3600) for v in vehicles)
    total_idle_s = sum(v.get("_idle_s", v["_idle_hours"] * 3600) for v in vehicles)
    total_eng = total_eng_s / 3600
    total_drive = total_drive_s / 3600
    total_idle = total_idle_s / 3600
    total_miles = sum(v.get("_miles", 0) for v in vehicles)
    avg_drive_pct = (total_drive_s / total_eng_s * 100) if total_eng_s > 0 else 0

    with_driver = [v for v in vehicles if v.get("_driver_name")]
    total_fuel = sum(v["_fuel_gal"] for v in with_driver if v.get("_fuel_gal"))
    fuel_miles = sum(v.get("_miles", 0) for v in with_driver)
    fleet_mpg = fuel_miles / total_fuel if total_fuel > 0 else 0

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('reports.efficiency_fmt_title')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>{len(vehicles)}</b> trucks  ·  "
        f"👤 <b>{len(with_driver)}</b> drivers  ·  Past {days} days",
        f"  ⏱  <b>{total_eng:,.1f}h</b> engine  ·  "
        f"🚗 {total_drive:,.1f}h drive  ·  🅿️ {total_idle:,.1f}h idle",
        f"  🛣  <b>{total_miles:,}</b> mi  ·  "
        f"📈 {avg_drive_pct:.0f}% driving  ·  "
        f"⛽ {fleet_mpg:.1f} MPG",
        "",
        "  ── ── ── ── ── ── ── ──",
        "",
    ]

    for v in vehicles:
        name = v["name"]
        eng_h = v["_engine_hours"]
        drv_h = v["_driving_hours"]
        idle_h = v["_idle_hours"]
        drv_pct = v["_driving_pct"]
        idle_pct = v["_idle_pct"]
        tag = _company_tag(v, show_company)
        miles = v.get("_miles", 0)
        bar = _engine_bar(drv_pct)

        driver = v.get("_driver_name")
        if driver:
            mpg = v.get("_mpg", 0)
            eco = v.get("_green_pct", 0)
            ovr = v.get("_overspeed_min", 0)
            antic = v.get("_antic_brakes")
            total_brk = v.get("_total_brakes")
            brk_txt = f"🛑 {antic}/{total_brk}" if antic is not None else ""
            lines.append(
                f"  <b>{tag}#{name}</b>  ⏱ {eng_h}h · 🛣 {miles:,}mi\n"
                f"  {bar} 🚗 {drv_h}h ({drv_pct}%) · 🅿️ {idle_h}h ({idle_pct}%)\n"
                f"  👤 {driver}  ·  ⛽ {mpg}mpg  ·  "
                f"🌿 {eco}%  ·  ⚡ {ovr}m  {brk_txt}\n"
            )
        else:
            lines.append(
                f"  <b>{tag}#{name}</b>  ⏱ {eng_h}h · 🛣 {miles:,}mi\n"
                f"  {bar} 🚗 {drv_h}h ({drv_pct}%) · 🅿️ {idle_h}h ({idle_pct}%)\n"
            )

    now_et = datetime.now(_TZ_ET)
    lines.append(f"  🕐  {now_et.strftime('%b %d, %Y  %I:%M %p')} EST")

    return _split_message("\n".join(lines))
