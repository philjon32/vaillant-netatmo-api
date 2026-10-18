import httpx
import pytest

from respx import MockRouter

from vaillant_netatmo_api.errors import RequestForbiddenException, RequestUnauthorizedException, is_token_error
from vaillant_netatmo_api.thermostat import thermostat_client
from vaillant_netatmo_api.token import Token

token = Token({
    "access_token": "12345",
    "refresh_token": "abcde",
    "expires_at": "",
})

get_thermostats_data_request = {
    "device_type": "NAVaillant",
    "data_amount": "app",
    "sync_device_id": "all",
    "access_token": "12345",
}

get_thermostats_data_refreshed_request = {
    **get_thermostats_data_request,
    "access_token": "67890",
}

get_thermostats_data_response = {
    "status": "ok",
    "body": {"devices": []},
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
    "expires_in": 10800,
}

operation_forbidden = {"error": {"code": 13, "message": "Operation is forbidden"}}
token_expired = {"error": {"code": 3, "message": "Access token expired"}}


@pytest.mark.asyncio
class TestThermostatAuth:
    async def test_auth_flow__forbidden_operation__raises_without_refreshing_token(self, respx_mock: MockRouter):
        respx_mock.post("https://api.netatmo.com/api/getthermostatsdata",
                        data=get_thermostats_data_request).respond(403, json=operation_forbidden)
        refresh = respx_mock.post("https://api.netatmo.com/oauth2/token")

        async with thermostat_client("client", "secret", token, None) as client:
            with pytest.raises(RequestForbiddenException) as err:
                await client.async_get_thermostats_data()

        assert err.value.error_code == 13
        assert not refresh.called

    async def test_auth_flow__expired_token__refreshes_token_and_retries(self, respx_mock: MockRouter):
        respx_mock.post("https://api.netatmo.com/api/getthermostatsdata",
                        data=get_thermostats_data_request).respond(403, json=token_expired)
        respx_mock.post("https://api.netatmo.com/oauth2/token",
                        data=refresh_token_request).respond(200, json=refresh_token_response)
        respx_mock.post("https://api.netatmo.com/api/getthermostatsdata",
                        data=get_thermostats_data_refreshed_request).respond(200, json=get_thermostats_data_response)

        updated: list[Token] = []
        async with thermostat_client("client", "secret", token, updated.append) as client:
            await client.async_get_thermostats_data()

        assert [t.refresh_token for t in updated] == ["fghij"]

    async def test_auth_flow__refresh_rejected__raises_unauthorized_without_retrying(self, respx_mock: MockRouter):
        data = respx_mock.post("https://api.netatmo.com/api/getthermostatsdata",
                               data=get_thermostats_data_request).respond(401)
        respx_mock.post("https://api.netatmo.com/oauth2/token",
                        data=refresh_token_request).respond(400, json={"error": "invalid_grant"})

        updated: list[Token] = []
        async with thermostat_client("client", "secret", token, updated.append) as client:
            with pytest.raises(RequestUnauthorizedException):
                await client.async_get_thermostats_data()

        assert data.call_count == 1
        assert updated == []


@pytest.mark.asyncio
class TestTokenErrorDetection:
    @pytest.mark.parametrize(
        ("status", "body", "expected"),
        [
            (401, None, True),
            (403, token_expired, True),
            (403, {"error": {"code": 2, "message": "Invalid access token"}}, True),
            (403, None, True),
            (403, operation_forbidden, False),
            (406, {"error": {"code": 7, "message": "Nothing to update"}}, False),
            (500, None, False),
        ],
    )
    async def test_is_token_error(self, status, body, expected):
        response = httpx.Response(status, json=body) if body is not None else httpx.Response(status)

        assert is_token_error(response) is expected
