import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from respx import MockRouter

from vaillant_netatmo_api.home import TemperatureControlMode, ThermMode, ThermSetpointMode
from vaillant_netatmo_api.state import HeatingPriority, ScheduleType, SystemMode, build_home_states, parse_enum
from vaillant_netatmo_api.thermostat import MeasurementScale, MeasurementType, thermostat_client
from vaillant_netatmo_api.token import Token

TESTDATA = Path(__file__).parent / "testdata"
BRUSSELS = ZoneInfo("Europe/Brussels")
HOME_ID = "aaaaaaaaaaaaaaaaaaaaaaa1"
BOILER_ID = "70:ee:50:00:00:01"
THERMOSTAT_ID = "07:00:00:00:00:01"
ROOM_ID = "1000000001"

token = Token({"access_token": "12345", "refresh_token": "abcde", "expires_at": ""})


def load(name: str) -> dict:
    return json.loads((TESTDATA / f"{name}.json").read_text())


@pytest.fixture
def home():
    [home] = build_home_states(
        load("homesdata"),
        {HOME_ID: load("homestatus")},
        {HOME_ID: load("getconfigs")},
        load("getthermostatsdata"),
    )
    return home


class TestBuildHomeStates:
    def test_home(self, home):
        assert home.id == HOME_ID
        assert home.timezone == "Europe/Brussels"
        assert home.therm_mode == ThermMode.SCHEDULE
        assert home.temperature_control_mode == TemperatureControlMode.HEATING
        assert home.settings.anticipation is True
        assert home.settings.heating_priority == HeatingPriority.ECO
        assert home.settings.setpoint_default_duration == 180
        assert home.settings.boost_default_duration == 30
        assert home.linked_schedules == {"bbbbbbbbbbbbbbbbbbbbbbb1": "bbbbbbbbbbbbbbbbbbbbbbb2"}

    def test_room(self, home):
        [room] = home.rooms
        assert room.id == ROOM_ID
        assert room.module_ids == [THERMOSTAT_ID]
        assert room.setpoint_mode == ThermSetpointMode.HOME
        assert room.measured_temperature is not None
        assert room.setpoint_start is not None and room.setpoint_start.tzinfo is timezone.utc
        assert room.temperature_offset == 0
        assert home.room(ROOM_ID) is room

    def test_thermostat(self, home):
        [thermostat] = home.thermostats
        assert thermostat.id == THERMOSTAT_ID
        assert thermostat.room_id == ROOM_ID
        assert thermostat.boiler_id == BOILER_ID
        assert thermostat.firmware == 14
        assert 0 <= thermostat.battery_percent <= 100
        assert thermostat.battery_state in ("full", "high", "medium", "low", "very_low")
        assert thermostat.reachable is True
        assert thermostat.heating_demand is False
        assert thermostat.estimated_setpoint_temperature is not None

    def test_boiler_and_hot_water(self, home):
        [boiler] = home.boilers
        assert boiler.id == BOILER_ID
        assert boiler.firmware == 1030
        assert boiler.name == "Home"
        assert boiler.heating_type == "radiator"
        assert boiler.heating_curve.slope == 2.6
        assert boiler.heating_curve.default is True
        assert (boiler.heating_curve.min_flow_temperature, boiler.heating_curve.max_flow_temperature) == (21, 90)
        assert boiler.system_mode == SystemMode.WINTER
        assert boiler.energy_metering is False
        assert (boiler.boiler_error, boiler.ebus_error, boiler.maintenance_due, boiler.refill_water) == (False,) * 4
        assert boiler.boiler_errors == []

        hot_water = boiler.hot_water
        assert (hot_water.setpoint_temperature, hot_water.min_temperature, hot_water.max_temperature) == (45, 35, 65)
        assert hot_water.always_on is False
        assert hot_water.boost_active is False
        assert hot_water.boost_end is None
        assert hot_water.control == "instantaneous"

    def test_schedules(self, home):
        heating = home.selected_schedule(ScheduleType.THERM)
        hot_water = home.selected_schedule(ScheduleType.EVENT)

        assert heating.name == "My schedule"
        assert (heating.away_temp, heating.hg_temp) == (16, 7)
        assert {zone.name for zone in heating.zones} == {"Comfort", "Night", "Eco"}
        assert heating.zone(0).room_temperatures == {ROOM_ID: 20}
        assert hot_water.id == home.linked_schedules[heating.id]
        assert hot_water.zone(0).dhw_enabled is True
        assert hot_water.zone(1).dhw_enabled is False

    def test_duplicates_in_status_are_ignored(self):
        status = load("homestatus")
        assert len(status["home"]["modules"]) > len({m["id"] for m in status["home"]["modules"]})

        [home] = build_home_states(load("homesdata"), {HOME_ID: status}, {}, {})

        assert len(home.thermostats) == 1
        assert len(home.boilers) == 1

    def test_missing_sources_degrade_to_none(self):
        [home] = build_home_states(load("homesdata"), {}, {}, {})

        [boiler] = home.boilers
        assert boiler.hot_water.setpoint_temperature is None
        assert home.rooms[0].measured_temperature is None

    def test_unknown_values_do_not_raise(self):
        homes = load("homesdata")
        homes["homes"][0]["therm_mode"] = "something_new"
        homes["homes"][0]["therm_heating_priority"] = "turbo"

        [home] = build_home_states(homes, {}, {}, {})

        assert home.therm_mode is None
        assert home.settings.heating_priority is None
        assert parse_enum(ThermMode, None) is None


class TestSchedule:
    # Heating: Sunday 07:00 Comfort, 22:00 Night; Monday 00:00 starts with Night.
    def test_zone_at(self, home):
        heating = home.selected_schedule(ScheduleType.THERM)

        assert heating.zone_at(datetime(2026, 10, 18, 21, 59, tzinfo=BRUSSELS)).name == "Comfort"
        assert heating.zone_at(datetime(2026, 10, 18, 22, 0, tzinfo=BRUSSELS)).name == "Night"
        assert heating.zone_at(datetime(2026, 10, 19, 0, 0, tzinfo=BRUSSELS)).name == "Night"

    def test_next_change_skips_entries_that_keep_the_zone(self, home):
        heating = home.selected_schedule(ScheduleType.THERM)

        # Sunday 23:00 is Night; Monday 00:00 continues Night; Monday 07:00 is Comfort.
        when, zone = heating.next_change(datetime(2026, 10, 18, 23, 0, tzinfo=BRUSSELS))

        assert when == datetime(2026, 10, 19, 7, 0, tzinfo=BRUSSELS)
        assert zone.name == "Comfort"

    def test_next_change_same_day(self, home):
        heating = home.selected_schedule(ScheduleType.THERM)

        when, zone = heating.next_change(datetime(2026, 10, 18, 12, 0, tzinfo=BRUSSELS))

        assert when == datetime(2026, 10, 18, 22, 0, tzinfo=BRUSSELS)
        assert zone.name == "Night"

    def test_hot_water_schedule(self, home):
        hot_water = home.selected_schedule(ScheduleType.EVENT)

        assert hot_water.zone_at(datetime(2026, 10, 19, 6, 30, tzinfo=BRUSSELS)).dhw_enabled is True
        assert hot_water.zone_at(datetime(2026, 10, 19, 3, 0, tzinfo=BRUSSELS)).dhw_enabled is False


@pytest.mark.asyncio
class TestClient:
    async def test_async_get_home_states(self, respx_mock: MockRouter):
        respx_mock.post("https://app.netatmo.net/api/homesdata").respond(200, json={"status": "ok", "body": load("homesdata")})
        respx_mock.post("https://app.netatmo.net/syncapi/v1/homestatus").respond(200, json={"status": "ok", "body": load("homestatus")})
        respx_mock.post("https://app.netatmo.net/syncapi/v1/getconfigs").respond(200, json={"status": "ok", "body": load("getconfigs")})
        respx_mock.post("https://api.netatmo.com/api/getthermostatsdata", data__contains={"data_amount": "full"}).respond(
            200, json={"status": "ok", "body": load("getthermostatsdata")}
        )

        async with thermostat_client("", "", token, None) as client:
            [home] = await client.async_get_home_states()

        assert home.boilers[0].hot_water.setpoint_temperature == 45

    async def test_async_get_measures__several_types__splits_values_per_type(self, respx_mock: MockRouter):
        route = respx_mock.post("https://api.netatmo.com/api/getmeasure").respond(200, json={
            "status": "ok",
            "body": [{"beg_time": 1_000_000, "step_time": 600, "value": [[0, 20.2], [120, 20.1]]}],
        })

        async with thermostat_client("", "", token, None) as client:
            series = await client.async_get_measures(
                BOILER_ID, THERMOSTAT_ID, [MeasurementType.SUM_BOILER_ON, MeasurementType.TEMPERATURE],
                MeasurementScale.FIVE_MINS, datetime.fromtimestamp(1_000_000, timezone.utc),
            )

        assert b"type=sum_boiler_on%2Ctemperature" in route.calls.last.request.content
        start = datetime.fromtimestamp(1_000_000, timezone.utc)
        assert series[MeasurementType.SUM_BOILER_ON] == [(start, 0), (start + timedelta(minutes=10), 120)]
        assert series[MeasurementType.TEMPERATURE][1] == (start + timedelta(minutes=10), 20.1)

    async def test_async_set_configs(self, respx_mock: MockRouter):
        route = respx_mock.post("https://app.netatmo.net/syncapi/v1/setconfigs").respond(200, json={"status": "ok"})

        async with thermostat_client("", "", token, None) as client:
            await client.async_set_configs(HOME_ID, BOILER_ID, dhw_always_on=True)

        assert json.loads(route.calls.last.request.content) == {
            "home": {"id": HOME_ID, "modules": [{"id": BOILER_ID, "dhw_always_on": True}]},
            "app_identifier": "app_thermostat_vaillant",
        }

    async def test_async_switch_home_schedule(self, respx_mock: MockRouter):
        route = respx_mock.post("https://app.netatmo.net/api/switchhomeschedule").respond(200, json={"status": "ok"})

        async with thermostat_client("", "", token, None) as client:
            await client.async_switch_home_schedule(HOME_ID, "s1", ScheduleType.EVENT)

        assert json.loads(route.calls.last.request.content)["schedule_type"] == "event"

    async def test_async_set_home_settings__only_given_settings_are_sent(self, respx_mock: MockRouter):
        route = respx_mock.post("https://app.netatmo.net/api/sethomedata").respond(200, json={"status": "ok"})

        async with thermostat_client("", "", token, None) as client:
            await client.async_set_home_settings(HOME_ID, heating_priority=HeatingPriority.COMFORT, anticipation=False)

        assert json.loads(route.calls.last.request.content)["home"] == {
            "id": HOME_ID, "anticipation": False, "therm_heating_priority": "comfort",
        }

    async def test_non_ok_status__raises(self, respx_mock: MockRouter):
        from vaillant_netatmo_api.errors import NonOkResponseException

        respx_mock.post("https://app.netatmo.net/api/sethomedata").respond(200, json={"status": "failed"})

        async with thermostat_client("", "", token, None) as client:
            with pytest.raises(NonOkResponseException):
                await client.async_set_home_settings(HOME_ID, anticipation=True)

