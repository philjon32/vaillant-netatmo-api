"""Typed snapshot of a home, merged from the read endpoints of the Netatmo API.

One `HomeState` combines:

* `homesdata`: structure (rooms, modules), settings and schedules,
* `homestatus`: live state of rooms, thermostats and boilers,
* `getconfigs`: hot water configuration of the boilers,
* `getthermostatsdata` (old API): the few fields no other endpoint reports.

The builders are pure functions over the response bodies, so they can be tested with
recorded responses. Unknown enum values parse to None instead of raising, and all
timestamps become timezone-aware UTC datetimes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, TypeVar

from .home import SystemMode, TemperatureControlMode, ThermMode, ThermSetpointMode

_E = TypeVar("_E", bound=Enum)

MINUTES_PER_WEEK = 7 * 24 * 60


class HeatingPriority(Enum):
    """How the heating balances comfort against energy use."""

    ECO = "eco"
    COMFORT = "comfort"


class ScheduleType(Enum):
    """Kind of schedule: heating (therm) or hot water (event)."""

    THERM = "therm"
    EVENT = "event"


@dataclass(frozen=True, kw_only=True)
class Zone:
    """A zone of a schedule: room temperatures (heating) or hot water on/off (hot water)."""

    id: int
    name: str
    type: int | None
    room_temperatures: dict[str, float] = field(default_factory=dict)
    dhw_enabled: bool | None = None


@dataclass(frozen=True, kw_only=True)
class TimetableEntry:
    """Start of a zone, in minutes since Monday 00:00 in the home's time zone."""

    zone_id: int
    m_offset: int


@dataclass(frozen=True, kw_only=True)
class Schedule:
    """A weekly heating or hot water schedule."""

    id: str
    type: ScheduleType | None
    name: str
    selected: bool
    zones: list[Zone]
    timetable: list[TimetableEntry]
    away_temp: float | None = None
    hg_temp: float | None = None

    def zone(self, zone_id: int) -> Zone | None:
        """Return the zone with the given id."""

        return next((zone for zone in self.zones if zone.id == zone_id), None)

    def zone_at(self, at: datetime) -> Zone | None:
        """Return the zone active at the given time, which must be in the home's time zone."""

        if not self.timetable:
            return None

        minute = _minute_of_week(at)
        current = self.timetable[-1]  # Before the first entry, last week's last zone applies.
        for entry in self.timetable:
            if entry.m_offset > minute:
                break
            current = entry

        return self.zone(current.zone_id)

    def next_change(self, at: datetime) -> tuple[datetime, Zone | None] | None:
        """Return when the zone changes next and the zone it changes to.

        Entries that continue the current zone (such as Monday 00:00 after Sunday
        night) are not changes. Returns None if the schedule has a single zone.
        """

        current = self.zone_at(at)
        if current is None:
            return None

        minute = _minute_of_week(at)
        week_start = datetime.combine(at.date() - timedelta(days=at.weekday()), time(), at.tzinfo)
        upcoming = [(entry.m_offset, entry) for entry in self.timetable if entry.m_offset > minute] + [
            (MINUTES_PER_WEEK + entry.m_offset, entry) for entry in self.timetable
        ]

        for offset, entry in upcoming:
            if entry.zone_id != current.id:
                return week_start + timedelta(minutes=offset), self.zone(entry.zone_id)

        return None


@dataclass(frozen=True, kw_only=True)
class HomeSettings:
    """Settings of the home, as shown under heating parameters in the app."""

    anticipation: bool | None
    heating_priority: HeatingPriority | None
    setpoint_default_duration: int | None
    boost_default_duration: int | None
    absence_autoaway: bool | None


@dataclass(frozen=True, kw_only=True)
class Room:
    """A room and its setpoint."""

    id: str
    module_ids: list[str]
    measured_temperature: float | None
    setpoint_mode: ThermSetpointMode | None
    setpoint_temperature: float | None
    setpoint_start: datetime | None
    setpoint_end: datetime | None
    anticipating: bool | None
    reachable: bool | None
    temperature_offset: float | None


@dataclass(frozen=True, kw_only=True)
class Thermostat:
    """A room thermostat (NAThermVaillant)."""

    id: str
    type: str
    name: str | None
    room_id: str | None
    boiler_id: str | None
    firmware: int | None
    battery_percent: int | None
    battery_state: str | None
    heating_demand: bool | None
    reachable: bool | None
    rf_strength: int | None
    last_seen: datetime | None
    estimated_setpoint_temperature: float | None


@dataclass(frozen=True, kw_only=True)
class HotWater:
    """Domestic hot water of a boiler."""

    setpoint_temperature: float | None
    min_temperature: float | None
    max_temperature: float | None
    always_on: bool | None
    enabled: bool | None
    boost_active: bool
    boost_end: datetime | None
    control: str | None


@dataclass(frozen=True, kw_only=True)
class HeatingCurve:
    """Weather compensation of the boiler (house_model.hc_gefs).

    The app shows the slope divided by ten ("heating curve 2.6"). While `default`
    is true the curve is the learnt default rather than a value set by hand.
    """

    slope: float | None
    shift: float | None
    base_outdoor_temperature: float | None
    max_outdoor_temperature: float | None
    min_flow_temperature: float | None
    max_flow_temperature: float | None
    default: bool | None


@dataclass(frozen=True, kw_only=True)
class Boiler:
    """A boiler with its eBUS gateway (NAVaillant)."""

    id: str
    type: str
    subtype: str | None
    name: str | None
    firmware: int | None
    reachable: bool | None
    outdoor_temperature: float | None
    wifi_strength: int | None
    rf_strength: int | None
    boiler_errors: list[Any]
    boiler_error: bool
    ebus_error: bool
    maintenance_due: bool
    refill_water: bool
    energy_metering: bool
    heating_type: str | None
    heating_curve: HeatingCurve | None
    system_mode: SystemMode | None
    hot_water: HotWater


@dataclass(frozen=True, kw_only=True)
class HomeState:
    """Everything known about one home."""

    id: str
    name: str
    timezone: str | None
    therm_mode: ThermMode | None
    therm_mode_end: datetime | None
    temperature_control_mode: TemperatureControlMode | None
    settings: HomeSettings
    rooms: list[Room]
    thermostats: list[Thermostat]
    boilers: list[Boiler]
    schedules: list[Schedule]
    linked_schedules: dict[str, str]
    # Read endpoints whose last good response was used because the request failed.
    stale_sources: frozenset[str] = frozenset()

    def room(self, room_id: str | None) -> Room | None:
        """Return the room with the given id."""

        return next((room for room in self.rooms if room.id == room_id), None)

    def schedule(self, schedule_id: str | None) -> Schedule | None:
        """Return the schedule with the given id."""

        return next((s for s in self.schedules if s.id == schedule_id), None)

    def schedules_of_type(self, schedule_type: ScheduleType) -> list[Schedule]:
        """Return the heating or hot water schedules."""

        return [s for s in self.schedules if s.type == schedule_type]

    def selected_schedule(self, schedule_type: ScheduleType) -> Schedule | None:
        """Return the active heating or hot water schedule."""

        return next((s for s in self.schedules_of_type(schedule_type) if s.selected), None)


def build_home_states(
    homes_data: dict,
    home_statuses: dict[str, dict],
    configs: dict[str, dict],
    thermostats_data: dict,
) -> list[HomeState]:
    """Merge response bodies into home snapshots.

    `homes_data` and `thermostats_data` are the `body` of homesdata and
    getthermostatsdata; `home_statuses` and `configs` map home ids to the `body`
    of homestatus and getconfigs.
    """

    devices = {device.get("_id"): device for device in thermostats_data.get("devices", [])}
    old_modules = {
        module.get("_id"): module
        for device in devices.values()
        for module in device.get("modules", [])
    }

    return [
        _build_home(
            home,
            _unique_by_id(home_statuses.get(home["id"], {}).get("home", {})),
            _unique_by_id(configs.get(home["id"], {}).get("home", {})),
            devices,
            old_modules,
        )
        for home in homes_data.get("homes", [])
    ]


def _build_home(
    home: dict,
    status: dict[str, dict[str, dict]],
    config: dict[str, dict[str, dict]],
    devices: dict[str, dict],
    old_modules: dict[str, dict],
) -> HomeState:
    status_rooms = status.get("rooms", {})
    status_modules = status.get("modules", {})
    config_modules = config.get("modules", {})

    rooms = [
        _build_room(room, status_rooms.get(room["id"], {}))
        for room in home.get("rooms", [])
    ]

    thermostats: list[Thermostat] = []
    boilers: list[Boiler] = []
    for module in home.get("modules", []):
        module_status = status_modules.get(module["id"], {})
        if module.get("type") == "NAVaillant":
            boilers.append(
                _build_boiler(
                    module,
                    module_status,
                    config_modules.get(module["id"], {}),
                    devices.get(module["id"], {}),
                )
            )
        else:
            thermostats.append(
                _build_thermostat(module, module_status, old_modules.get(module["id"], {}), rooms)
            )

    linked: dict[str, str] = {}
    for link in home.get("linked_schedules", []):
        if "therm" in link and "event" in link:
            linked[link["therm"]] = link["event"]

    return HomeState(
        id=home["id"],
        name=home.get("name", ""),
        timezone=home.get("timezone"),
        therm_mode=parse_enum(ThermMode, home.get("therm_mode")),
        therm_mode_end=parse_timestamp(home.get("therm_mode_endtime")),
        temperature_control_mode=parse_enum(TemperatureControlMode, home.get("temperature_control_mode")),
        settings=HomeSettings(
            anticipation=home.get("anticipation"),
            heating_priority=parse_enum(HeatingPriority, home.get("therm_heating_priority")),
            setpoint_default_duration=home.get("therm_setpoint_default_duration"),
            boost_default_duration=home.get("therm_boost_default_duration"),
            absence_autoaway=home.get("therm_absence_autoaway"),
        ),
        rooms=rooms,
        thermostats=thermostats,
        boilers=boilers,
        schedules=[_build_schedule(schedule) for schedule in home.get("schedules", [])],
        linked_schedules=linked,
    )


def _build_room(room: dict, status: dict) -> Room:
    offset = next(
        (value for key, value in room.items() if key.startswith("measure_offset_")),
        None,
    )
    return Room(
        id=room["id"],
        module_ids=list(room.get("module_ids", [])),
        measured_temperature=status.get("therm_measured_temperature"),
        setpoint_mode=parse_enum(ThermSetpointMode, status.get("therm_setpoint_mode")),
        setpoint_temperature=status.get("therm_setpoint_temperature"),
        setpoint_start=parse_timestamp(status.get("therm_setpoint_start_time")),
        setpoint_end=parse_timestamp(status.get("therm_setpoint_end_time")),
        anticipating=status.get("anticipating"),
        reachable=status.get("reachable"),
        temperature_offset=offset,
    )


def _build_thermostat(module: dict, status: dict, old: dict, rooms: list[Room]) -> Thermostat:
    room_id = module.get("room_id") or next(
        (room.id for room in rooms if module["id"] in room.module_ids), None
    )
    return Thermostat(
        id=module["id"],
        type=module.get("type", ""),
        name=module.get("name") or old.get("module_name") or None,
        room_id=room_id,
        boiler_id=module.get("bridge"),
        firmware=status.get("firmware_revision", old.get("firmware")),
        battery_percent=status.get("battery_percent", old.get("battery_percent")),
        battery_state=status.get("battery_state"),
        heating_demand=status.get("boiler_status", old.get("boiler_status")),
        reachable=status.get("reachable", old.get("reachable")),
        rf_strength=status.get("rf_strength"),
        last_seen=parse_timestamp(status.get("last_seen")),
        estimated_setpoint_temperature=old.get("measured", {}).get("est_setpoint_temp"),
    )


def _build_boiler(module: dict, status: dict, config: dict, device: dict) -> Boiler:
    ebus = device.get("ebus_status", {})
    hwb = device.get("setpoint_hwb", {})
    boiler_errors = status.get("boiler_error", device.get("boiler_error")) or []
    boost_end = parse_timestamp(hwb.get("setpoint_endtime"))

    return Boiler(
        id=module["id"],
        type=module.get("type", ""),
        subtype=module.get("subtype"),
        name=module.get("name") or device.get("station_name") or None,
        firmware=status.get("firmware_revision", device.get("firmware")),
        reachable=module.get("reachable"),
        outdoor_temperature=status.get(
            "outdoor_temperature", device.get("outdoor_temperature", {}).get("te")
        ),
        wifi_strength=status.get("wifi_strength", device.get("wifi_status")),
        rf_strength=status.get("rf_strength", device.get("rf_amb_status")),
        boiler_errors=list(boiler_errors),
        boiler_error=bool(boiler_errors) or bool(ebus.get("boiler_error")),
        ebus_error=bool(status.get("ebus_error", ebus.get("ebus_error"))),
        maintenance_due=bool(ebus.get("maintenance_status")),
        refill_water=bool(ebus.get("refill_water")),
        energy_metering=bool(status.get("emf_avail", ebus.get("emf_avail"))),
        heating_type=device.get("heating_system", {}).get("heating_type"),
        heating_curve=_build_heating_curve(device.get("house_model", {})),
        system_mode=parse_enum(SystemMode, device.get("system_mode")),
        hot_water=HotWater(
            setpoint_temperature=config.get("dhw_setpoint_temperature", device.get("dhw")),
            min_temperature=config.get("dhw_temperature_min", device.get("dhw_min")),
            max_temperature=config.get("dhw_temperature_max", device.get("dhw_max")),
            always_on=config.get("dhw_always_on"),
            enabled=status.get("dhw_enabled", device.get("dhw_enabled")),
            boost_active=bool(hwb.get("setpoint_activate")),
            boost_end=boost_end if hwb.get("setpoint_activate") else None,
            control=module.get("dhw_control", device.get("dhw_control")),
        ),
    )


def _build_heating_curve(house_model: dict) -> HeatingCurve | None:
    curve = house_model.get("hc_gefs")
    if not curve:
        return None

    slope = curve.get("slope")
    return HeatingCurve(
        slope=slope / 10 if isinstance(slope, (int, float)) else None,
        shift=curve.get("temp_shift"),
        base_outdoor_temperature=curve.get("base_external_temp"),
        max_outdoor_temperature=curve.get("max_ext_temp"),
        min_flow_temperature=curve.get("min_flow_set_point"),
        max_flow_temperature=curve.get("max_flow_set_point"),
        default=curve.get("default"),
    )


def _build_schedule(schedule: dict) -> Schedule:
    zones = []
    for zone in schedule.get("zones", []):
        room_temperatures = {
            room["id"]: room.get("therm_setpoint_temperature") for room in zone.get("rooms", [])
        } or {
            room["room_id"]: room.get("temp") for room in zone.get("rooms_temp", [])
        }
        dhw = [module.get("dhw_enabled") for module in zone.get("modules", [])]
        zones.append(
            Zone(
                id=zone.get("id"),
                name=zone.get("name") or _DEFAULT_ZONE_NAMES.get(zone.get("type"), ""),
                type=zone.get("type"),
                room_temperatures=room_temperatures,
                dhw_enabled=dhw[0] if dhw else None,
            )
        )

    timetable = sorted(
        (
            TimetableEntry(zone_id=entry.get("zone_id", entry.get("id")), m_offset=entry["m_offset"])
            for entry in schedule.get("timetable", [])
        ),
        key=lambda entry: entry.m_offset,
    )

    return Schedule(
        id=schedule["id"],
        type=parse_enum(ScheduleType, schedule.get("type")),
        name=schedule.get("name") or "",
        selected=bool(schedule.get("selected")),
        zones=zones,
        timetable=timetable,
        away_temp=schedule.get("away_temp"),
        hg_temp=schedule.get("hg_temp"),
    )


# Names the app shows for zones without a name, by zone type.
_DEFAULT_ZONE_NAMES = {0: "Comfort", 1: "Night", 2: "Away", 3: "Frost guard", 5: "Eco"}


def _unique_by_id(body: dict) -> dict[str, dict[str, dict]]:
    """Index the modules and rooms of a status or config body by id.

    The API returns some of them twice; the first occurrence wins.
    """

    indexed: dict[str, dict[str, dict]] = {}
    for key in ("modules", "rooms"):
        items: dict[str, dict] = {}
        for item in body.get(key, []):
            items.setdefault(item.get("id"), item)
        indexed[key] = items
    return indexed


def parse_enum(enum: type[_E], value: Any) -> _E | None:
    """Return the enum member for the value, or None for missing or unknown values."""

    try:
        return enum(value)
    except ValueError:
        return None


def parse_timestamp(value: Any) -> datetime | None:
    """Return a UTC datetime for a Unix timestamp, or None."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc)
    return None


def _minute_of_week(at: datetime) -> int:
    return at.weekday() * 24 * 60 + at.hour * 60 + at.minute
