"""Backward-compat shim — real implementation moved to infra.crypto."""
from infra.crypto import *  # noqa: F401, F403
from infra.crypto import (  # noqa: F401
    init_encryption, is_enabled, encrypt, decrypt,
    _ENC_PREFIX,
)
