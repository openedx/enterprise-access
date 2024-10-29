import crum
import requests

from edx_django_utils.monitoring import set_custom_attribute
from edx_rest_framework_extensions.auth.jwt.cookies import jwt_cookie_name


def get_request_id():
    """
    Helper to get the request id - usually set via an X-Request-ID header
    """
    request = crum.get_current_request()
    if request is not None and request.headers is not None:
        return request.headers.get('X-Request-ID')
    else:
        return None


class BaseUserApiClient(requests.Session):
    """
    A requests Session that includes the Authorization and User-Agent headers from the original request.
    """
    def __init__(self, original_request, **kwargs):
        super().__init__(**kwargs)
        self.original_request = original_request

        self.headers = {}

        if self.original_request:
            # If Authorization header is present in the original request, pass through to subsequent request headers
            if 'Authorization' in self.original_request.headers:
                self.headers['Authorization'] = self.original_request.headers['Authorization']

            # If no Authorization header, check for JWT in cookies
            jwt_token = self.original_request.COOKIES.get(jwt_cookie_name())
            if 'Authorization' not in self.headers and jwt_token is not None:
                self.headers['Authorization'] = f'JWT {jwt_token}'

            # Add X-Request-ID header if applicable
            request_id = get_request_id()
            if self.headers.get('X-Request-ID') is None and request_id is not None:
                self.headers['X-Request-ID'] = request_id

    def request(self, method, url, headers=None, **kwargs):  # pylint: disable=arguments-differ
        if headers:
            headers.update(self.headers)
        else:
            headers = self.headers

        # Set `api_client` as a custom attribute for monitoring, reflecting the API client's module path
        set_custom_attribute('api_client', 'enterprise_access.apps.api_client.base_user.BaseUserApiClient')

        return super().request(method, url, headers=headers, **kwargs)
