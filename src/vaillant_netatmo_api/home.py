from __future__ import annotations

from enum import Enum


class Home:
    """Home model representing a home in Vaillant system."""

    def __init__(
        self,
        id: str | None = None,
        name: str = "",
        rooms: list[dict] = [],
        therm_mode: str | None = None,
        therm_setpoint_default_duration: int | None = None,
        **kwargs,
    ) -> None:
        """Create new home model."""

        self.id = id
        self.name = name
        self.rooms = [Room(**room) for room in rooms]
        self.therm_mode = _parse_enum(ThermMode, therm_mode)
        self.therm_setpoint_default_duration = therm_setpoint_default_duration

    def get_room_for_module(self, module_id: str) -> Room | None:
        """Return the room which the given thermostat module belongs to."""

        for room in self.rooms:
            if module_id in room.module_ids:
                return room

        return None

    def __eq__(self, other: Home):
        if not isinstance(other, Home):
            return False

        return (
            self.id == other.id
            and len(self.rooms) == len(other.rooms)
            and all([False for i, j in zip(self.rooms, other.rooms) if i != j])
        )


class Room:
    """Room model representing a room in a home for Vaillant system."""

    def __init__(
        self,
        id: str | None = None,
        module_ids: list[str] = [],
        therm_relay: str | None = None,
        **kwargs,
    ) -> None:
        """Create new room model."""

        self.id = id
        self.module_ids = list(module_ids)
        self.therm_relay = therm_relay

    def __eq__(self, other: Room):
        if not isinstance(other, Room):
            return False

        return (
            self.id == other.id
        )


class TemperatureControlMode(Enum):
    """TemperatureControlMode enumeration representing possible system modes of the thermostat."""

    HEATING = "heating"
    COOLING = "cooling"


class ThermMode(Enum):
    """ThermMode enumeration representing possible system modes of the thermostat."""

    HG = "hg"
    SCHEDULE = "schedule"
    AWAY = "away"


class ThermSetpointMode(Enum):
    """ThermSetpointMode enumeration representing possible system modes of the thermostat."""

    MANUAL = "manual"
    HOME = "home"


def _parse_enum(enum: type[Enum], value: str | None) -> Enum | None:
    """Return the enum member for the value, or None for missing or unknown values."""

    try:
        return enum(value)
    except ValueError:
        return None
