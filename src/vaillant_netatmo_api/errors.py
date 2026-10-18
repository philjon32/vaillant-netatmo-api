"""Module containing exceptions when communicating with the Netatmo API."""

from __future__ import annotations

from contextlib import contextmanager
from re import sub
from typing import Generator

from httpx import codes, HTTPStatusError, NetworkError, Request, Response, TimeoutException

# Netatmo error codes (the "error.code" field of an error response body) which mean
# the access token itself is the problem. Any other 403 is a refused operation.
TOKEN_ERROR_CODES = frozenset({2, 3})  # invalid access token, access token expired


def netatmo_error_code(response: Response | None) -> int | None:
    """Return the Netatmo error code from an error response body, if there is one."""

    if response is None:
        return None

    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return None

    if isinstance(error, dict) and isinstance(error.get("code"), int):
        return error["code"]

    return None


def is_token_error(response: Response) -> bool:
    """Return whether the response rejects the request because of the access token."""

    if response.status_code == codes.UNAUTHORIZED:
        return True

    if response.status_code == codes.FORBIDDEN:
        code = netatmo_error_code(response)
        return code is None or code in TOKEN_ERROR_CODES

    return False


class UnsuportedArgumentsException(Exception):
    """Exception which is thrown when provided argument is not valid parameter of the method."""

    def __init__(self, message: str, **kwargs) -> None:
        self.params = f"{kwargs=}"

        super().__init__(message, self.params)


class NonOkResponseException(Exception):
    """Exception which is thrown when server returns a valid HTTP response, but the request was not successfully handled and the response contains errors."""

    def __init__(self, message: str, **kwargs) -> None:
        self.params = f"{kwargs=}"

        super().__init__(message, self.params)


class ApiException(Exception):
    """Base class for all exceptions related to networking."""

    def __init__(self, message: str, request: Request, response: Response | None) -> None:
        self.request = _sanitize_request(request)
        self.response = _sanitize_response(response)
        self.error_code = netatmo_error_code(response)

        super().__init__(message, self.request, self.response)


class RetryableException(ApiException):
    """Base class for all retryable exceptions related to networking."""


class NonRetryableException(ApiException):
    """Base class for all non-retryable exceptions related to networking."""


class NetworkTimeoutException(RetryableException):
    """Exception which is thrown when request times out and server doesn't return a response."""


class NetworkException(RetryableException):
    """Exception which is thrown when server is unavailable or unreachable and doesn't return a response."""


class RequestUnauthorizedException(NonRetryableException):
    """Exception which is thrown when server rejects the access token (401, or 403 with a token error code)."""


class RequestBackoffException(NonRetryableException):
    """Exception which is thrown when server returns an 429 TOO MANY REQUESTS response."""


class RequestClientException(NonRetryableException):
    """Exception which is thrown when server returns any 4xx response."""


class RequestForbiddenException(RequestClientException):
    """Exception which is thrown when server returns 403 FORBIDDEN for a valid token, refusing the operation itself."""


class RequestServerException(RetryableException):
    """Exception which is thrown when server returns any 5xx response."""


class RequestException(RetryableException):
    """Exception which is thrown when server returns any other non-2xx response."""


@contextmanager
def client_error_handler() -> Generator[None]:
    try:
        yield
    except TimeoutException as e:
        raise NetworkTimeoutException("Request timed out while accessing the API. Retry the request with longer timeout. Check the network if the issue persists.", e.request, None) from e
    except NetworkError as e:
        raise NetworkException("Unknown network error while accessing the API. Retry the request or check the network if the issue persists.", e.request, None) from e
    except HTTPStatusError as e:
        if is_token_error(e.response):
            raise RequestUnauthorizedException("Access token is invalid or expired. Refresh the access token and try again.", e.request, e.response) from e
        if e.response.status_code == codes.FORBIDDEN:
            raise RequestForbiddenException("Server refused to perform the operation. Retrying with a new access token will not help.", e.request, e.response) from e
        if e.response.status_code == codes.TOO_MANY_REQUESTS:
            raise RequestBackoffException("Client is sending too many requests. Reduce the frequency at which API is accessed and try again.", e.request, e.response)
        if codes.is_client_error(e.response.status_code):
            raise RequestClientException("Server processed the request and returned a 4xx response. Don't retry the request without changing the arguments as the new request will fail again with the same error.", e.request, e.response) from e
        if codes.is_server_error(e.response.status_code):
            raise RequestServerException("Server couldn't process the request successfully and returned a 5xx response. Try again with reasonable backoff strategy.", e.request, e.response) from e
        else:
            raise RequestException("Unknown response error. Check the log for more details.", e.request, e.response) from e

def _sanitize_request(request: Request) -> dict:
    if not request:
        return None
    
    return {
        "method": request.method,
        "url": request.url,
        "body": _sanitize_params(
            request.content,
            [b"client_secret", b"password", b"access_token", b"refresh_token"],
        )
    }

def _sanitize_response(response: Response) -> dict:
    if not response:
        return None

    return {
        "status_code": response.status_code,
        "url": response.url,
        "body": response.content,
        "duration": response.elapsed,
    }

def _sanitize_params(body: bytes, params: list[bytes]) -> str:
    param_value_before = rb".+?(&|$)"
    param_value_after = rb"<FILTERED>&"

    result = body
    for param in params:
        before = param + rb"=" + param_value_before
        after = param + rb"=" + param_value_after
        result = sub(before, after, result)
    return result