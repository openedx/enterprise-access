"""Test subsidy_requests.utils"""

from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
from django.test import TestCase

from enterprise_access.apps.subsidy_request.utils import get_data_from_jwt_payload, get_user_from_request_session

User = get_user_model()


class UtilsTests(TestCase):
    """ Tests for utils. """

    def test_get_user_from_request_session(self):
        """
        Test get_user_from_request_session
        """

        # Create a user
        test_user = User.objects.create(username='joe_brain')

        # Create request with session
        request = HttpRequest()
        middleware = SessionMiddleware(mock.Mock())
        middleware.process_request(request)
        request.session['_auth_user_id'] = test_user.id
        request.session.save()

        assert get_user_from_request_session(request) == test_user

    @mock.patch('enterprise_access.apps.subsidy_request.utils.configured_jwt_decode_handler')
    def test_get_data_from_jwt_payload(self, mock_jwt_decoder):
        """
        Test get_data_from_jwt_payload
        """
        mock_jwt_decoder.return_value = {
            'user_id': '14',
            'adminstrator': True,
            'pie': 'best_dessert'
        }
        request = HttpRequest()
        request.COOKIES[settings.JWT_AUTH['JWT_AUTH_COOKIE_HEADER_PAYLOAD']] = 'someEncodedData'
        request.COOKIES[settings.JWT_AUTH['JWT_AUTH_COOKIE_SIGNATURE']] = 'someEncodeSignature'

        expected = {'user_id': '14'}
        actual = get_data_from_jwt_payload(request, 'user_id')
        assert actual == expected

        expected = {}
        actual = get_data_from_jwt_payload(request, 'cake')
        assert actual == expected

    def test_get_data_from_jwt_payload_no_cookies(self):
        """
        Test get_data_from_jwt_payload when cookie not available
        """
        request = HttpRequest()
        request.COOKIES[settings.JWT_AUTH['JWT_AUTH_COOKIE_HEADER_PAYLOAD']] = None
        request.COOKIES[settings.JWT_AUTH['JWT_AUTH_COOKIE_SIGNATURE']] = None

        with self.assertRaises(TypeError):
            get_data_from_jwt_payload(request, 'user_id')
