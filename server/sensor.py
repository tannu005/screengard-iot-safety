"""
sensor.py — Proximity Sensor Data Module
IoT Proximity-Based Screen Safety System

Receives and manages proximity data sent by ESP32 via HTTP POST.
Implements:
  - Thread-safe ring buffer for recent readings
  - Debounce logic to prevent false positives from single spurious readings
  - Human-presence scoring (persistent signal required, not a single blip)
  - Zone classification: SAFE / WARNING / DANGER
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List


# ─── CONSTANTS ───────────────────────────────────────────────────────────────

ZONE_DANGER  = "danger"    # distance ≤ 10 cm
ZONE_WARNING = "warning"   # distance ≤ 25 cm
ZONE_SAFE    = "safe"      # distance > 25 cm
ZONE_ABSENT  = "absent"    # no object / timeout

DISTANCE_DANGER_CM  = 10.0
DISTANCE_WARNING_CM = 25.0

# Debounce: require N consecutive zone readings before confirming
DEBOUNCE_CONSECUTIVE = 3

# Consider "no human present" if no valid reading for this many seconds
PRESENCE_TIMEOUT_SEC = 5.0

# Ring buffer size
BUFFER_SIZE = 20


# ─── DATA TYPES ──────────────────────────────────────────────────────────────

@dataclass
class ProximityReading:
    """A single proximity reading from the ESP32."""
    distance_cm: float          # -1.0 if no object detected
    zone: str                   # danger / warning / safe / absent
    timestamp: float            # Unix timestamp (time.time())
    timestamp_ms: int           # ESP32 millis() value (for latency calc)
    device_id: str              # ESP32 MAC address


@dataclass
class ProximityState:
    """Current debounced, confirmed state of the proximity system."""
    confirmed_zone: str = ZONE_ABSENT
    human_present: bool = False
    distance_cm: float = -1.0
    last_update: float = field(default_factory=time.time)
    consecutive_count: int = 0


# ─── SENSOR MODULE ───────────────────────────────────────────────────────────

class ProximitySensor:
    """
    Thread-safe proximity data manager.

    Usage:
        sensor = ProximitySensor()
        sensor.ingest(payload_dict)        # called by Flask route
        state = sensor.get_state()         # called by detection loop
        readings = sensor.get_recent(n=5)  # for dashboard
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._buffer: deque[ProximityReading] = deque(maxlen=BUFFER_SIZE)
        self._state = ProximityState()

        # Debounce tracking
        self._pending_zone: Optional[str] = None
        self._pending_count: int = 0

    # ── Ingest raw payload from ESP32 HTTP POST ──────────────────────────────

    def ingest(self, payload: dict) -> ProximityReading:
        """
        Parse and store a new proximity reading.
        Updates internal state with debounce logic.

        Args:
            payload: dict with keys: distance_cm, timestamp_ms, device_id, zone

        Returns:
            ProximityReading object
        """
        now = time.time()

        # Validate and extract fields
        try:
            distance = float(payload.get("distance_cm", -1.0))
            esp_zone = str(payload.get("zone", ZONE_ABSENT))
            ts_ms    = int(payload.get("timestamp_ms", 0))
            dev_id   = str(payload.get("device_id", "unknown"))
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid proximity payload: {e}")

        # Derive authoritative zone from distance (don't fully trust ESP32 zone)
        zone = self._classify_zone(distance)

        reading = ProximityReading(
            distance_cm=distance,
            zone=zone,
            timestamp=now,
            timestamp_ms=ts_ms,
            device_id=dev_id,
        )

        with self._lock:
            self._buffer.append(reading)
            self._update_state(reading)

        return reading

    # ── State query ──────────────────────────────────────────────────────────

    def get_state(self) -> ProximityState:
        """Return a copy of the current confirmed proximity state."""
        with self._lock:
            # Check if presence has timed out
            if (self._state.human_present and
                    time.time() - self._state.last_update > PRESENCE_TIMEOUT_SEC):
                self._state.human_present = False
                self._state.confirmed_zone = ZONE_ABSENT

            # Return shallow copy to avoid race conditions
            return ProximityState(
                confirmed_zone=self._state.confirmed_zone,
                human_present=self._state.human_present,
                distance_cm=self._state.distance_cm,
                last_update=self._state.last_update,
                consecutive_count=self._state.consecutive_count,
            )

    def get_recent(self, n: int = 10) -> List[ProximityReading]:
        """Return the N most recent readings (newest last)."""
        with self._lock:
            return list(self._buffer)[-n:]

    def get_latest(self) -> Optional[ProximityReading]:
        """Return the single most recent reading, or None."""
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def is_human_present(self) -> bool:
        """Convenience method: True if debounced human presence confirmed."""
        return self.get_state().human_present

    def get_zone(self) -> str:
        """Convenience method: current confirmed zone string."""
        return self.get_state().confirmed_zone

    # ── Simulation: inject a fake reading (for testing without hardware) ─────

    def simulate(self, distance_cm: float) -> ProximityReading:
        """
        Inject a simulated reading (no hardware required).
        Useful for dashboard simulation mode and unit tests.
        """
        payload = {
            "distance_cm": distance_cm,
            "timestamp_ms": int(time.time() * 1000) % (2**32),
            "device_id": "simulator",
            "zone": self._classify_zone(distance_cm),
        }
        return self.ingest(payload)

    # ── Private helpers ──────────────────────────────────────────────────────

    def _classify_zone(self, distance_cm: float) -> str:
        """Map a distance reading to a zone string."""
        if distance_cm < 0:
            return ZONE_ABSENT
        elif distance_cm <= DISTANCE_DANGER_CM:
            return ZONE_DANGER
        elif distance_cm <= DISTANCE_WARNING_CM:
            return ZONE_WARNING
        else:
            return ZONE_SAFE

    def _update_state(self, reading: ProximityReading) -> None:
        """
        Internal debounce state machine.
        Must be called with self._lock held.
        """
        zone = reading.zone

        if zone == self._pending_zone:
            self._pending_count += 1
        else:
            # Zone changed — reset debounce counter
            self._pending_zone = zone
            self._pending_count = 1

        # Confirm zone change only after consecutive readings
        if self._pending_count >= DEBOUNCE_CONSECUTIVE:
            self._state.confirmed_zone = zone
            self._state.distance_cm = reading.distance_cm
            self._state.last_update = reading.timestamp
            self._state.consecutive_count = self._pending_count

            # Human is present if zone is danger or warning (not absent/safe with timeout)
            self._state.human_present = zone in (ZONE_DANGER, ZONE_WARNING)


# ─── MODULE-LEVEL SINGLETON ──────────────────────────────────────────────────

# Import this instance in server.py and other modules
proximity_sensor = ProximitySensor()
