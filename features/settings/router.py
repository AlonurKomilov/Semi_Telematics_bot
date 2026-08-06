"""Settings feature — composes its component routers.

Each Settings component is a vertical slice under ``features/settings/<x>/``
with its own ``router.py`` (and ``service.py`` where it has real domain
logic), exactly like the Vehicles feature's components.  This module just
mounts them; they all share the historical ``/admin`` URL prefix.

Capability-backed Settings components live in capabilities/ instead
(Permissions, Storage, Integrations) — they're consumed beyond Settings.
"""
from fastapi import APIRouter

from .team_management.router import router as _team_management
from .invites.router import router as _invites
from .companies.router import router as _companies
from .audit.router import router as _audit
from .account.router import router as _account
from .account.config import router as _account_config_legacy
from .account.config import config_router as _account_config
from .work_hours.router import router as _work_hours

router = APIRouter()
for _component_router in (
    _team_management, _invites, _companies, _audit,
    _account, _account_config, _account_config_legacy, _work_hours,
):
    router.include_router(_component_router)
