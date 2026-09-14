"""The retention hub discovers contributors by module NAME. A name that
begins with ``system.`` is an upward dependency the import guard cannot
see — the system layer registers its own rules at boot instead
(``system.bootstrap.install`` imports ``system.security.retention``)."""

from capabilities.data_lifecycle.retention import _CONTRIBUTORS


def test_no_contributor_names_the_system_layer():
    upward = [m for m in _CONTRIBUTORS if m == "system" or m.startswith("system.")]
    assert not upward, (
        f"retention contributors reach up into the system layer: {upward}. "
        "The system layer imports its own retention module at boot."
    )
