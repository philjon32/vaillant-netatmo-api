import json

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from respx import MockRouter

from vaillant_netatmo_api.errors import RequestClientException, UnsuportedArgumentsException
from vaillant_netatmo_api.thermostat import thermostat_client
from vaillant_netatmo_api.token import Token
from vaillant_netatmo_api.home import Home, TemperatureControlMode, ThermMode, ThermSetpointMode

token = Token({
    "access_token": "12345",
    "refresh_token": "abcde",
    "expires_at": "",
})


get_homes_data_request = {
    "app_type": "app_thermostat_vaillant",
    "app_identifier": "app_thermostat_vaillant",
    "sync_measurements": True,
}

get_homes_data_refreshed_request = {
    "device_type": "NAVaillant",
    "data_amount": "app",
    "sync_device_id": "all",
}

get_homes_data_response = {
    "status": "ok",
    "body": {
        "homes": [
            {
                "id": "id",
                "name": "name",
                "roomes": [
                    {
                        "id": "id",
                    }
                ]
            }
        ]
    }
}

refresh_token_request = {
    "grant_type": "refresh_token",
    "client_id": "client",
    "client_secret": "secret",
    "refresh_token": "abcde",
}

refresh_token_response = {
    "access_token": "67890",
    "refresh_token": "fghij",
    "expires_at": "",
}


@pytest.mark.asyncio
class TestThermostatHome:
    async def test_async_get_homes_data__invalid_request_params__raises_error(self, respx_mock: MockRouter):
        respx_mock.post("https://app.netatmo.net/api/homesdata",
                        json=get_homes_data_request, headers={("authorization", "Bearer 12345")}).respond(400)

        async with thermostat_client("", "", token, None) as client:
            with pytest.raises(RequestClientException):
                await client.async_get_homes_data()

    @pytest.mark.skip(reason="second call gets marked as 'not mocked' even though all is ok when testing manually (hint: because of authorization header)")
    async def test_async_get_homes_data__server_errors__retry_until_success(self, respx_mock: MockRouter):
        respx_mock.post("https://app.netatmo.net/api/homesdata",
                        json=get_homes_data_request, headers={("authorization", "Bearer 12345")}).respond(401)
        respx_mock.post("https://app.netatmo.net/oauth2/token",
                        data=refresh_token_request).respond(200, json=refresh_token_response)
        respx_mock.post("https://app.netatmo.net/api/homesdata",
                        json=get_homes_data_refreshed_request, headers={("authorization", "Bearer 67890")}).respond(200, json=get_homes_data_response)

        async with thermostat_client(refresh_token_request["client_id"], refresh_token_request["client_secret"], token, None) as client:
            homes = await client.async_get_homes_data()

            expected_homes = get_homes_data_response["body"]["homes"]

            assert respx_mock.calls.call_count == 3
            assert len(homes) == len(expected_homes)
            for x in zip(homes, expected_homes):
                assert x[0] == Home(**x[1])

    async def test_async_get_homes_data__unauthorized_errors__succeed_after_refreshing_token(self, respx_mock: MockRouter):
        respx_mock.post("https://app.netatmo.net/api/homesdata", json=get_homes_data_request, headers={("authorization", "Bearer 12345")}).mock(side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=get_homes_data_response),
        ])

        async with thermostat_client("", "", token, None) as client:
            homes = await client.async_get_homes_data()

            expected_homes = get_homes_data_response["body"]["homes"]

            assert respx_mock.calls.call_count == 2
            assert len(homes) == len(expected_homes)
            for x in zip(homes, expected_homes):
                assert x[0] == Home(**x[1])

    async def test_async_get_homes_data__valid_request_params__returns_valid_device_list(self, respx_mock: MockRouter):
        respx_mock.post("https://app.netatmo.net/api/homesdata",
                        json=get_homes_data_request, headers={("authorization", "Bearer 12345")}).respond(200, json=get_homes_data_response)

        async with thermostat_client("", "", token, None) as client:
            homes = await client.async_get_homes_data()

            expected_homes = get_homes_data_response["body"]["homes"]

            assert len(homes) == len(expected_homes)
            for x in zip(homes, expected_homes):
                assert x[0] == Home(**x[1])

    async def test_async_set_home_data__therm_mode__is_sent_inside_home_object(self, respx_mock: MockRouter):
        route = respx_mock.post("https://app.netatmo.net/api/sethomedata",
                                headers={("authorization", "Bearer 12345")}).respond(200, json={"status": "ok"})

        async with thermostat_client("", "", token, None) as client:
            await client.async_set_home_data("home", TemperatureControlMode.HEATING, ThermMode.AWAY)

        assert json.loads(route.calls.last.request.content) == {
            "home": {"id": "home", "temperature_control_mode": "heating", "therm_mode": "away"},
            "app_identifier": "app_thermostat_vaillant",
        }


class TestHomeModel:
    def test_home__homesdata_payload__links_rooms_to_modules(self):
        home = Home(**{
            "id": "home",
            "rooms": [
                {"id": "room-1", "module_ids": ["07:00:00:00:00:01"], "therm_relay": "70:ee:50:00:00:01"},
                {"id": "room-2", "module_ids": ["07:00:00:00:00:02"]},
            ],
            "therm_mode": "away",
            "therm_setpoint_default_duration": 180,
        })

        assert home.get_room_for_module("07:00:00:00:00:02").id == "room-2"
        assert home.get_room_for_module("unknown") is None
        assert home.rooms[0].therm_relay == "70:ee:50:00:00:01"
        assert home.therm_mode == ThermMode.AWAY
        assert home.therm_setpoint_default_duration == 180
        assert home.timezone is None
        assert Home(id="home", timezone="Europe/Brussels").timezone == "Europe/Brussels"

    def test_home__unknown_therm_mode__is_none(self):
        assert Home(id="home", therm_mode="something_new").therm_mode is None
        assert Home(id="home").therm_mode is None

    @pytest.mark.asyncio
    async def test_async_set_state_for_room__aware_end_time__is_sent_as_timestamp(self, respx_mock: MockRouter):
        route = respx_mock.post("https://app.netatmo.net/syncapi/v1/setstate").respond(200, json={"status": "ok"})
        end_time = datetime.now(timezone.utc) + timedelta(hours=1)

        async with thermostat_client("", "", token, None) as client:
            await client.async_set_state_for_room(
                "home", "room", ThermSetpointMode.MANUAL, therm_setpoint_temperature=21, therm_setpoint_end_time=end_time
            )

        room = json.loads(route.calls.last.request.content)["home"]["rooms"][0]
        assert room["therm_setpoint_end_time"] == round(end_time.timestamp())

    @pytest.mark.asyncio
    async def test_async_set_state_for_room__aware_end_time_in_past__raises(self, respx_mock: MockRouter):
        async with thermostat_client("", "", token, None) as client:
            with pytest.raises(UnsuportedArgumentsException):
                await client.async_set_state_for_room(
                    "home", "room", ThermSetpointMode.MANUAL, therm_setpoint_temperature=21,
                    therm_setpoint_end_time=datetime.now(timezone.utc) - timedelta(minutes=1),
                )
