"""KPI — the dispatch section.

The subject is DISPATCHERS: the people being measured and (with the
incentive engine) paid.  Subject noun, not a viewing persona — the same
naming precedent as ``/kpi/dispatchers`` and ``features/driver_pay``.

Two different products live here and must not be confused:

  grades.py   A–D performance GRADES — shared analytics, ``can_view_kpi``,
              viewable widely.  No money.
  engine.py   the incentive ENGINE — computes compensation.  Pure
              functions; the surfaces that expose its output carry their
              own permission, because payout amounts are compensation
              data and ``can_view_kpi`` must never leak them.
"""
