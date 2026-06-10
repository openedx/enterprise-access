"""
Tests for the Xpert API client.
"""
from unittest import mock

import ddt
import requests
from django.test import TestCase, override_settings

from enterprise_access.apps.prompts.api_client import (
    XpertAPIClient,
    XpertAPIConfigurationError,
    XpertAPIRequestError,
    XpertAPIResponseError
)

MOCK_SETTINGS = {
    'XPERT_AI_CLIENT_ID': 'test-client-id',
    'XPERT_API_BASE_URL': 'https://xpert.example.com',
    'XPERT_REQUEST_TIMEOUT': 30,
}

PATCH_REQUESTS_POST = 'enterprise_access.apps.prompts.api_client.requests.post'


def _mock_post_response(json_data, status_code=200):
    """Return a mock requests.Response with the given JSON body and status."""
    mock_response = mock.Mock(spec=requests.Response)
    mock_response.status_code = status_code
    mock_response.json.return_value = json_data
    mock_response.raise_for_status.return_value = None
    return mock_response


def _mock_post_error_response(status_code=500):
    """Return a mock that raises HTTPError on raise_for_status."""
    mock_response = mock.Mock(spec=requests.Response)
    mock_response.status_code = status_code
    http_error = requests.HTTPError(response=mock_response)
    mock_response.raise_for_status.side_effect = http_error
    return mock_response


@override_settings(**MOCK_SETTINGS)
class XpertAPIClientConfigurationTests(TestCase):
    """Tests for settings validation in XpertAPIClient.send_message()."""

    _SEND_KWARGS = {
        'system_prompt': 'You are a helpful assistant.',
        'messages': [{'role': 'user', 'content': 'Hello'}],
        'conversation_id': 'conv-cfg',
    }

    @override_settings(XPERT_AI_CLIENT_ID='')
    @mock.patch(PATCH_REQUESTS_POST)
    def test_missing_client_id_raises_configuration_error(self, mock_post):
        client = XpertAPIClient()
        with self.assertRaises(XpertAPIConfigurationError):
            client.send_message(**self._SEND_KWARGS)
        mock_post.assert_not_called()

    @override_settings(XPERT_AI_CLIENT_ID=None)
    @mock.patch(PATCH_REQUESTS_POST)
    def test_none_client_id_raises_configuration_error(self, mock_post):
        client = XpertAPIClient()
        with self.assertRaises(XpertAPIConfigurationError):
            client.send_message(**self._SEND_KWARGS)
        mock_post.assert_not_called()

    @override_settings(XPERT_API_BASE_URL='')
    @mock.patch(PATCH_REQUESTS_POST)
    def test_missing_base_url_raises_configuration_error(self, mock_post):
        client = XpertAPIClient()
        with self.assertRaises(XpertAPIConfigurationError):
            client.send_message(**self._SEND_KWARGS)
        mock_post.assert_not_called()

    @override_settings(XPERT_API_BASE_URL=None)
    @mock.patch(PATCH_REQUESTS_POST)
    def test_none_base_url_raises_configuration_error(self, mock_post):
        client = XpertAPIClient()
        with self.assertRaises(XpertAPIConfigurationError):
            client.send_message(**self._SEND_KWARGS)
        mock_post.assert_not_called()


@ddt.ddt
@override_settings(**MOCK_SETTINGS)
class XpertAPIClientInputValidationTests(TestCase):
    """Tests for send_message() input validation — no HTTP call should be made."""

    def setUp(self):
        self.client = XpertAPIClient()
        self.valid_kwargs = {
            'system_prompt': 'You are a helpful assistant.',
            'messages': [{'role': 'user', 'content': 'Hello'}],
            'conversation_id': 'conv-123',
        }

    @ddt.data('', '   ', '\n\n', '\t')
    @mock.patch(PATCH_REQUESTS_POST)
    def test_blank_system_prompt_raises_request_error(self, blank_value, mock_post):
        with self.assertRaises(XpertAPIRequestError):
            self.client.send_message(
                system_prompt=blank_value,
                messages=self.valid_kwargs['messages'],
                conversation_id=self.valid_kwargs['conversation_id'],
            )
        mock_post.assert_not_called()

    @mock.patch(PATCH_REQUESTS_POST)
    def test_none_system_prompt_raises_request_error(self, mock_post):
        with self.assertRaises(XpertAPIRequestError):
            self.client.send_message(
                system_prompt=None,
                messages=self.valid_kwargs['messages'],
                conversation_id=self.valid_kwargs['conversation_id'],
            )
        mock_post.assert_not_called()

    @ddt.data('', '   ', '\n\n', '\t')
    @mock.patch(PATCH_REQUESTS_POST)
    def test_blank_conversation_id_raises_request_error(self, blank_value, mock_post):
        with self.assertRaises(XpertAPIRequestError):
            self.client.send_message(
                system_prompt=self.valid_kwargs['system_prompt'],
                messages=self.valid_kwargs['messages'],
                conversation_id=blank_value,
            )
        mock_post.assert_not_called()

    @mock.patch(PATCH_REQUESTS_POST)
    def test_none_conversation_id_raises_request_error(self, mock_post):
        with self.assertRaises(XpertAPIRequestError):
            self.client.send_message(
                system_prompt=self.valid_kwargs['system_prompt'],
                messages=self.valid_kwargs['messages'],
                conversation_id=None,
            )
        mock_post.assert_not_called()

    @mock.patch(PATCH_REQUESTS_POST)
    def test_none_messages_raises_request_error(self, mock_post):
        with self.assertRaises(XpertAPIRequestError):
            self.client.send_message(
                system_prompt=self.valid_kwargs['system_prompt'],
                messages=None,
                conversation_id=self.valid_kwargs['conversation_id'],
            )
        mock_post.assert_not_called()

    @mock.patch(PATCH_REQUESTS_POST)
    def test_non_list_messages_raises_request_error(self, mock_post):
        with self.assertRaises(XpertAPIRequestError):
            self.client.send_message(
                system_prompt=self.valid_kwargs['system_prompt'],
                messages={'role': 'user', 'content': 'Hello'},
                conversation_id=self.valid_kwargs['conversation_id'],
            )
        mock_post.assert_not_called()


@override_settings(**MOCK_SETTINGS)
class XpertAPIClientRequestTests(TestCase):
    """Tests for correct HTTP request construction."""

    def setUp(self):
        self.messages = [{'role': 'user', 'content': 'Hello'}]
        self.system_prompt = 'You are a helpful assistant.'
        self.conversation_id = 'conv-abc'
        self.valid_envelope = [{'role': 'assistant', 'content': '{"result":"ok"}'}]

    @mock.patch(PATCH_REQUESTS_POST)
    def test_posts_to_correct_url_without_trailing_slash(self, mock_post):
        mock_post.return_value = _mock_post_response(self.valid_envelope)
        client = XpertAPIClient()
        client.send_message(
            system_prompt=self.system_prompt,
            messages=self.messages,
            conversation_id=self.conversation_id,
        )
        url = mock_post.call_args[0][0]
        self.assertEqual(url, 'https://xpert.example.com/v1/message')

    @override_settings(XPERT_API_BASE_URL='https://xpert.example.com/')
    @mock.patch(PATCH_REQUESTS_POST)
    def test_posts_to_correct_url_with_trailing_slash(self, mock_post):
        mock_post.return_value = _mock_post_response(self.valid_envelope)
        client = XpertAPIClient()
        client.send_message(
            system_prompt=self.system_prompt,
            messages=self.messages,
            conversation_id=self.conversation_id,
        )
        url = mock_post.call_args[0][0]
        self.assertEqual(url, 'https://xpert.example.com/v1/message')

    @mock.patch(PATCH_REQUESTS_POST)
    def test_payload_contains_required_fields(self, mock_post):
        mock_post.return_value = _mock_post_response(self.valid_envelope)
        client = XpertAPIClient()
        client.send_message(
            system_prompt=self.system_prompt,
            messages=self.messages,
            conversation_id=self.conversation_id,
        )
        payload = mock_post.call_args[1]['json']
        self.assertEqual(payload['client_id'], 'test-client-id')
        self.assertEqual(payload['system_message'], self.system_prompt)
        self.assertEqual(payload['messages'], self.messages)
        self.assertEqual(payload['conversation_id'], self.conversation_id)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_stream_is_always_false(self, mock_post):
        mock_post.return_value = _mock_post_response(self.valid_envelope)
        client = XpertAPIClient()
        client.send_message(
            system_prompt=self.system_prompt,
            messages=self.messages,
            conversation_id=self.conversation_id,
        )
        payload = mock_post.call_args[1]['json']
        self.assertIs(payload['stream'], False)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_payload_does_not_contain_response_format(self, mock_post):
        mock_post.return_value = _mock_post_response(self.valid_envelope)
        client = XpertAPIClient()
        client.send_message(
            system_prompt=self.system_prompt,
            messages=self.messages,
            conversation_id=self.conversation_id,
        )
        payload = mock_post.call_args[1]['json']
        self.assertNotIn('response_format', payload)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_payload_includes_tags_when_nonempty(self, mock_post):
        mock_post.return_value = _mock_post_response(self.valid_envelope)
        client = XpertAPIClient()
        client.send_message(
            system_prompt=self.system_prompt,
            messages=self.messages,
            conversation_id=self.conversation_id,
            tags=['discovery', 'edx-available-course'],
        )
        payload = mock_post.call_args[1]['json']
        self.assertEqual(payload['tags'], ['discovery', 'edx-available-course'])

    @mock.patch(PATCH_REQUESTS_POST)
    def test_payload_omits_tags_when_none(self, mock_post):
        mock_post.return_value = _mock_post_response(self.valid_envelope)
        client = XpertAPIClient()
        client.send_message(
            system_prompt=self.system_prompt,
            messages=self.messages,
            conversation_id=self.conversation_id,
            tags=None,
        )
        payload = mock_post.call_args[1]['json']
        self.assertNotIn('tags', payload)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_payload_omits_tags_when_empty_list(self, mock_post):
        mock_post.return_value = _mock_post_response(self.valid_envelope)
        client = XpertAPIClient()
        client.send_message(
            system_prompt=self.system_prompt,
            messages=self.messages,
            conversation_id=self.conversation_id,
            tags=[],
        )
        payload = mock_post.call_args[1]['json']
        self.assertNotIn('tags', payload)

    @override_settings(XPERT_REQUEST_TIMEOUT=15)
    @mock.patch(PATCH_REQUESTS_POST)
    def test_uses_configured_timeout(self, mock_post):
        mock_post.return_value = _mock_post_response(self.valid_envelope)
        client = XpertAPIClient()
        client.send_message(
            system_prompt=self.system_prompt,
            messages=self.messages,
            conversation_id=self.conversation_id,
        )
        self.assertEqual(mock_post.call_args[1]['timeout'], 15)


@override_settings(**MOCK_SETTINGS)
class XpertAPIClientResponseTests(TestCase):
    """Tests for response envelope normalization."""

    def setUp(self):
        self.client = XpertAPIClient()
        self.kwargs = {
            'system_prompt': 'You are a helpful assistant.',
            'messages': [{'role': 'user', 'content': 'Hello'}],
            'conversation_id': 'conv-xyz',
        }

    @mock.patch(PATCH_REQUESTS_POST)
    def test_returns_first_envelope_item(self, mock_post):
        first = {'role': 'assistant', 'content': '{"result":"ok"}'}
        second = {'role': 'assistant', 'content': '{"result":"ignored"}'}
        mock_post.return_value = _mock_post_response([first, second])
        result = self.client.send_message(**self.kwargs)
        self.assertEqual(result, first)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_does_not_parse_content_json_string(self, mock_post):
        raw_content = '{"result":"some complex json"}'
        envelope = [{'role': 'assistant', 'content': raw_content}]
        mock_post.return_value = _mock_post_response(envelope)
        result = self.client.send_message(**self.kwargs)
        self.assertEqual(result['content'], raw_content)
        self.assertIsInstance(result['content'], str)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_raises_response_error_for_invalid_json(self, mock_post):
        mock_response = mock.Mock(spec=requests.Response)
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError('not JSON')
        mock_post.return_value = mock_response
        with self.assertRaises(XpertAPIResponseError):
            self.client.send_message(**self.kwargs)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_raises_response_error_for_non_list_response(self, mock_post):
        mock_post.return_value = _mock_post_response({'role': 'assistant'})
        with self.assertRaises(XpertAPIResponseError):
            self.client.send_message(**self.kwargs)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_raises_response_error_for_empty_list(self, mock_post):
        mock_post.return_value = _mock_post_response([])
        with self.assertRaises(XpertAPIResponseError):
            self.client.send_message(**self.kwargs)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_raises_response_error_for_non_dict_first_item(self, mock_post):
        mock_post.return_value = _mock_post_response(['not a dict'])
        with self.assertRaises(XpertAPIResponseError):
            self.client.send_message(**self.kwargs)


@override_settings(**MOCK_SETTINGS)
class XpertAPIClientErrorTests(TestCase):
    """Tests for request-level error handling."""

    def setUp(self):
        self.client = XpertAPIClient()
        self.kwargs = {
            'system_prompt': 'You are a helpful assistant.',
            'messages': [{'role': 'user', 'content': 'Hello'}],
            'conversation_id': 'conv-err',
        }

    @mock.patch(PATCH_REQUESTS_POST)
    def test_raises_request_error_for_request_exception(self, mock_post):
        mock_post.side_effect = requests.ConnectionError('connection refused')
        with self.assertRaises(XpertAPIRequestError):
            self.client.send_message(**self.kwargs)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_raises_request_error_for_non_2xx_response(self, mock_post):
        mock_post.return_value = _mock_post_error_response(status_code=500)
        with self.assertRaises(XpertAPIRequestError):
            self.client.send_message(**self.kwargs)

    @mock.patch(PATCH_REQUESTS_POST)
    def test_raises_request_error_for_timeout(self, mock_post):
        mock_post.side_effect = requests.Timeout('timed out')
        with self.assertRaises(XpertAPIRequestError):
            self.client.send_message(**self.kwargs)
