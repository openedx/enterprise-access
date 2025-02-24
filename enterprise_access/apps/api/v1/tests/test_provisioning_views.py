"""
Tests for the provisioning views.
"""
import uuid

import ddt
from edx_rbac.constants import ALL_ACCESS_CONTEXT
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import (
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE,
    SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE
)
from test_utils import APITest

PROVISIONING_CREATE_ENDPOINT = reverse('api:v1:provisioning-create')

TEST_ENTERPRISE_UUID = uuid.uuid4()


@ddt.ddt
class TestProvisioningAuth(APITest):
    """
    Tests Authentication and Permission checking for provisioning.
    """
    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, AND in the correct context/customer STILL gets you a 403.
        # Provisioning APIs are inaccessible to all learners.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': ALL_ACCESS_CONTEXT},
            status.HTTP_403_FORBIDDEN,
        ),
        # An admin role is not authorized to provision.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': ALL_ACCESS_CONTEXT},
            status.HTTP_403_FORBIDDEN,
        ),
        # Even operators can't provision
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': ALL_ACCESS_CONTEXT},
            status.HTTP_403_FORBIDDEN,
        ),
        # No JWT based auth, no soup for you.
        (
            None,
            status.HTTP_401_UNAUTHORIZED,
        ),
    )
    @ddt.unpack
    def test_provisioning_create_view_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for the provisioning create view..
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        response = self.client.post(PROVISIONING_CREATE_ENDPOINT)
        assert response.status_code == expected_response_code

    def test_provisioning_create_allowed_for_provisioning_admins(self):
        """
        Tests that we get expected 200 response for the provisioning create view when
        the requesting user has the correct system role and provides a valid request payload.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE,
            'context': ALL_ACCESS_CONTEXT,
        }])

        request_payload = {
            "enterprise_customer": {
                "name": "Test customer",
                "country": "US",
                "slug": "test-customer",
            },
            "pending_admins": [
                {
                    "user_email": "test-admin@example.com",
                },
            ],
        }
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=request_payload)
        assert response.status_code == status.HTTP_201_CREATED
