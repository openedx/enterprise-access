"""
Mixins for accessing `HandlerContext` data for bffs app
"""

import time

import logging
from urllib.error import HTTPError

from enterprise_access.apps.bffs.api import (
    get_and_cache_enterprise_course_enrollments,
    get_and_cache_secured_algolia_search_keys,
    transform_secured_algolia_api_key_response
)
from enterprise_access.apps.bffs.constants import COURSE_ENROLLMENT_STATUSES, UNENROLLABLE_COURSE_STATUSES

logger = logging.getLogger(__name__)


class BFFContextDataMixin:
    """
    Mixin to validate that the `self.context` attribute is defined.
    """

    def __init__(self, *args, **kwargs):
        """
        Initialize the BFFDataMixin, ensuring that the
        `self.context` attribute is defined.
        """
        super().__init__(*args, **kwargs)
        if not hasattr(self, 'context'):
            raise AttributeError("The `self.context` attribute must be defined.")


class EnterpriseCustomerUserSubsidiesDataMixin(BFFContextDataMixin):
    """
    Mixin to access enterprise customer user subsidies data from the context.
    """

    @property
    def enterprise_customer_user_subsidies(self):
        """
        Get enterprise customer user subsidies from the context.
        """
        return self.context.data.get('enterprise_customer_user_subsidies', {})


class LearnerSubscriptionsDataMixin(EnterpriseCustomerUserSubsidiesDataMixin):
    """
    Mixin to access learner subscriptions data from the context.
    """

    @property
    def subscriptions(self):
        """
        Get subscriptions from the context.
        """
        return self.enterprise_customer_user_subsidies.get('subscriptions', {})

    @property
    def customer_agreement(self):
        """
        Get customer agreement from the context.
        """
        return self.subscriptions.get('customer_agreement', {})

    @property
    def subscription_licenses(self):
        """
        Get subscription licenses from the context.
        """
        return self.subscriptions.get('subscription_licenses', [])

    @property
    def subscription_licenses_by_status(self):
        """
        Get subscription licenses by status from the context.
        """
        return self.subscriptions.get('subscription_licenses_by_status', {})

    @property
    def subscription_license(self):
        """
        Get subscription license from the context.
        """
        return self.subscriptions.get('subscription_license', None)

    @property
    def subscription_plan(self):
        """
        Get subscription plan from the context.
        """
        return self.subscriptions.get('subscription_plan', {})

    @property
    def show_subscription_expiration_notifications(self):
        """
        Get whether subscription expiration notifications should be shown from the context.
        """
        return self.subscriptions.get('show_expiration_notifications', False)


class LearnerSubsidiesDataMixin(LearnerSubscriptionsDataMixin):
    """
    Mixin to access learner subsidies data from the context (e.g., subscriptions)
    """


class BaseLearnerDataMixin(LearnerSubsidiesDataMixin, BFFContextDataMixin):
    """
    Mixin to access shared common properties for learner-focused routes.
    """

    @property
    def default_enterprise_enrollment_intentions(self):
        """
        Get default enterprise enrollment intentions from the context.
        """
        return self.context.data.get('default_enterprise_enrollment_intentions', {})


class EnterpriseCourseEnrollmentsDataMixin(BaseLearnerDataMixin):
    """
    Mixin to load and access enterprise course enrollments data from the context.
    """

    @property
    def enterprise_course_enrollments(self):
        """
        Get enterprise course enrollments from the context.
        """
        return self.context.data.get('enterprise_course_enrollments', [])

    @property
    def all_enrollments_by_status(self):
        """
        Get all enrollments by status from the context.
        """
        return self.context.data.get('all_enrollments_by_status', {})

    def load_enterprise_course_enrollments(self):
        """
        Loads enterprise course enrollments data.

        Returns:
            list: A list of enterprise course enrollments.
        """
        if not self.context.is_request_user_linked_to_enterprise_customer:
            # Skip loading enterprise course enrollments if the request user is not linked to the enterprise customer
            logger.info(
                'Request user %s is not linked to enterprise customer %s. Skipping enterprise course enrollments.',
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            return

        try:
            enterprise_course_enrollments = get_and_cache_enterprise_course_enrollments(
                request=self.context.request,
                enterprise_customer_uuid=self.context.enterprise_customer_uuid,
                is_active=True,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Error retrieving enterprise course enrollments")
            self.add_error(
                user_message="Could not retrieve your enterprise course enrollments.",
                developer_message=f"Failed to retrieve enterprise course enrollments: {exc}",
            )

        try:
            course_enrollments_data = self._transform_enterprise_course_enrollments(enterprise_course_enrollments)
            self.context.data['enterprise_course_enrollments'] = course_enrollments_data.get('enrollments', [])
            self.context.data['all_enrollments_by_status'] = course_enrollments_data.get('enrollments_by_status', {})
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Error transforming enterprise course enrollments")
            self.add_error(
                user_message="Could not transform your enterprise course enrollments.",
                developer_message=f"Failed to transform enterprise course enrollments: {exc}",
            )

    def _transform_enterprise_course_enrollments(self, enterprise_course_enrollments):
        """
        Transform the enterprise course enrollments data.

        Args:
            enterprise_course_enrollments: The enterprise course enrollments data.
        Returns:
            The transformed enterprise course enrollments data.
        """
        enrollments = [
            self._transform_enterprise_course_enrollment(enterprise_course_enrollment)
            for enterprise_course_enrollment in enterprise_course_enrollments
        ]
        enrollments_by_status = self._group_course_enrollments_by_status(enrollments)
        return {
            'enrollments': enrollments,
            'enrollments_by_status': enrollments_by_status,
        }

    def _transform_enterprise_course_enrollment(self, enrollment):
        """
        Transform the enterprise course enrollment data.

        Args:
            enrollment: The enterprise course enrollment data.
        Returns:
            The transformed enterprise course enrollment data.
        """

        # Extract specific fields verbatim from the enrollment data
        fields_to_pluck = [
            'course_run_id',
            'course_run_status',
            'course_key',
            'course_type',
            'created',
            'end_date',
            'enroll_by',
            'is_enrollment_active',
            'is_revoked',
            'micromasters_title',
            'mode',
            'org_name',
            'pacing',
            'product_source',
            'resume_course_run_url',
            'start_date',
        ]
        transformed_data = {
            field: enrollment.get(field)
            for field in fields_to_pluck
        }

        # Update transformed enrollment data with additional derived fields
        transformed_data.update({
            'title': enrollment.get('display_name'),
            # The link to course here gives precedence to the resume course link, which is
            # present if the learner has made progress. If the learner has not made progress,
            # we should link to the main course run URL. Similarly, if the resume course link
            # is not set in the API response, we should fallback on the normal course link.
            'link_to_course': (
                enrollment.get('resume_course_run_url') or
                enrollment.get('course_run_url')
            ),
            'link_to_certificate': enrollment.get('certificate_download_url'),
            'has_emails_enabled': enrollment.get('emails_enabled', False),
            'notifications': enrollment.get('due_dates'),
            'can_unenroll': self._can_unenroll_course_enrollment(enrollment),
        })

        return transformed_data

    def _group_course_enrollments_by_status(self, course_enrollments):
        """
        Groups course enrollments by their status.

        Args:
            enrollments (list): List of course enrollment dictionaries.

        Returns:
            dict: A dictionary where keys are status names and values are lists of enrollments with that status.
        """
        statuses = {
            COURSE_ENROLLMENT_STATUSES.IN_PROGRESS: [],
            COURSE_ENROLLMENT_STATUSES.UPCOMING: [],
            COURSE_ENROLLMENT_STATUSES.COMPLETED: [],
            COURSE_ENROLLMENT_STATUSES.SAVED_FOR_LATER: [],
        }
        for enrollment in course_enrollments:
            status = enrollment.get('course_run_status')
            if status in statuses:
                statuses[status].append(enrollment)
        return statuses

    def _can_unenroll_course_enrollment(self, enrollment):
        """
        Determines whether a course enrollment may be unenrolled based on its enrollment
        status (e.g., in progress, completed) and enrollment completion.
        """
        return (
            enrollment.get('course_run_status') in UNENROLLABLE_COURSE_STATUSES and
            not enrollment.get('certificate_download_url')
        )


class AlgoliaDataMixin(BFFContextDataMixin):
    """
    Mixin to handle Algolia search functionality and API key management.
    """

    def load_secured_algolia_api_key(self):
        """
        Fetches and initializes the secured Algolia API keys for the request user.
        Updates the context with the fetched keys.
        """
        time.sleep(5)

        try:
            secured_algolia_api_key_data = get_and_cache_secured_algolia_search_keys(
                self.context.request,
                self.context.enterprise_customer_uuid,
            )

            secured_algolia_api_key = None
            catalog_uuids_to_catalog_query_uuids = {}

            try:
                secured_algolia_api_key, catalog_uuids_to_catalog_query_uuids = (
                    transform_secured_algolia_api_key_response(secured_algolia_api_key_data)
                )
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    'Error transforming secured algolia api key for request user %s,'
                    'enterprise customer uuid %s and/or slug %s',
                    self.context.lms_user_id,
                    self.context.enterprise_customer_uuid,
                    self.context.enterprise_customer_slug,
                )

            # Update context with the fetched data
            self.context.update_algolia_keys(
                secured_algolia_api_key,
                catalog_uuids_to_catalog_query_uuids
            )

            # Log if no Algolia key or catalog mapping was found
            if not (secured_algolia_api_key and catalog_uuids_to_catalog_query_uuids):
                logger.info(
                    'No secured algolia key found for request user %s, enterprise customer uuid %s, '
                    'and/or enterprise slug %s',
                    self.context.lms_user_id,
                    self.context.enterprise_customer_uuid,
                    self.context.enterprise_customer_slug,
                )
                self.context.add_error(
                    user_message='No secured algolia api key or catalog query mapping found',
                    developer_message=(
                        f'No secured algolia api key or catalog query mapping found for request '
                        f'user {self.context.lms_user_id} and enterprise uuid '
                        f'{self.context.enterprise_customer_uuid}'
                    ),
                )

        except HTTPError as exc:
            exception_response = exc.response.json()
            exception_response_user_message = exception_response.get('user_message')
            exception_response_developer_message = exception_response.get('developer_message')

            logger.exception(
                'HTTP Error initializing the secured algolia api keys for request user %s, '
                'enterprise customer uuid %s',
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            self.context.add_error(
                user_message=exception_response_user_message or 'Error initializing search functionality',
                developer_message=exception_response_developer_message or str(exc),
                status_code=exc.response.status_code
            )

        except Exception as exc:  # pylint: disable=broad-except
            logger.exception(
                'Error initializing the secured algolia api keys for request user %s, '
                'enterprise customer uuid %s',
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            self.context.add_error(
                user_message='Error initializing search functionality',
                developer_message=f'Could not initialize the secured algolia api keys. Error: {exc}'
            )


class LearnerDashboardDataMixin(EnterpriseCourseEnrollmentsDataMixin, AlgoliaDataMixin, BaseLearnerDataMixin):
    """
    Mixin to access learner dashboard data from the context.
    """
