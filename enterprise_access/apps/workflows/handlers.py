"""
Default workflow handlers.
"""

import logging

from uuid import UUID

from enterprise_access.apps.workflows.decorators import workflow_action_step
from enterprise_access.utils import localized_utcnow

logger = logging.getLogger(__name__)


@workflow_action_step(
    name='Activate Enterprise Customer User',
    slug='activate_enterprise_customer_user',
)
def activate_enterprise_customer_user_changed():
    """
    Activates an enterprise customer user for the specified enterprise customer.
    """
    logger.info("Activating enterprise customer user for enterprise customer UUID: TBD")
    return {
        "id": 692281,
        "enterpriseCustomer": {
            "uuid": "852eac48-b5a9-4849-8490-743f3f2deabf",
            "name": "Executive Education (2U) Integration QA",
            "slug": "exec-ed-2u-integration-qa",
            "active": True,
            "other": "fields",
        },
        "active": True,
        "userId": 17737721,
        "user": {
            "id": 17737721,
            "username": "astankiewicz_edx",
            "firstName": "Adam",
            "lastName": "Stankiewicz",
            "email": "astankiewicz@edx.org",
            "isStaff": True,
            "isActive": True,
            "dateJoined": "2018-01-26T20:05:56Z"
        },
        "dataSharingConsentRecords": [
            {
                "username": "astankiewicz_edx",
                "enterpriseCustomerUuid": "852eac48-b5a9-4849-8490-743f3f2deabf",
                "exists": True,
                "consentProvided": True,
                "consentRequired": False,
                "courseId": "course-v1:HarvardX+CS50+X"
            }
        ],
        "groups": [],
        "created": "2022-11-08T14:49:09.494221Z",
        "inviteKey": "b584d15d-f286-4b25-b6da-766bab654394",
        "roleAssignments": ["enterprise_learner"],
        "enterpriseGroup": []
    }


def validate_context_for_activate_subscription_license(context):
    """
    Validates the context for the `activate_subscription_license` action step.
    """
    if not context:
        return

    enterprise_customer_uuid = context.get('enterprise_customer_uuid')
    try:
        return UUID(enterprise_customer_uuid)
    except ValueError as exc:
        raise ValueError(f"{enterprise_customer_uuid} is not a valid uuid.") from exc


@workflow_action_step(
    name='Activate Subscription License',
    slug='activate_subscription_license_changed',
    prerequisite_steps=['retrieve_subscription_licenses'],
    validate_func=validate_context_for_activate_subscription_license,
)
def activate_subscription_license(context, **kwargs):  # pylint: disable=unused-argument
    """
    Activates a subscription license for the specified subscription license.
    """
    enterprise_customer_uuid = context.get('enterprise_customer_uuid')
    logger.info(f"Activating subscription license for subscription license UUID: {enterprise_customer_uuid}")

    # TODO: Make API request to activate subscription license, etc.

    # Return mock response for activated subscription license. Will be exposed for other
    # workflow steps to use, as needed.
    return {
        "enterprise_customer_uuid": enterprise_customer_uuid,
        "license_uuid": "30793582-0b90-4420-8ce7-f465f87d1d1b",
        "license_activated": True,
        "activated_at": localized_utcnow().isoformat(),
    }


@workflow_action_step(
    name='Auto-apply Subscription License',
    slug='auto_apply_subscription_license',
)
def auto_apply_subscription_license():
    """
    Automatically applies a subscription license to an enterprise customer user.
    """
    logger.info("Automatically applying subscription license to enterprise customer user.")


@workflow_action_step(
    name='Retrieve Subscription Licenses',
    slug='retrieve_subscription_licenses',
)
def retrieve_subscription_licenses():
    """
    Retrieves a subscription license for the specified enterprise customer.
    """
    logger.info("Retrieving subscription license for enterprise customer UUID: TBD")
    # learner-licenses (license-manager)
    return {
        "count": 1,
        "current_page": 1,
        "customer_agreement": {
            "uuid": "ad01594d-f7c9-4978-b699-5e28d5be42eb",
            "enterprise_customer_uuid": "852eac48-b5a9-4849-8490-743f3f2deabf",
            "enterprise_customer_slug": "exec-ed-2u-integration-qa",
            "default_enterprise_catalog_uuid": None,
            "disable_expiration_notifications": False,
            "net_days_until_expiration": 242,
            "subscription_for_auto_applied_licenses": None,
            "available_subscription_catalogs": [
                "12301166-5ab9-4e00-86a6-75b1f57883bf"
            ]
        },
        "next": None,
        "num_pages": 1,
        "previous": None,
        "results": [
            {
                "uuid": "30793582-0b90-4420-8ce7-f465f87d1d1b",
                "status": "activated",
                "user_email": "astankiewicz@edx.org",
                "activation_date": "2024-08-20T14:14:37.137697Z",
                "last_remind_date": "2024-08-20T14:14:29.851901Z",
                "subscription_plan_uuid": "490fa134-3248-4c5a-bb5e-723386259f81",
                "revoked_date": None,
                "activation_key": "d69cfdb3-6eb1-4e6f-8782-7177bd8ba044",
                "customer_agreement": {},
                "subscription_plan": {
                    "title": "[QA] Test Subscription",
                    "uuid": "490fa134-3248-4c5a-bb5e-723386259f81",
                    "start_date": "2024-01-18T16:30:55Z",
                    "expiration_date": "2025-05-30T16:30:59Z",
                    "enterprise_customer_uuid": "852eac48-b5a9-4849-8490-743f3f2deabf",
                    "enterprise_catalog_uuid": "12301166-5ab9-4e00-86a6-75b1f57883bf",
                    "is_active": True,
                    "is_current": True,
                    "is_revocation_cap_enabled": False,
                    "days_until_expiration": 242,
                    "days_until_expiration_including_renewals": 242,
                    "is_locked_for_renewal_processing": False,
                    "should_auto_apply_licenses": None,
                    "created": "2024-01-19T16:32:38.235213Z",
                },
            },
        ],
        "start": 0,
    }


@workflow_action_step(
    name='Retrieve Credits Available',
    slug='retrieve_credits_available',
)
def retrieve_credits_available():
    """
    Retrieves the number of credits available for the specified enterprise customer.
    """
    logger.info("Retrieving credits available for enterprise customer UUID: TBD")
    return [
        {
            "uuid": "d69cfdb3-6eb1-4e6f-8782-7177bd8ba044",
            "active": True,
            "retired": False,
            "policy_type": "PerLearnerSpendCreditAccessPolicy",
            "assignment_configuration": None,
            "catalog_uuid": "12301166-5ab9-4e00-86a6-75b1f57883bf",
            "description": "",
            "display_name": "Example Policy",
            "enterprise_customer_uuid": "852eac48-b5a9-4849-8490-743f3f2deabf",
            "subsidy_expiration_date": "2025-05-30T16:30:59Z",
            "subsidy_uuid": "490fa134-3248-4c5a-bb5e-723386259f81",
            "remaining_balance": 500000,
        },
    ]


@workflow_action_step(
    name='Retrieve Enterprise Course Enrollments',
    slug='retrieve_enterprise_course_enrollments',
)
def retrieve_enterprise_course_enrollments():
    """
    Retrieves enterprise course enrollments for the enterprise customer user.
    """
    logger.info("Retrieving enterprise course enrollments for enterprise customer user.")


@workflow_action_step(
    name='Retrieve Default Enterprise Course Enrollments',
    slug='retrieve_default_enterprise_course_enrollments',
    required_params=['lms_user_id', 'enterprise_customer_uuid'],
)
def retrieve_default_enterprise_course_enrollments(context, **kwargs):  # pylint: disable=unused-argument
    """
    Retrieves default enterprise course enrollments for the enterprise customer user.
    """
    lms_user_id = context.get('lms_user_id')
    enterprise_customer_uuid = context.get('enterprise_customer_uuid')

    logger.info(
        f"Retrieving default enterprise course enrollments for enterprise customer "
        f"user with LMS ID: {lms_user_id} and enterprise customer UUID: {enterprise_customer_uuid}"
    )

    # TODO: Make API request to retrieve default enterprise course enrollments.

    # Mock response
    return {
        "lms_user_id": lms_user_id,
        "user_email": "user@example.com",
        "default_enterprise_course_enrollments_status": {
            "needs_enrollment": {
                "enrollable": [
                    # top-level course
                    {
                        "uuid":  "...",
                        "content_key": "edX+DemoX",
                        "course_run_key": "course-v1:edX+DemoX+Demo_Course",  # advertised course run
                        "content_metadata": {
                            "start_date": "...",
                            "end_date": "...",
                            "enroll_by_date": "...",
                            "enroll_start_date": "...",
                            "content_price": 123,
                        },
                        "applicable_enterprise_catalog_uuids": ["..."],
                        "enterprise_customer_uuid": "...",
                    },
                    # course run
                    {
                        "uuid":  "...",
                        "content_key": "course-v1:edX+DemoY+Demo_Course",
                        "course_run_key": "course-v1:edX+DemoY+Demo_Course",
                        "content_metadata": {
                            "start_date": "...",
                            "end_date": "...",
                            "enroll_by_date": "...",
                            "enroll_start_date": "...",
                            "content_price": 123,
                        },
                        "applicable_enterprise_catalog_uuids": ["...", ...], 
                        "enterprise_customer_uuid": "...",
                    }
                ],
                "not_enrollable": [],  
            },
            "already_enrolled": [
                # top-level course
                {
                    "uuid":  "...",
                    "content_key": "edX+DemoZ",
                    "course_run_key": "course-v1:edX+DemoZ+Demo_Course",  # advertised course run
                    "content_metadata": {
                        "start_date": "...",
                        "end_date": "...",
                        "enroll_by_date": "...",
                        "enroll_start_date": "...",
                        "content_price": 123,
                    },
                    "applicable_enterprise_catalog_uuids": ["...", ...],
                    "enterprise_customer_uuid": "...",
                    # represents whether existing enrollment is unenrolled/enrolled
                    "is_existing_course_enrollment_active": True
                }
            ]
        },
        # complete list of default enrollments, regardless of enrollment state
        "default_enterprise_course_enrollments": [],
        "metadata": {
            "total_default_enterprise_course_enrollments": 3,
            "total_needs_enrollment": {
                "enrollable": 2,
                "not_enrollable": 0,
            },
            "total_already_enrolled": 1,
        }
    }

@workflow_action_step(
    name='Enroll Default Enterprise Course Enrollments',
    slug='enroll_default_enterprise_course_enrollments',
    prerequisite_steps=[
        'retrieve_default_enterprise_course_enrollments',
        'retrieve_subscription_licenses',
    ],
    required_params=['lms_user_id', 'enterprise_customer_uuid'],
)
def enroll_default_enterprise_course_enrollments(context, result, **kwargs):  # pylint: disable=unused-argument
    """
    Enrolls an enterprise customer user in default enterprise course enrollments.
    """
    lms_user_id = context.get('lms_user_id')
    enterprise_customer_uuid = context.get('enterprise_customer_uuid')
    default_enterprise_course_enrollments = result.get('retrieve_default_enterprise_course_enrollments')
    default_enterprise_course_enrollments_metadata = default_enterprise_course_enrollments.get('metadata')
    total_needs_enrollment_enrollable = (
        default_enterprise_course_enrollments_metadata.get('total_needs_enrollment').get('enrollable')
    )

    if not total_needs_enrollment_enrollable:
        logger.info(
            f"No default enterprise course enrollments need enrollment for enterprise "
            f"customer UUID: {enterprise_customer_uuid} and LMS user ID: {lms_user_id}"
        )

    needs_enrollment = default_enterprise_course_enrollments.get('needs_enrollment').get('enrollable') or []
    needs_enrollment_course_run_keys = [
        default_enrollment.get('course_run_key')
        for default_enrollment in needs_enrollment
    ]
    logger.info(
        f"Enrolling enterprise customer user in default enterprise course "
        f"enrollments {needs_enrollment_course_run_keys} for enterprise "
        f"customer UUID: {enterprise_customer_uuid}"
    )

    #
    # 1. Determine redeemability of each not-yet-enrolled default enterprise course enrollment with subscription license
    #    or Learner Credit.
    #
    # 2. Redeem/enroll the enterprise customer user in the not-yet-enrolled and redeemable default enterprise course
    #    enrollments, using the appropriate redemption method.
    #      - For subscriptions redemption (VSF):
    #        * Either call `enroll_learners_in_courses` (edx-enterprise) directly OR consider the
    #          implications of getting subscriptions into the can-redeem / redeem paradigm (redeem
    #          uses same `enroll_learners_in_courses` already).
    #    - For Learner Credit redemption (future):
    #        * Redeem with existing can-redeem / redeem paradigm.

    return {
        'success': True,
        # TODO: Add any additional metadata or results here
    }
