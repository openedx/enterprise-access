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

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.enterprise_customer_uuid_1 = uuid4()
        cls.enterprise_customer_uuid_2 = uuid4()

    def setUp(self):
        super().setUp()
        if not hasattr(self, '_operator_cookies'):
            self.set_jwt_cookie([
                {
                    'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
                    'context': ALL_ACCESS_CONTEXT
                }
            ])
            self._operator_cookies = self.client.cookies
        else:
            self.client.cookies = self._operator_cookies
