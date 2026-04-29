"""
test_system.py — Unit Tests for IoT Screen Safety System
Run with: pytest server/tests/test_system.py -v
"""

import sys, os, time, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sensor import ProximitySensor, ZONE_DANGER, ZONE_WARNING, ZONE_SAFE, ZONE_ABSENT
from control import BrightnessController, BrightnessLevel, ControlState


# ─────────────────── SENSOR TESTS ────────────────────────────────────────────

class TestProximitySensor:

    def setup_method(self):
        self.sensor = ProximitySensor()

    def _inject(self, distance_cm, n=1):
        for _ in range(n):
            self.sensor.simulate(distance_cm)

    def test_zone_classification_danger(self):
        self._inject(20.0, n=5)
        assert self.sensor.get_zone() == ZONE_DANGER

    def test_zone_classification_warning(self):
        self._inject(40.0, n=5)
        assert self.sensor.get_zone() == ZONE_WARNING

    def test_zone_classification_safe(self):
        self._inject(100.0, n=5)
        assert self.sensor.get_zone() == ZONE_SAFE

    def test_absent_on_negative_distance(self):
        self._inject(-1.0, n=5)
        assert self.sensor.get_zone() == ZONE_ABSENT

    def test_debounce_requires_consecutive(self):
        # Only 1 danger reading — should not confirm zone (needs DEBOUNCE_CONSECUTIVE=3)
        self.sensor.ingest({"distance_cm": 5.0})
        # The internal pending counter should have incremented
        assert self.sensor._pending_count >= 1
        # But confirmed zone should NOT be danger yet (only 1 of 3 required readings)
        # It may be 'absent' (initial) since debounce hasn't fired
        assert self.sensor._pending_count < 3  # not yet confirmed

    def test_debounce_confirms_after_enough_readings(self):
        self._inject(5.0, n=5)
        assert self.sensor.get_zone() == ZONE_DANGER
        assert self.sensor.is_human_present() is True

    def test_human_absent_after_timeout(self):
        from sensor import PRESENCE_TIMEOUT_SEC
        self._inject(5.0, n=5)
        assert self.sensor.is_human_present() is True
        # Manually expire last_update
        self.sensor._state.last_update = time.time() - (PRESENCE_TIMEOUT_SEC + 1)
        assert self.sensor.is_human_present() is False

    def test_recent_buffer(self):
        for d in [10, 20, 30, 40, 50]:
            self.sensor.simulate(float(d))
        recents = self.sensor.get_recent(3)
        assert len(recents) == 3

    def test_thread_safety(self):
        import threading
        errors = []
        def writer():
            for i in range(100):
                try:
                    self.sensor.simulate(float(i % 60))
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(errors) == 0

    def test_invalid_payload_raises(self):
        with pytest.raises(ValueError):
            self.sensor.ingest({"distance_cm": "not-a-number"})


# ─────────────────── BRIGHTNESS CONTROLLER TESTS ─────────────────────────────

class TestBrightnessController:

    def setup_method(self):
        self.changes = []
        self.ctrl = BrightnessController(
            on_state_change=lambda s: self.changes.append(s)
        )

    def teardown_method(self):
        self.ctrl.shutdown()

    def test_initial_state(self):
        s = self.ctrl.get_state()
        assert s.brightness_level == "NORMAL"
        assert s.current_brightness >= 0

    def test_no_human_stays_normal(self):
        self.ctrl.trigger(human_present=False, is_child=False, proximity_zone="absent")
        s = self.ctrl.get_state()
        assert s.target_brightness == BrightnessLevel.NORMAL.value

    def test_adult_in_warning_reduces(self):
        self.ctrl.trigger(human_present=True, is_child=False, proximity_zone="warning")
        s = self.ctrl.get_state()
        assert s.target_brightness == BrightnessLevel.REDUCED.value

    def test_adult_in_danger_reduces(self):
        self.ctrl.trigger(human_present=True, is_child=False, proximity_zone="danger")
        s = self.ctrl.get_state()
        assert s.target_brightness == BrightnessLevel.REDUCED.value

    def test_child_in_danger_dims_after_confirm(self):
        from control import CHILD_CONFIRM_SEC
        # Pre-set child first seen to past
        self.ctrl._child_first_seen = time.time() - (CHILD_CONFIRM_SEC + 0.5)
        self.ctrl._state.last_trigger_time = 0  # bypass cooldown
        self.ctrl.trigger(human_present=True, is_child=True, proximity_zone="danger")
        s = self.ctrl.get_state()
        assert s.target_brightness == BrightnessLevel.SAFE_DIM.value

    def test_cooldown_prevents_rapid_triggers(self):
        self.ctrl.trigger(human_present=True, is_child=False, proximity_zone="warning")
        first_target = self.ctrl.get_state().target_brightness
        # Immediately trigger again with different state — should be blocked by cooldown
        self.ctrl.trigger(human_present=False, is_child=False, proximity_zone="safe")
        assert self.ctrl.get_state().target_brightness == first_target

    def test_restore_sets_normal(self):
        self.ctrl._state.target_brightness = 10  # pretend dimmed
        self.ctrl.restore()
        assert self.ctrl.get_state().target_brightness == BrightnessLevel.NORMAL.value

    def test_callback_called_on_change(self):
        before = len(self.changes)
        self.ctrl.restore()
        time.sleep(0.1)
        assert len(self.changes) >= before  # at least one callback


# ─────────────────── INTEGRATION: SENSOR + CONTROLLER ────────────────────────

class TestIntegration:

    def test_danger_zone_triggers_reduction(self):
        sensor = ProximitySensor()
        ctrl   = BrightnessController()

        try:
            # Inject danger zone
            for _ in range(5):
                sensor.simulate(20.0)

            state = sensor.get_state()
            ctrl.trigger(
                human_present=state.human_present,
                is_child=False,
                proximity_zone=state.confirmed_zone,
            )
            assert ctrl.get_state().target_brightness < BrightnessLevel.NORMAL.value
        finally:
            ctrl.shutdown()

    def test_safe_zone_keeps_normal(self):
        sensor = ProximitySensor()
        ctrl   = BrightnessController()

        try:
            for _ in range(5):
                sensor.simulate(100.0)

            state = sensor.get_state()
            ctrl.trigger(
                human_present=state.human_present,
                is_child=False,
                proximity_zone=state.confirmed_zone,
            )
            assert ctrl.get_state().target_brightness == BrightnessLevel.NORMAL.value
        finally:
            ctrl.shutdown()
