"""Per-row kind derivation for the Alerts Board (_kind_from_row).

The Board's Type column names the specific thing when the stored
alert_subkey provably encodes one — event sub-kinds and the parking
classifier's location class.  Everything else must come back empty so
the UI falls back to the generic type label instead of showing a
timestamp fragment as a "kind".
"""

from capabilities.alerting.router import _kind_from_row


class TestEventKinds:
    def test_bare_event_type_subkey(self):
        # dedup mode keeps the caller subkey as-is
        assert _kind_from_row("events", "braking") == "braking"

    def test_prefixed_event_type_subkey(self):
        # no-dedup mode appends timestamp + detail after the prefix
        assert _kind_from_row("events", "rollingStop:2026-07-27T10:00:eid42") == "rollingStop"

    def test_singular_variant(self):
        assert _kind_from_row("event", "crash:2026-07-27T10:00:eid7") == "crash"

    def test_timestamp_only_subkey_is_not_a_kind(self):
        # older rows fired without an event_type: "{ts}:{detail}"
        assert _kind_from_row("events", "2026-07-27T10:00:eid42") == ""

    def test_empty_subkey(self):
        assert _kind_from_row("events", "") == ""


class TestParkingKinds:
    def test_unsafe_class(self):
        assert _kind_from_row("parking", "2026-07-25T09:00:parking:unsafe:8h") == "unsafe"

    def test_unknown_class(self):
        assert _kind_from_row("parking", "2026-07-25T09:00:parking:unknown:9h") == "unknown"

    def test_dedup_mode_subkey_without_timestamp(self):
        assert _kind_from_row("parking", "parking:unsafe:8h") == "unsafe"

    def test_unparseable_subkey(self):
        assert _kind_from_row("parking", "2026-07-25T09:00:whatever") == ""


class TestOtherTypes:
    def test_types_without_kind_encoding_stay_empty(self):
        # fuel/faults/health subkeys carry timestamps + details, never a
        # kind vocabulary — they must not leak fragments into the UI.
        assert _kind_from_row("fuel", "2026-07-25T09:00:fuel:8") == ""
        assert _kind_from_row("faults", "2026-07-25T09:00:spn100-fmi1") == ""
        assert _kind_from_row("camera", "anything:at:all") == ""
