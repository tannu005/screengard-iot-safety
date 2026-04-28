"""
control.py — Brightness Controller Module
IoT Proximity-Based Screen Safety System

Manages smooth brightness transitions for the connected display.

Brightness states:
  NORMAL   → 100% (no threat detected)
  REDUCED  →  30% (adult close to screen)
  SAFE_DIM →  10% (child detected close to screen — maximum protection)

Transitions use ease-in-out interpolation over a configurable duration.
Includes a fail-safe that restores full brightness if no human detected.
Works on Windows (WMI DDC-CI for external monitors, ACPI for laptop panel).
"""

import threading
import time
import logging
import math
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Callable

# Suppress noisy EDID parse warnings from screen_brightness_control
logging.getLogger("screen_brightness_control.windows").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)



# ─── CONSTANTS ───────────────────────────────────────────────────────────────

class BrightnessLevel(Enum):
    NORMAL   = 100
    REDUCED  = 30
    SAFE_DIM = 10


# Transition duration in seconds (ease-in-out interpolation)
TRANSITION_DURATION_SEC = 1.2

# Update interval during transition (smaller = smoother)
TRANSITION_STEP_SEC = 0.05  # 20 Hz interpolation

# Cooldown: once a trigger fires, wait this long before re-evaluating
COOLDOWN_SEC = 5.0

# Fail-safe: restore NORMAL if system is ON but no human for this long
FAILSAFE_RESTORE_SEC = 30.0

# Minimum time child must be detected before dimming (prevents photo triggers)
CHILD_CONFIRM_SEC = 2.0


# ─── DATA TYPES ──────────────────────────────────────────────────────────────

@dataclass
class ControlState:
    current_brightness: int = 100
    target_brightness: int = 100
    brightness_level: str = "NORMAL"
    is_transitioning: bool = False
    last_trigger_time: float = 0.0
    trigger_reason: str = "none"
    cooldown_active: bool = False


# ─── PLATFORM BRIGHTNESS DRIVER ──────────────────────────────────────────────

class BrightnessDriver:
    """
    Cross-platform brightness driver.
    Primary: screen_brightness_control (Windows WMI + DDC-CI).
    Fallback: no-op with console log (for displays without DDC-CI support).
    """

    def __init__(self):
        self._available = False
        self._sbc = None
        self._try_init()

    def _try_init(self):
        try:
            import screen_brightness_control as sbc
            self._sbc = sbc
            # Test if we can get current brightness
            current = sbc.get_brightness()
            if current is not None:
                self._available = True
                logger.info(f"Brightness driver ready. Current: {current}%")
            else:
                logger.warning("screen_brightness_control: could not read brightness")
        except ImportError:
            logger.warning(
                "screen_brightness_control not installed. "
                "Install with: pip install screen-brightness-control\n"
                "Brightness changes will be simulated only."
            )
        except Exception as e:
            logger.warning(f"Brightness driver init failed: {e}. Will simulate.")

    @property
    def is_available(self) -> bool:
        return self._available

    def get(self) -> int:
        """Get current display brightness (0–100)."""
        if not self._available:
            return 100
        try:
            val = self._sbc.get_brightness()
            if isinstance(val, list):
                val = val[0]
            return int(val) if val is not None else 100
        except Exception:
            return 100

    def set(self, brightness: int) -> bool:
        """
        Set display brightness (0–100).
        Returns True on success.
        """
        brightness = max(0, min(100, int(brightness)))

        if not self._available:
            logger.debug(f"[SIM] Brightness → {brightness}%")
            return True

        try:
            self._sbc.set_brightness(brightness)
            return True
        except Exception as e:
            logger.error(f"Failed to set brightness to {brightness}%: {e}")
            return False


# ─── BRIGHTNESS CONTROLLER ────────────────────────────────────────────────────

class BrightnessController:
    """
    Manages smooth brightness transitions and trigger logic.

    Thread-safe. Call trigger() from the detection loop.
    Transitions run in a background thread.
    """

    def __init__(self, on_state_change: Optional[Callable[[ControlState], None]] = None):
        """
        Args:
            on_state_change: Optional callback called whenever state changes.
                             Useful for emitting WebSocket events.
        """
        self._driver = BrightnessDriver()
        self._lock = threading.Lock()
        self._state = ControlState()
        self._state.current_brightness = self._driver.get()
        self._state.target_brightness = self._state.current_brightness

        self._on_state_change = on_state_change

        # Transition thread
        self._transition_thread: Optional[threading.Thread] = None
        self._transition_stop = threading.Event()

        # Fail-safe thread
        self._failsafe_thread = threading.Thread(
            target=self._failsafe_loop, daemon=True, name="FailsafeThread"
        )
        self._failsafe_active = True
        self._last_human_time: float = 0.0
        self._failsafe_thread.start()

        # Child confirmation timer
        self._child_first_seen: float = 0.0

        logger.info(f"BrightnessController initialized. Driver available: {self._driver.is_available}")

    # ── Public API ────────────────────────────────────────────────────────────

    def trigger(
        self,
        human_present: bool,
        is_child: bool,
        proximity_zone: str,
    ) -> None:
        """
        Evaluate detection results and trigger brightness changes if needed.

        Called by the detection loop in server.py on every cycle.

        Args:
            human_present: True if a human is present (proximity debounced)
            is_child:       True if vision module confirmed a child
            proximity_zone: 'danger' | 'warning' | 'safe' | 'absent'
        """
        with self._lock:
            now = time.time()

            # Update fail-safe timer
            if human_present:
                self._last_human_time = now

            # Cooldown check
            if now - self._state.last_trigger_time < COOLDOWN_SEC:
                self._state.cooldown_active = True
                return
            self._state.cooldown_active = False

            # Determine desired brightness level
            target_level, reason = self._decide_level(
                human_present, is_child, proximity_zone, now
            )

            target_pct = target_level.value

            if target_pct == self._state.target_brightness:
                return  # No change needed

            # Commit the change
            self._state.target_brightness = target_pct
            self._state.last_trigger_time = now
            self._state.trigger_reason = reason
            self._state.brightness_level = target_level.name

        logger.info(f"Brightness trigger: {reason} → {target_pct}%")
        self._start_transition(target_pct)
        self._notify()

    def restore(self) -> None:
        """Immediately schedule a restore to NORMAL brightness."""
        with self._lock:
            self._state.target_brightness = BrightnessLevel.NORMAL.value
            self._state.brightness_level = "NORMAL"
            self._state.trigger_reason = "manual_restore"
        self._start_transition(BrightnessLevel.NORMAL.value)
        self._notify()

    def get_state(self) -> ControlState:
        with self._lock:
            s = self._state
            return ControlState(
                current_brightness=s.current_brightness,
                target_brightness=s.target_brightness,
                brightness_level=s.brightness_level,
                is_transitioning=s.is_transitioning,
                last_trigger_time=s.last_trigger_time,
                trigger_reason=s.trigger_reason,
                cooldown_active=s.cooldown_active,
            )

    def shutdown(self):
        """Clean up threads and restore brightness on exit."""
        self._failsafe_active = False
        self._transition_stop.set()
        self.restore()

    # ── Decision Logic ────────────────────────────────────────────────────────

    def _decide_level(
        self,
        human_present: bool,
        is_child: bool,
        zone: str,
        now: float,
    ):
        """
        Core decision table:
          - No human → NORMAL (fail-safe)
          - Human in SAFE zone → NORMAL
          - Human in WARNING zone → REDUCED
          - Human in DANGER zone (adult) → REDUCED
          - Child in DANGER zone (confirmed for CHILD_CONFIRM_SEC) → SAFE_DIM
        """
        if not human_present or zone in ("safe", "absent"):
            self._child_first_seen = 0.0
            return BrightnessLevel.NORMAL, "no_human_or_safe"

        if zone == "warning":
            return BrightnessLevel.REDUCED, "human_in_warning_zone"

        # zone == "danger"
        if is_child:
            if self._child_first_seen == 0.0:
                self._child_first_seen = now
            child_confirmed = (now - self._child_first_seen) >= CHILD_CONFIRM_SEC
            if child_confirmed:
                return BrightnessLevel.SAFE_DIM, "child_in_danger_zone"
            else:
                return BrightnessLevel.REDUCED, "child_detected_confirming"
        else:
            self._child_first_seen = 0.0
            return BrightnessLevel.REDUCED, "adult_in_danger_zone"

    # ── Smooth Transition ─────────────────────────────────────────────────────

    def _start_transition(self, target: int):
        """Start a smooth ease-in-out brightness transition in background thread."""
        # Cancel any in-progress transition
        self._transition_stop.set()
        if self._transition_thread and self._transition_thread.is_alive():
            self._transition_thread.join(timeout=2)

        self._transition_stop.clear()
        self._transition_thread = threading.Thread(
            target=self._do_transition,
            args=(target,),
            daemon=True,
            name="BrightnessTransition",
        )
        self._transition_thread.start()

    def _do_transition(self, target: int):
        """Ease-in-out interpolation from current to target brightness."""
        with self._lock:
            start = self._state.current_brightness
            self._state.is_transitioning = True

        if start == target:
            with self._lock:
                self._state.is_transitioning = False
            return

        steps = max(1, int(TRANSITION_DURATION_SEC / TRANSITION_STEP_SEC))

        for step in range(steps + 1):
            if self._transition_stop.is_set():
                break

            t = step / steps  # 0.0 → 1.0

            # Ease-in-out cubic: t = t^2 * (3 - 2t)
            t_eased = t * t * (3 - 2 * t)

            current = int(start + (target - start) * t_eased)
            self._driver.set(current)

            with self._lock:
                self._state.current_brightness = current

            time.sleep(TRANSITION_STEP_SEC)

        # Ensure we land exactly on target
        self._driver.set(target)
        with self._lock:
            self._state.current_brightness = target
            self._state.is_transitioning = False

        self._notify()

    # ── Fail-safe Loop ────────────────────────────────────────────────────────

    def _failsafe_loop(self):
        """
        Background thread that restores brightness if no human detected
        for FAILSAFE_RESTORE_SEC seconds. Prevents screen staying dim
        if the detection system crashes or sensor disconnects.
        """
        while self._failsafe_active:
            time.sleep(5.0)

            with self._lock:
                current_target = self._state.target_brightness
                last_human = self._last_human_time

            if current_target < BrightnessLevel.NORMAL.value:
                elapsed = time.time() - last_human
                if elapsed >= FAILSAFE_RESTORE_SEC:
                    logger.warning(
                        f"Fail-safe triggered: no human detected for {elapsed:.0f}s. "
                        "Restoring brightness."
                    )
                    self.restore()

    # ── Notification ──────────────────────────────────────────────────────────

    def _notify(self):
        if self._on_state_change:
            try:
                self._on_state_change(self.get_state())
            except Exception as e:
                logger.debug(f"State change callback error: {e}")


# ─── MODULE-LEVEL SINGLETON ──────────────────────────────────────────────────

# Initialized in server.py with callback
brightness_controller: Optional[BrightnessController] = None
