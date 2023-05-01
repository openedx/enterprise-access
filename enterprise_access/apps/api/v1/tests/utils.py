"""
Utilities for unit tests of views and viewsets.
"""
from uuid import uuid4

from enterprise_access.apps.core.constants import ALL_ACCESS_CONTEXT, SYSTEM_ENTERPRISE_OPERATOR_ROLE
from test_utils import APITestWithMocks


class BaseEnterpriseAccessTestCase(APITestWithMocks):
    """
    Tests for SubsidyRequestViewSet.
    """

    def setUp(self):
        super().setUp()
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
                'context': ALL_ACCESS_CONTEXT
            }
        ])

        self.enterprise_customer_uuid_1 = uuid4()
        self.enterprise_customer_uuid_2 = uuid4()
