"""
Low-level HTTP transport client for the Xpert AI service.
"""
import logging
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Lightweight type aliases for Xpert message structures.
Message = dict[str, Any]
XpertResponse = dict[str, Any]

class XpertAPIError(Exception):
    """
    Base exception for all Xpert API client failures.

    Catch this class to handle any Xpert client error without distinguishing
    between configuration, transport, and response envelope failures.
    """


class XpertAPIConfigurationError(XpertAPIError):
    """
    Raised when required Django settings for the Xpert client are missing
    or falsy (``XPERT_AI_CLIENT_ID``, ``XPERT_API_BASE_URL``).

    Callers should treat this as a deployment misconfiguration, not a
    recoverable runtime error. The service must be redeployed with the
    correct settings before ``send_message()`` can succeed.
    """


class XpertAPIRequestError(XpertAPIError):
    """
    Raised when ``send_message()`` is called with invalid arguments, when the
    HTTP transport fails (connection error, timeout), or when Xpert returns a
    non-2xx response.

    Distinguishable from ``XpertAPIConfigurationError`` (settings are missing)
    and ``XpertAPIResponseError`` (transport succeeded but the envelope is
    malformed or unparseable).
    """


class XpertAPIResponseError(XpertAPIError):
    """
    Raised when the Xpert response body cannot be parsed as JSON, or does not
    conform to the expected envelope shape: a non-empty list whose first
    element is a dict.

    This typically indicates an unexpected change to the Xpert response
    contract rather than a transient network failure.
    """


class XpertAPIClient:
    """
    Low-level HTTP transport client for the Xpert AI ``/v1/message`` endpoint.
    - Validates required Django settings before issuing any HTTP request.
    - Validates required call arguments before issuing an HTTP request.
    - Constructs the Xpert request payload.
    - POSTs to ``{XPERT_API_BASE_URL}/v1/message`` with a configurable timeout.
    - Wraps transport failures and non-2xx responses in typed exceptions.
    - Normalizes the Xpert response envelope and returns the first response object.

    Required settings
    -----------------
    ``XPERT_AI_CLIENT_ID``
        Xpert client identifier. Sent as ``client_id`` in every request payload.
    ``XPERT_API_BASE_URL``
        Base URL of the Xpert service, without a trailing ``/v1/message`` path.
    ``XPERT_REQUEST_TIMEOUT``
        Seconds to wait before timing out. Defaults to 30.

    Response envelope
    -----------------
    Xpert returns a list of message objects::

        [{"role": "assistant", "content": "{\"result\": \"...\"}"}]

    ``send_message()`` validates the envelope and returns ``response[0]``.
    The ``content`` field is returned as-is (a raw JSON string); parsing
    is the responsibility of the caller.
    """

    def _validate_configuration(self) -> tuple[str, str]:
        """
        Validate required Django settings and return the client credentials.

        Returns:
            tuple: ``(client_id, message_endpoint)`` where ``message_endpoint``
                is the fully resolved URL for the Xpert ``/v1/message`` path.

        Raises:
            XpertAPIConfigurationError: If ``XPERT_AI_CLIENT_ID`` or
                ``XPERT_API_BASE_URL`` are missing or falsy in Django settings.
        """
        client_id = getattr(settings, 'XPERT_AI_CLIENT_ID', None)
        if not client_id:
            raise XpertAPIConfigurationError(
                'Missing XPERT_AI_CLIENT_ID in settings required for XpertAPIClient.'
            )
        base_url = getattr(settings, 'XPERT_API_BASE_URL', None)
        if not base_url:
            raise XpertAPIConfigurationError(
                'Missing XPERT_API_BASE_URL in settings required for XpertAPIClient.'
            )
        message_endpoint = base_url.rstrip('/') + '/v1/message'
        return client_id, message_endpoint

    def _validate_request(
        self,
        system_prompt: str,
        messages: list[Message],
        conversation_id: str,
    ) -> None:
        """
        Validate required ``send_message()`` arguments before any HTTP request.

        Arguments:
            system_prompt (str): Must be a non-blank string.
            messages (list): Must be a non-None list.
            conversation_id (str): Must be a non-blank string.

        Raises:
            XpertAPIRequestError: If any required argument is missing, blank,
                or of the wrong type.
        """
        if not system_prompt or not str(system_prompt).strip():
            raise XpertAPIRequestError('system_prompt is required and must not be blank.')
        if not conversation_id or not str(conversation_id).strip():
            raise XpertAPIRequestError('conversation_id is required and must not be blank.')
        if messages is None:
            raise XpertAPIRequestError('messages is required.')
        if not isinstance(messages, list):
            raise XpertAPIRequestError(
                f'messages must be a list, got {type(messages).__name__}.'
            )

    def _build_payload(
        self,
        client_id: str,
        system_prompt: str,
        messages: list[Message],
        conversation_id: str,
        tags: list[str] | None,
    ) -> dict[str, Any]:
        """
        Assemble the Xpert request payload.

        ``system_prompt`` is mapped to the Xpert ``system_message`` field.
        ``stream`` is always ``False``. ``tags`` are included only when non-empty.

        Arguments:
            client_id (str): Xpert client identifier from settings.
            system_prompt (str): System instruction; sent as ``system_message``.
            messages (list): Xpert-compatible conversation message list.
            conversation_id (str): Xpert conversation identifier.
            tags (list | None): Optional RAG control tags.

        Returns:
            dict: Xpert request payload ready for JSON serialization.
        """
        payload: dict[str, Any] = {
            'client_id': client_id,
            'system_message': system_prompt,
            'messages': messages,
            'conversation_id': conversation_id,
            'stream': False,
        }
        if tags:
            payload['tags'] = tags
        return payload

    def _normalize_response(self, response: requests.Response) -> XpertResponse:
        """
        Parse and validate the Xpert response envelope.

        Xpert returns a non-empty list of message objects. This method validates
        that shape and returns the first element.

        Arguments:
            response: The raw ``requests.Response`` from the Xpert POST.

        Returns:
            dict: The first response object from the Xpert response envelope.

        Raises:
            XpertAPIResponseError: If the response body is not valid JSON, is
                not a list, is empty, or has a non-dict first element.
        """
        try:
            data = response.json()
        except ValueError as exc:
            raise XpertAPIResponseError(
                'Xpert response body could not be parsed as JSON.'
            ) from exc

        if not isinstance(data, list):
            raise XpertAPIResponseError(
                f'Xpert response envelope is not a list: got {type(data).__name__}.'
            )
        if not data:
            raise XpertAPIResponseError('Xpert response envelope is an empty list.')
        if not isinstance(data[0], dict):
            raise XpertAPIResponseError(
                f'First item in Xpert response envelope is not a dict: got {type(data[0]).__name__}.'
            )
        return data[0]

    def send_message(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        conversation_id: str,
        tags: list[str] | None = None,
    ) -> XpertResponse:
        """
        Send a prompt-backed message to the Xpert ``/v1/message`` endpoint.

        ``system_prompt`` is sent to Xpert as ``system_message``.
        ``stream`` is always ``False`` and is not caller-configurable.
        ``tags`` are included in the payload only when non-empty.

        Arguments:
            system_prompt (str): System instruction used to guide Xpert's
                response. Sent to Xpert as the ``system_message`` payload field.
            messages (list): Xpert-compatible conversation message list. Each
                element should be a dict with at minimum ``role`` and ``content``
                keys.
            conversation_id (str): Xpert conversation identifier. Required by
                Xpert for session continuity.
            tags (list | None): Optional RAG control tags. Included in the
                payload only when the list is non-empty; omitted otherwise.

        Returns:
            dict: The first response object from the Xpert response envelope,
            e.g.::

                {"role": "assistant", "content": "{\"result\": \"...\"}"}

            The ``content`` value is returned as a raw string and is not
            parsed by this client.

        Raises:
            XpertAPIConfigurationError: If required Django settings are missing.
            XpertAPIRequestError: If ``system_prompt``, ``conversation_id``,
                or ``messages`` fail validation, or if the HTTP transport fails
                or returns a non-2xx status.
            XpertAPIResponseError: If the Xpert response body cannot be parsed
                as JSON or does not conform to the expected envelope shape.
        """
        client_id, message_endpoint = self._validate_configuration()
        request_timeout = getattr(settings, 'XPERT_REQUEST_TIMEOUT', 30)
        self._validate_request(system_prompt, messages, conversation_id)
        payload = self._build_payload(client_id, system_prompt, messages, conversation_id, tags)

        try:
            response = requests.post(
                message_endpoint,
                json=payload,
                timeout=request_timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            logger.exception(f'Xpert API returned a non-2xx response for client_id: {client_id}.')
            raise XpertAPIRequestError(
                f'Xpert API returned a non-2xx response: {exc} for client_id: {client_id}'
            ) from exc
        except requests.RequestException as exc:
            logger.exception(f'Xpert API request failed for client_id: {client_id}.')
            raise XpertAPIRequestError(
                f'Xpert API request failed: {exc}'
            ) from exc

        return self._normalize_response(response)
