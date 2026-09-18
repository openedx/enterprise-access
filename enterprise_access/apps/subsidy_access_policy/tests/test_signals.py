"""
Tests for subsidy_access_policy signals and handlers.
"""
import uuid

from django.test import TestCase
from openedx_events.enterprise.data import EnterpriseGroup
from openedx_events.enterprise.signals import ENTERPRISE_GROUP_DELETED

from enterprise_access.apps.subsidy_access_policy.models import PolicyGroupAssociation
from enterprise_access.apps.subsidy_access_policy.signals import handle_enterprise_group_deleted
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PolicyGroupAssociationFactory
)


class TestEnterpriseGroupDeletedSignal(TestCase):
    """
    Tests for the ENTERPRISE_GROUP_DELETED signal handler.
    """

    def setUp(self):
        """
        Set up test data for the test cases.
        """
        super().setUp()
        self.group_uuid_1 = uuid.uuid4()
        self.group_uuid_2 = uuid.uuid4()
        self.group_uuid_3 = uuid.uuid4()

        # Create policies to associate with groups
        self.policy_1 = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()
        self.policy_2 = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()
        self.policy_3 = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()

        # Create policy-group associations
        self.association_1 = PolicyGroupAssociationFactory.create(
            subsidy_access_policy=self.policy_1,
            enterprise_group_uuid=self.group_uuid_1
        )
        self.association_2 = PolicyGroupAssociationFactory.create(
            subsidy_access_policy=self.policy_2,
            enterprise_group_uuid=self.group_uuid_2
        )
        # Different group that shouldn't be affected
        self.association_3 = PolicyGroupAssociationFactory.create(
            subsidy_access_policy=self.policy_3,
            enterprise_group_uuid=self.group_uuid_3
        )

    def test_handle_enterprise_group_deleted_direct_call(self):
        """
        Test that the signal handler correctly deletes associations when called directly.
        """
        # Set up a mock enterprise group
        mock_enterprise_group = EnterpriseGroup(uuid=self.group_uuid_1)

        # Verify that associations for group_uuid_1 exist before deletion
        self.assertTrue(
            PolicyGroupAssociation.objects.filter(enterprise_group_uuid=self.group_uuid_1).exists(),
            "Associations for group_uuid_1 should exist before deletion"
        )

        # Call the signal handler directly
        handle_enterprise_group_deleted(enterprise_group=mock_enterprise_group)

        # Verify that associations for group_uuid_1 are deleted
        self.assertFalse(
            PolicyGroupAssociation.objects.filter(enterprise_group_uuid=self.group_uuid_1).exists(),
            "Associations for deleted group should be removed"
        )

        # Verify that associations for other groups are not affected
        self.assertTrue(
            PolicyGroupAssociation.objects.filter(enterprise_group_uuid=self.group_uuid_2).exists(),
            "Associations for unrelated groups should not be affected"
        )

    def test_handle_enterprise_group_deleted_via_signal(self):
        """
        Test that the signal handler correctly responds to the ENTERPRISE_GROUP_DELETED signal.
        """
        # Set up a mock enterprise group
        mock_enterprise_group = EnterpriseGroup(uuid=self.group_uuid_2)

        # Verify that associations for group_uuid_1 exist before deletion
        self.assertTrue(
            PolicyGroupAssociation.objects.filter(enterprise_group_uuid=self.group_uuid_2).exists(),
            "Associations for group_uuid_1 should exist before deletion"
        )

        # Send the signal
        ENTERPRISE_GROUP_DELETED.send_event(enterprise_group=mock_enterprise_group)

        # Verify that associations for group_uuid_1 are deleted
        self.assertFalse(
            PolicyGroupAssociation.objects.filter(enterprise_group_uuid=self.group_uuid_2).exists(),
            "Associations for deleted group should be removed when signal is sent"
        )

        # Verify that associations for other groups are not affected
        self.assertTrue(
            PolicyGroupAssociation.objects.filter(enterprise_group_uuid=self.group_uuid_3).exists(),
            "Associations for unrelated groups should not be affected when signal is sent"
        )

    def test_handle_enterprise_group_deleted_wrong_kwargs(self):
        """
        Test that the signal handler gracefully handles missing UUID.
        """
        # Initial count of associations
        initial_count = PolicyGroupAssociation.objects.count()

        # Call the signal handler with missing kwargs
        with self.assertRaises(ValueError) as e:
            handle_enterprise_group_deleted()
            # Assert ValueError is raised for missing enterprise_group:
        self.assertEqual(str(e.exception), 'Missing or invalid enterprise_group in signal')

        # Call the signal handler with an invalid enterprise_group
        with self.assertRaises(ValueError) as e:
            handle_enterprise_group_deleted(enterprise_group="invalid_group")
            # Assert ValueError is raised for invalid enterprise_group:
        self.assertEqual(str(e.exception), 'Missing or invalid enterprise_group in signal')

        # Verify no associations were deleted
        self.assertEqual(
            PolicyGroupAssociation.objects.count(),
            initial_count,
            "No associations should be deleted when signal is called with wrong kwargs"
        )
