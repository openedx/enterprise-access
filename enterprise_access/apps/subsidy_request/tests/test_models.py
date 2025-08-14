""" Tests for core models. """

from datetime import datetime
from uuid import uuid4

import ddt
from django.forms import ValidationError
from django.test import TestCase
from pytest import mark

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.apps.subsidy_request.constants import (
    LearnerCreditAdditionalActionStates,
    LearnerCreditRequestActionErrorReasons,
    LearnerCreditRequestUserMessages,
    SubsidyRequestStates,
    SubsidyTypeChoices
)
from enterprise_access.apps.subsidy_request.models import (
    LearnerCreditRequest,
    LearnerCreditRequestActions,
    LearnerCreditRequestConfiguration
)
from enterprise_access.apps.subsidy_request.tasks import update_course_info_for_subsidy_request_task
from enterprise_access.apps.subsidy_request.tests.factories import (
    CouponCodeRequestFactory,
    LearnerCreditRequestActionsFactory,
    LearnerCreditRequestConfigurationFactory,
    LearnerCreditRequestFactory,
    LicenseRequestFactory,
    SubsidyRequestCustomerConfigurationFactory
)
from test_utils import TestCaseWithMockedDiscoveryApiClient

now = datetime.utcnow()


@ddt.ddt
@mark.django_db
class LicenseRequestTests(TestCaseWithMockedDiscoveryApiClient):
    """ LicenseRequest model tests. """

    mock_subscription_plan_uuid = uuid4()
    mock_license_uuid = uuid4()

    @ddt.data(
        (None, now),
        (1, None)
    )
    @ddt.unpack
    def test_missing_review_info(self, reviewer_id, reviewed_at):
        with self.assertRaises(ValidationError) as error:
            reviewer = UserFactory(id=reviewer_id) if reviewer_id else None
            license_request = LicenseRequestFactory(
                state=SubsidyRequestStates.PENDING,
                reviewer=reviewer,
                reviewed_at=reviewed_at,
            )
            license_request.save()

        expected_error = 'Both reviewer and reviewed_at are required for a review.'
        assert error.exception.messages[0] == expected_error

    @ddt.data(
        (mock_subscription_plan_uuid, None),
        (None, mock_license_uuid),
    )
    @ddt.unpack
    def test_missing_license_info(self, subscription_plan_uuid, license_uuid):
        with self.assertRaises(ValidationError) as error:
            license_request = LicenseRequestFactory(
                state=SubsidyRequestStates.APPROVED,
                subscription_plan_uuid=subscription_plan_uuid,
                license_uuid=license_uuid,
            )
            license_request.save()

        expected_error = 'Both subscription_plan_uuid and license_uuid are required for a fulfilled license request.'
        assert error.exception.messages[0] == expected_error

    def test_update_course_info_from_discovery(self):
        """
        course data should be fetched from discovery if not set on subsidy object
        during a save().
        """
        original_call_count = self.mock_discovery_client.call_count

        subsidy = LicenseRequestFactory(course_title=None, course_partners=None)
        update_course_info_for_subsidy_request_task("LicenseRequest", str(subsidy.uuid))

        subsidy.refresh_from_db()
        assert subsidy.course_title is not None
        assert subsidy.course_partners is not None
        assert self.mock_discovery_client.call_count > original_call_count


@ddt.ddt
class CouponCodeRequestTests(TestCaseWithMockedDiscoveryApiClient):
    """ CouponCodeRequest model tests. """

    mock_coupon_id = 123456
    mock_coupon_code = uuid4()

    @ddt.data(
        (mock_coupon_id, None),
        (None, mock_coupon_code),
    )
    @ddt.unpack
    def test_missing_coupon_info(self, coupon_id, coupon_code):
        with self.assertRaises(ValidationError) as error:
            coupon_code_request = CouponCodeRequestFactory(
                state=SubsidyRequestStates.APPROVED,
                coupon_id=coupon_id,
                coupon_code=coupon_code,
            )
            coupon_code_request.save()

        expected_error = 'Both coupon_id and coupon_code are required for a fulfilled coupon request.'
        assert error.exception.messages[0] == expected_error

    def test_update_course_info_from_discovery(self):
        """
        course data should be fetched from discovery if not set on subsidy object
        during a save().
        """
        original_call_count = self.mock_discovery_client.call_count

        subsidy = CouponCodeRequestFactory(course_title=None, course_partners=None)
        update_course_info_for_subsidy_request_task("CouponCodeRequest", str(subsidy.uuid))

        subsidy.refresh_from_db()
        assert subsidy.course_title is not None
        assert subsidy.course_partners is not None
        assert self.mock_discovery_client.call_count > original_call_count


@ddt.ddt
class LearnerCreditRequestTests(TestCase):
    """
    Test cases for the LearnerCreditRequest model.
    """

    def setUp(self):
        """
        Set up test data for each test case.
        """
        self.user = UserFactory()
        self.subsidy_access_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()
        self.enterprise_customer_uuid = uuid4()
        self.learner_credit_request = LearnerCreditRequest.objects.create(
            user=self.user,
            course_id="edX+DemoX",
            course_title="Demo Course",
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            state=SubsidyRequestStates.REQUESTED,
        )

    def test_initial_state(self):
        """
          Ensure that a newly created LearnerCreditRequest starts in the REQUESTED state.
          """
        self.assertEqual(self.learner_credit_request.state, SubsidyRequestStates.REQUESTED)

    def test_approve_success(self):
        """
        Verify that approving a request updates the state, assigns a reviewer, and sets the reviewed timestamp.
        """
        reviewer = UserFactory()
        self.learner_credit_request.approve(reviewer)
        self.assertEqual(self.learner_credit_request.state, SubsidyRequestStates.APPROVED)
        self.assertEqual(self.learner_credit_request.reviewer, reviewer)
        self.assertIsNotNone(self.learner_credit_request.reviewed_at)

    def test_decline_success(self):
        """
        Verify that declining a request updates the state, assigns a reviewer, records a reason,
        and sets the reviewed timestamp.
        """
        reviewer = UserFactory()
        decline_reason = "Insufficient eligibility"
        self.learner_credit_request.decline(reviewer, reason=decline_reason)
        self.assertEqual(self.learner_credit_request.state, SubsidyRequestStates.DECLINED)
        self.assertEqual(self.learner_credit_request.reviewer, reviewer)
        self.assertEqual(self.learner_credit_request.decline_reason, decline_reason)
        self.assertIsNotNone(self.learner_credit_request.reviewed_at)

    def test_cancel_success(self):
        """
        Verify that canceling a request updates the state and sets the reviewed timestamp.
        """
        reviewer = UserFactory()
        self.learner_credit_request.cancel(reviewer)
        self.assertEqual(self.learner_credit_request.state, SubsidyRequestStates.CANCELLED)
        self.assertEqual(self.learner_credit_request.reviewer, reviewer)
        self.assertIsNotNone(self.learner_credit_request.reviewed_at)

    def test_clean_invalid_without_review_data(self):
        """
        Ensure validation fails if a reviewed request lacks a reviewer and timestamp.
        """
        self.learner_credit_request.state = SubsidyRequestStates.APPROVED
        self.learner_credit_request.reviewed_at = None
        self.learner_credit_request.reviewer = None
        with self.assertRaises(ValidationError):
            self.learner_credit_request.clean()

    def test_clean_valid_with_review_data(self):
        """
        Ensure validation succeeds when a reviewed request has both reviewer and timestamp.
        """
        reviewer = UserFactory()
        self.learner_credit_request.state = SubsidyRequestStates.APPROVED
        self.learner_credit_request.reviewed_at = datetime.now()
        self.learner_credit_request.reviewer = reviewer
        try:
            self.learner_credit_request.clean()
        except ValidationError:
            self.fail("ValidationError raised unexpectedly!")

    @ddt.data(
        SubsidyRequestStates.REQUESTED,
        SubsidyRequestStates.APPROVED,
        SubsidyRequestStates.ERROR,
        SubsidyRequestStates.ACCEPTED,
    )
    def test_unique_constraint(self, state):
        """
        Ensure that a LearnerCreditRequest cannot be created with the same user, course_id,
        and enterprise_customer_uuid in REQUESTED, APPROVED, ERROR or ACCEPTED state.
        """
        with self.assertRaises(Exception):
            LearnerCreditRequest.objects.create(
                user=self.user,
                course_id="edX+DemoX",
                enterprise_customer_uuid=self.enterprise_customer_uuid,
                state=state,
                subsidy_access_policy=self.subsidy_access_policy
            )

    def test_update_course_info_from_discovery(self):
        """
        course data should be fetched from discovery if not set on subsidy object
        during a save().
        """

        subsidy = LearnerCreditRequestFactory(course_title=None, course_partners=None)
        update_course_info_for_subsidy_request_task("LearnerCreditRequest", str(subsidy.uuid))
        subsidy.refresh_from_db()
        assert subsidy.course_title is not None
        assert subsidy.course_partners is not None


class LearnerCreditRequestConfigurationTests(TestCase):
    """
    Test cases for the LearnerCreditRequestConfiguration model.
    """

    def setUp(self):
        """
        Set up test data for each test case.
        """
        self.subsidy_access_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()
        self.config = LearnerCreditRequestConfiguration.objects.create(active=True)
        self.subsidy_access_policy.learner_credit_request_config = self.config
        self.subsidy_access_policy.save()

    def test_configuration_creation(self):
        """
        Ensure a LearnerCreditRequestConfiguration instance is created successfully.
        """
        self.assertIsNotNone(self.config.uuid)

    def test_policy_association(self):
        """
        Verify that the configuration is correctly associated with a SubsidyAccessPolicy.
        """
        self.assertEqual(self.subsidy_access_policy.learner_credit_request_config, self.config)
        self.assertTrue(self.subsidy_access_policy.bnr_enabled)

    def test_bnr_enabled_property(self):
        """
        Verify that the bnr_enabled property returns the correct value.
        """
        # Test when learner_credit_request_config is set
        self.assertTrue(self.subsidy_access_policy.bnr_enabled)

        # Test when learner_credit_request_config is not set
        self.subsidy_access_policy.learner_credit_request_config = None
        self.subsidy_access_policy.save()
        self.assertFalse(self.subsidy_access_policy.bnr_enabled)


@ddt.ddt
class LearnerCreditRequestActionsTests(TestCase):
    """
    Test cases for the LearnerCreditRequestActions model.
    """

    def setUp(self):
        """
        Set up test data for each test case.
        """
        self.user = UserFactory()
        self.learner_credit_request = LearnerCreditRequestFactory(user=self.user)
        self.action = LearnerCreditRequestActionsFactory(
            learner_credit_request=self.learner_credit_request,
            recent_action=SubsidyRequestStates.REQUESTED,
            status=SubsidyRequestStates.REQUESTED,
        )
        self.enterprise_customer_uuid = uuid4()

    def test_string_representation(self):
        """
        Test the string representation of the model.
        """
        expected_string = (
            f"<LearnerCreditRequestActions for request {self.learner_credit_request}"
            f" with action {self.action.recent_action}>"
        )
        self.assertEqual(str(self.action), expected_string)

    def test_model_creation(self):
        """
        Test that a LearnerCreditRequestActions instance is created successfully.
        """
        self.assertIsNotNone(self.action.uuid)
        self.assertEqual(self.action.recent_action, SubsidyRequestStates.REQUESTED)
        self.assertEqual(self.action.status, SubsidyRequestStates.REQUESTED)
        self.assertIsNone(self.action.error_reason)
        self.assertIsNone(self.action.traceback)

    def test_reminded_action(self):
        """
        Test creating an action with the REMINDED state.
        """
        reminded_action = LearnerCreditRequestActionsFactory(
            learner_credit_request=self.learner_credit_request,
            recent_action=LearnerCreditAdditionalActionStates.REMINDED,
            status=LearnerCreditAdditionalActionStates.REMINDED,
        )
        self.assertEqual(reminded_action.recent_action, LearnerCreditAdditionalActionStates.REMINDED)
        self.assertEqual(reminded_action.status, LearnerCreditAdditionalActionStates.REMINDED)

    def test_error_action(self):
        """
        Test creating an action with an error.
        """
        error_action = LearnerCreditRequestActionsFactory(
            learner_credit_request=self.learner_credit_request,
            recent_action=SubsidyRequestStates.ERROR,
            status=SubsidyRequestStates.ERROR,
            error_reason=LearnerCreditRequestActionErrorReasons.FAILED_APPROVAL,
            traceback="An error occurred",
        )
        self.assertEqual(error_action.recent_action, SubsidyRequestStates.ERROR)
        self.assertEqual(error_action.status, SubsidyRequestStates.ERROR)
        self.assertEqual(error_action.error_reason, LearnerCreditRequestActionErrorReasons.FAILED_APPROVAL)
        self.assertEqual(error_action.traceback, "An error occurred")

    def test_model_update(self):
        """
        Test updating a LearnerCreditRequestActions instance.
        """
        self.action.recent_action = SubsidyRequestStates.APPROVED
        self.action.status = LearnerCreditRequestUserMessages.CHOICES[3][
            0
        ]  # APPROVED choice
        self.action.save()
        updated_action = LearnerCreditRequestActions.objects.get(
            uuid=self.action.uuid
        )
        self.assertEqual(
            updated_action.recent_action, SubsidyRequestStates.APPROVED
        )
        self.assertEqual(
            updated_action.status,
            LearnerCreditRequestUserMessages.CHOICES[3][0],
        )

    def test_clean_success_when_learner_credit_config_inactive(self):
        """
        Test that validation passes when learner credit config exists but is inactive.
        """
        # Create inactive learner credit config
        lc_config = LearnerCreditRequestConfigurationFactory(active=False)
        PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(  # Changed here
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            learner_credit_request_config=lc_config,
        )

        config = SubsidyRequestCustomerConfigurationFactory.build(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.LICENSE,
        )

        # Should not raise ValidationError
        try:
            config.clean()
        except ValidationError:
            self.fail("ValidationError raised unexpectedly!")

    def test_clean_fails_when_learner_credit_already_enabled(self):
        """
        Test that validation fails when trying to enable B&R while learner credit is already active.
        """
        # Create active learner credit config
        lc_config = LearnerCreditRequestConfigurationFactory(active=True)
        PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(  # Changed here
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            learner_credit_request_config=lc_config,
        )

        config = SubsidyRequestCustomerConfigurationFactory.build(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.COUPON,
        )

        with self.assertRaises(ValidationError) as context:
            config.clean()

        expected_message = (
            "Browse & Request is already enabled for learner credit. "
            "Only one subsidy type can have Browse & Request enabled at a time."
        )
        self.assertIn(expected_message, str(context.exception))

    def test_save_calls_full_clean(self):
        """
        Test that save() method calls full_clean() which includes our custom validation.
        """
        # Create active learner credit config
        lc_config = LearnerCreditRequestConfigurationFactory(active=True)
        PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(  # Changed here
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            learner_credit_request_config=lc_config,
        )

        config = SubsidyRequestCustomerConfigurationFactory.build(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.LICENSE,
        )

        # save() should trigger validation and raise error
        with self.assertRaises(ValidationError):
            config.save()


class SubsidyRequestCustomerConfigurationTests(TestCase):
    """
    Test cases for SubsidyRequestCustomerConfiguration clean() validation.
    """

    def setUp(self):
        """
        Set up test data for each test case.
        """
        self.enterprise_customer_uuid = uuid4()

    def test_clean_success_when_no_conflicting_learner_credit_config(self):
        """
        Test that validation passes when enabling B&R and no conflicting learner credit config exists.
        This covers multiple success scenarios.
        """
        # Case 1: No learner credit config exists at all
        config = SubsidyRequestCustomerConfigurationFactory.build(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.LICENSE,
        )

        try:
            config.clean()
        except ValidationError:
            self.fail(
                "ValidationError raised unexpectedly when no LC config exists!"
            )

        # Case 2: Learner credit config exists but is inactive
        lc_config = LearnerCreditRequestConfigurationFactory(active=False)
        PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            learner_credit_request_config=lc_config,
        )

        try:
            config.clean()
        except ValidationError:
            self.fail(
                "ValidationError raised unexpectedly when LC config is inactive!"
            )

        # Case 3: subsidy_requests_enabled is False (should always pass)
        config_disabled = SubsidyRequestCustomerConfigurationFactory.build(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=False,  # Disabled
            subsidy_type=SubsidyTypeChoices.COUPON,
        )

        # Make LC config active now - should still pass because customer config is disabled
        lc_config.active = True
        lc_config.save()

        try:
            config_disabled.clean()
        except ValidationError:
            self.fail(
                "ValidationError raised unexpectedly when subsidy_requests_enabled=False!"
            )

    def test_clean_fails_when_learner_credit_already_enabled_and_save_validation(
        self,
    ):
        """
        Test that validation fails when trying to enable B&R while learner credit is already active.
        Also tests that save() method properly calls validation.
        """
        lc_config = LearnerCreditRequestConfigurationFactory(active=True)
        PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            learner_credit_request_config=lc_config,
        )

        config = SubsidyRequestCustomerConfigurationFactory.build(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.COUPON,
        )

        with self.assertRaises(ValidationError) as context:
            config.clean()

        expected_message = (
            "Browse & Request is already enabled for learner credit. "
            "Only one subsidy type can have Browse & Request enabled at a time."
        )
        self.assertIn(expected_message, str(context.exception))

        # Test that save() method also triggers validation
        config_for_save = SubsidyRequestCustomerConfigurationFactory.build(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.LICENSE,
        )

        with self.assertRaises(ValidationError) as save_context:
            config_for_save.save()

        self.assertIn(expected_message, str(save_context.exception))
