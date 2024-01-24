""" Utils for subsidy_requests. """

import logging

from django.conf import settings
from edx_rest_framework_extensions.auth.jwt.decoder import configured_jwt_decode_handler

from enterprise_access.apps.core.models import User

logger = logging.getLogger(__name__)


def get_user_from_request_session(request):
    """
    Get user ID from SessionStore on a request object.

    Django Admin only allows for session auth, not jwt auth.

    Returns a user object.
    """

    user_session = request.session.load()
    user_id = user_session['_auth_user_id']
    user = User.objects.get(id=user_id)
    return user


def get_data_from_jwt_payload(request, keys):
    """
    Look at request object's cookies, and try to grab the keys from
    the jwt header payload.

    This is convenient for when we do not have an explicit jwt cookie
    set, but have the payload and signature available (basically when
    you are in the Django admin).

    Inputs:
      request - a django request object
      keys - list of strings

    Returns a dict with keys and their values

    Will throw KeyError if keys requested not present in jwt payload.
    """

    jwt_payload_name = settings.JWT_AUTH['JWT_AUTH_COOKIE_HEADER_PAYLOAD']
    jwt_payload_data = request.COOKIES.get(jwt_payload_name)

    jwt_signature_name = settings.JWT_AUTH['JWT_AUTH_COOKIE_SIGNATURE']
    jwt_signature_data = request.COOKIES.get(jwt_signature_name)

    try:
        jwt_data = jwt_payload_data + '.' + jwt_signature_data
    except TypeError:
        logger.exception(
            'JWT payload or signature not found in cookies. '
            'User should reauthenticate. '
        )
        raise

    jwt_dict = configured_jwt_decode_handler(jwt_data)

    return_dict = {key: value for key, value in jwt_dict.items() if key in keys}
    return return_dict
