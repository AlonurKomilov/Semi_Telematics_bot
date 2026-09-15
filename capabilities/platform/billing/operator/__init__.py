"""The operator console's surface onto billing.

Separate from the customer's surface next door because the audiences
are separate: this one is gated by ``require_system_owner`` and is
reached only from system.4truck.us.
"""

from capabilities.platform.billing.operator.router import router

__all__ = ["router"]
