"""
Tests for `clear_pii_for_expired_assignments` management command.
"""

import re
from unittest import TestCase, mock
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.utils import timezone

from enterprise_access.apps.content_assignments.constants import (
    RETIRED_EMAIL_ADDRESS_FORMAT,
    LearnerContentAssignmentStateChoices
)
from enterprise_access.apps.content_assignments.management.commands import clear_pii_for_expired_assignments
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory


@pytest.mark.django_db
class TestClearPiiForExpiredAssignmentsCommand(TestCase):
    """
    Tests `clear_pii_for_expired_assignments` management command.
    """

    def setUp(self):
        super().setUp()
        self.command = clear_pii_for_expired_assignments.Command()

        self.enterprise_uuid = uuid4()
        self.assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
        )
        self.assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=self.enterprise_uuid,
            active=True,
            assignment_configuration=self.assignment_configuration,
            spend_limit=10000 * 100,
        )

        self.expired_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            lms_user_id=None,
            content_key='edX+edXPrivacy101',
            content_title='edx: Privacy 101',
            content_quantity=-123,
            state=LearnerContentAssignmentStateChoices.EXPIRED,
            expired_at=timezone.now() - timezone.timedelta(hours=2),
        )
        self.expired_assignment.created = timezone.now() - timezone.timedelta(days=100)
        self.expired_assignment.save()
        self.expired_assignment.add_successful_expiration_action()

    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command_clears_pii_for_90_day_expiration(
        self,
        mock_subsidy_client,
        mock_catalog_client,
    ):
        """
        Verify that management command clears PII for assignments that expired due to 90-day timeout.
        """
        subsidy_expiry = timezone.now() + timezone.timedelta(days=365)
        enrollment_end = timezone.now() + timezone.timedelta(days=365)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [{
                'key': 'edX+edXPrivacy101',
                'normalized_metadata': {
                    'start_date': '2020-01-01 12:00:00Z',
                    'end_date': '2030-01-01 12:00:00Z',
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
            }],
        }

        call_command(self.command)

        self.expired_assignment.refresh_from_db()
        pattern = RETIRED_EMAIL_ADDRESS_FORMAT.format('[a-f0-9]{16}')
        assert re.match(pattern, self.expired_assignment.learner_email) is not None
