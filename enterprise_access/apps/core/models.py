""" Core models. """

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import ugettext_lazy as _
from edx_rbac.models import UserRole, UserRoleAssignment
from edx_rbac.utils import ALL_ACCESS_CONTEXT


class User(AbstractUser):
    """
    Custom user model for use with python-social-auth via edx-auth-backends.

    .. pii: Stores full name, username, and email address for a user.
    .. pii_types: name, username, email_address
    .. pii_retirement: local_api

    """
    full_name = models.CharField(_('Full Name'), max_length=255, blank=True, null=True)
    lms_user_id = models.IntegerField(
        blank=True,
        null=True,
    )

    @property
    def access_token(self):
        """
        Returns an OAuth2 access token for this user, if one exists; otherwise None.
        Assumes user has authenticated at least once with the OAuth2 provider (LMS).
        """
        try:
            return self.social_auth.first().extra_data['access_token']  # pylint: disable=no-member
        except Exception:  # pylint: disable=broad-except
            return None

    class Meta:
        get_latest_by = 'date_joined'
        indexes = [
            models.Index(fields=['username']),
            models.Index(fields=['email']),
            models.Index(fields=['lms_user_id'])
        ]

    def get_full_name(self):
        return self.full_name or super().get_full_name()

    def __str__(self):
        return f'{self.email}'


class EnterpriseAccessFeatureRole(UserRole):
    """
    User role definitions specific to Enterprise Access.
     .. no_pii:
    """

    def __str__(self):
        """
        Return human-readable string representation.
        """
        return f"EnterpriseAccessFeatureRole(name={self.name})"

    def __repr__(self):
        """
        Return uniquely identifying string representation.
        """
        return self.__str__()


class EnterpriseAccessRoleAssignment(UserRoleAssignment):
    """
    Model to map users to a EnterpriseAccessFeatureRole.
     .. no_pii:
    """

    role_class = EnterpriseAccessFeatureRole
    enterprise_customer_uuid = models.UUIDField(blank=True, null=True, verbose_name='Enterprise Customer UUID')

    def get_context(self):
        """
        Return the enterprise customer id or `*` if the user has access to all resources.
        """
        if self.enterprise_customer_uuid:
            return str(self.enterprise_customer_uuid)
        return ALL_ACCESS_CONTEXT

    @classmethod
    def user_assignments_for_role_name(cls, user, role_name):
        """
        Returns assignments for a given user and role name.
        """
        return cls.objects.filter(user__id=user.id, role__name=role_name)

    def __str__(self):
        """
        Return human-readable string representation.
        """
        return "EnterpriseAccessRoleAssignment(name={name}, user={user})".format(
            name=self.role.name,  # pylint: disable=no-member
            user=self.user.id,
        )

    def __repr__(self):
        """
        Return uniquely identifying string representation.
        """
        return self.__str__()
