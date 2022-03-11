""" Mixins for the api app. """


from functools import cached_property

from edx_rbac import utils

from enterprise_access.apps.core.models import User


class UserDetailsFromJwtMixin:
    """
    Mixin for retrieving user information from the jwt.
    """

    @cached_property
    def decoded_jwt(self):
        return utils.get_decoded_jwt(self.request)

    @property
    def lms_user_id(self):
        return self.decoded_jwt.get('user_id')

    @property
    def user(self):
        # user should always exists
        return User.objects.get(lms_user_id=self.lms_user_id)
