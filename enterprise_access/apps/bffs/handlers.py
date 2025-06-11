""""
Handlers for bffs app.
"""
import json
import logging
import time
from enum import Enum, auto

from enterprise_access.apps.api_client.constants import LicenseStatuses
from enterprise_access.apps.api_client.license_manager_client import LicenseManagerUserApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.bffs.api import (
    get_and_cache_default_enterprise_enrollment_intentions_learner_status,
    get_and_cache_subscription_licenses_for_learner,
    invalidate_default_enterprise_enrollment_intentions_learner_status_cache,
    invalidate_enterprise_course_enrollments_cache,
    invalidate_subscription_licenses_cache
)
from enterprise_access.apps.bffs.context import HandlerContext
from enterprise_access.apps.bffs.mixins import AlgoliaDataMixin, BaseLearnerDataMixin, LearnerDashboardDataMixin
from enterprise_access.apps.bffs.serializers import EnterpriseCustomerUserSubsidiesSerializer
from enterprise_access.apps.bffs.task_runner import ConcurrentTaskRunner

logger = logging.getLogger(__name__)

MOCK_TASK_DELAY = 5


class BaseHandler:
    """
    A base handler class that provides shared core functionality for different BFF handlers.
    The `BaseHandler` includes core methods for loading data and adding errors to the context.
    """

    def __init__(self, context: HandlerContext):
        """
        Initializes the BaseHandler with a HandlerContext.
        Args:
            context (HandlerContext): The context object containing request information and data.
        """
        self.context = context

    def load_and_process(self):
        """
        Loads and processes data. This method should be extended by subclasses to implement
        specific data loading and transformation logic.
        """
        raise NotImplementedError("Subclasses must implement `load_and_process` method.")

    def add_error(self, user_message, developer_message, status_code=None):
        """
        Adds an error to the context.
        Output fields determined by the ErrorSerializer
        """
        self.context.add_error(
            user_message=user_message,
            developer_message=developer_message,
            status_code=status_code,
        )

    def add_warning(self, user_message, developer_message):
        """
        Adds an error to the context.
        Output fields determined by the WarningSerializer
        """
        self.context.add_warning(
            user_message=user_message,
            developer_message=developer_message,
        )


class BaseLearnerPortalHandler(BaseHandler, AlgoliaDataMixin, BaseLearnerDataMixin):
    """
    A base handler class for learner-focused routes.

    The `BaseLearnerHandler` extends `BaseHandler` and provides shared core functionality
    across all learner-focused page routes, such as the learner dashboard, search, and course routes.
    """

    class CONCURRENCY_GROUPS(Enum):
        """
        Group names for concurrent tasks.
        """
        DEFAULT = auto()

    def __init__(self, context):
        """
         Initializes the BaseLearnerPortalHandler with a HandlerContext and API clients.
         Args:
             context (HandlerContext): The context object containing request information and data.
         """
        super().__init__(context)

        # API Clients
        self.license_manager_user_api_client = LicenseManagerUserApiClient(self.context.request)
        self.lms_api_client = LmsApiClient()

    def _get_concurrent_tasks(self):
        """
        Establishes the data structure for tasks and adds base tasks.
        Subclasses may call this method via super() to extend the tasks
        for any specific group.
        """
        # Initialize groups
        tasks = {
            self.CONCURRENCY_GROUPS.DEFAULT: [],
        }

        # Add tasks to default group
        tasks[self.CONCURRENCY_GROUPS.DEFAULT].extend([
            self.load_and_process_subsidies,
            self.load_secured_algolia_api_key,
            self.load_and_process_default_enrollment_intentions,
        ])

        return tasks

    def load_secured_algolia_api_key(self):
        """
        Temporary override to add delay.
        """
        time.sleep(MOCK_TASK_DELAY)
        super().load_secured_algolia_api_key()

    def load_and_process_subsidies(self):
        """
        Load and process subsidies for learners
        """
        time.sleep(MOCK_TASK_DELAY)
        empty_subsidies = {
            'subscriptions': {
                'customer_agreement': None,
            },
        }
        self.context.data['enterprise_customer_user_subsidies'] =\
            EnterpriseCustomerUserSubsidiesSerializer(empty_subsidies).data

        # Retrieve and process subsidies
        self.load_and_process_subscription_licenses()

    def load_and_process_default_enrollment_intentions(self):
        """
        Helper method to encapsulate the two-step enrollment process
        into a single unit of work for the concurrent runner.
        """
        time.sleep(MOCK_TASK_DELAY)
        self.load_default_enterprise_enrollment_intentions()
        self.enroll_in_redeemable_default_enterprise_enrollment_intentions()

    def load_and_process(self):
        """
        Loads and processes data. This is a basic implementation that can be overridden by subclasses.
        """
        try:
            # Verify enterprise customer exists and has learner portal enabled
            self.ensure_learner_portal_enabled()

            # Transform enterprise customer data
            self.transform_enterprise_customers()
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception(
                "Error loading/processing learner portal handler for request user %s and enterprise customer %s",
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            self.add_error(
                user_message="Could not load and/or process common data",
                developer_message=f"Unable to load and/or process common learner portal data: {exc}",
            )
            return

        # Run concurrent tasks
        all_tasks_to_run = self._get_concurrent_tasks()
        with ConcurrentTaskRunner(task_definitions=all_tasks_to_run) as runner:
            task_results = runner.run_group(self.CONCURRENCY_GROUPS.DEFAULT)
            def handle_task_error(task_name, error_message):
                logger.error(
                    "Error running concurrent task '%s' for request user %s and enterprise customer %s: %s",
                    task_name,
                    self.context.lms_user_id,
                    self.context.enterprise_customer_uuid,
                    error_message,
                )
                self.add_error(
                    user_message="Could not load and/or process a concurrent task",
                    developer_message=(
                        f"Unable to load and/or process concurrent task '{task_name}': {error_message}"
                    ),
                )
            runner.handle_failed_tasks(task_results, handle_task_error)

    def ensure_learner_portal_enabled(self):
        """
        Ensure the learner portal is enabled for the enterprise
        customer attributes in the context. If not, remove the enterprise
        customer data from the context and add a warning.
        """
        for customer_record_key in ('enterprise_customer', 'active_enterprise_customer', 'staff_enterprise_customer'):
            if not (customer_record := getattr(self.context, customer_record_key, None)):
                logger.warning(
                    f"No {customer_record_key} found in the context for request user {self.context.lms_user_id}"
                )
                continue

            if not customer_record.get('enable_learner_portal', False):
                logger.warning(
                    f"Learner portal is not enabled for enterprise customer {customer_record.get('uuid')}"
                )
                # Remove the enterprise customer data from the context
                self.context.data.pop(customer_record_key, None)

                # Add a warning to the context
                self.add_warning(
                    user_message="Learner portal not enabled for enterprise customer",
                    developer_message=(
                        f"[{customer_record_key}] Learner portal not enabled for enterprise "
                        f"customer {customer_record.get('uuid')} for request user {self.context.lms_user_id}"
                    ),
                )

    def transform_enterprise_customers(self):
        """
        Transform enterprise customer metadata retrieved by self.context.
        """
        for customer_record_key in ('enterprise_customer', 'active_enterprise_customer', 'staff_enterprise_customer'):
            if not (customer_record := getattr(self.context, customer_record_key, None)):
                logger.warning(
                    f"No {customer_record_key} found in the context for request user {self.context.lms_user_id}"
                )
                continue
            self.context.data[customer_record_key] = self.transform_enterprise_customer(customer_record)

        if enterprise_customer_users := self.context.all_linked_enterprise_customer_users:
            self.context.data['all_linked_enterprise_customer_users'] = [
                self.transform_enterprise_customer_user(enterprise_customer_user)
                for enterprise_customer_user in enterprise_customer_users
                if enterprise_customer_user.get('enterprise_customer').get('enable_learner_portal') is True
            ]
        else:
            logger.warning(
                f"No linked enterprise customer users found in the context for request user {self.context.lms_user_id}"
            )

    def transform_enterprise_customer_user(self, enterprise_customer_user):
        """
        Transform the enterprise customer user data.

        Args:
            enterprise_customer_user: The enterprise customer user data.
        Returns:
            The transformed enterprise customer user data.
        """
        enterprise_customer = enterprise_customer_user.get('enterprise_customer')
        return {
            **enterprise_customer_user,
            'enterprise_customer': self.transform_enterprise_customer(enterprise_customer),
        }

    def transform_enterprise_customer(self, enterprise_customer):
        """
        Transform the enterprise customer data.

        Args:
            enterprise_customer: The enterprise customer data.

        Returns:
            The transformed enterprise customer data.
        """
        # Learner Portal is enabled, so transform the enterprise customer data.
        identity_provider = enterprise_customer.get("identity_provider")
        active_integrations = enterprise_customer.get("active_integrations")
        disable_search = bool(
            not enterprise_customer.get("enable_integrated_customer_learner_portal_search", False) and
            identity_provider
        )
        show_integration_warning = bool(not disable_search and active_integrations)

        return {
            **enterprise_customer,
            'disable_search': disable_search,
            'show_integration_warning': show_integration_warning,
        }

    def load_subscription_licenses(self):
        """
        Load subscription licenses for the learner.
        """
        try:
            subscriptions_result = get_and_cache_subscription_licenses_for_learner(
                request=self.context.request,
                enterprise_customer_uuid=self.context.enterprise_customer_uuid,
                include_revoked=True,
                current_plans_only=False,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Error loading subscription licenses for request user %s and enterprise customer %s",
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            self.add_error(
                user_message="Unable to retrieve subscription licenses",
                developer_message=f"Unable to fetch subscription licenses. Error: {exc}",
            )
            return

        try:
            subscriptions_data = self.transform_subscriptions_result(subscriptions_result)
            self.context.data['enterprise_customer_user_subsidies'].update({
                'subscriptions': subscriptions_data,
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Error transforming subscription licenses for request user %s and enterprise customer %s",
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            self.add_error(
                user_message="Unable to transform subscription licenses",
                developer_message=f"Unable to transform subscription licenses. Error: {exc}",
            )

    def _extract_subscription_license(self, subscription_licenses_by_status):
        """
        Extract subscription licenses from the subscription licenses by status.
        """
        license_status_priority_order = [
            LicenseStatuses.ACTIVATED,
            LicenseStatuses.ASSIGNED,
            LicenseStatuses.REVOKED,
        ]
        subscription_license = next(
            (
                license
                for status in license_status_priority_order
                for license in subscription_licenses_by_status.get(status, [])
            ),
            None,
        )
        return subscription_license

    def transform_subscriptions_result(self, subscriptions_result):
        """
        Transform subscription licenses data if needed.
        """
        subscription_licenses = subscriptions_result.get('results', [])
        subscription_licenses_by_status = {}

        # Sort licenses by whether the associated subscription plans
        # are current; current plans should be prioritized over non-current plans.
        ordered_subscription_licenses = sorted(
            subscription_licenses,
            key=lambda license: not license.get('subscription_plan', {}).get('is_current'),
        )

        # Group licenses by status
        for subscription_license in ordered_subscription_licenses:
            status = subscription_license.get('status')
            if status not in subscription_licenses_by_status:
                subscription_licenses_by_status[status] = []
            subscription_licenses_by_status[status].append(subscription_license)

        customer_agreement = subscriptions_result.get('customer_agreement')
        subscription_license = self._extract_subscription_license(subscription_licenses_by_status)
        subscription_plan = subscription_license.get('subscription_plan') if subscription_license else None

        # Determine if expiration notifications should be shown
        if not customer_agreement:
            show_expiration_notifications = False
        else:
            disable_expiration_notifications = customer_agreement.get('disable_expiration_notifications', False)
            custom_expiration_messaging = customer_agreement.get('has_custom_license_expiration_messaging_v2', False)
            show_expiration_notifications = not (disable_expiration_notifications or custom_expiration_messaging)

        return {
            'customer_agreement': customer_agreement,
            'subscription_licenses': subscription_licenses,
            'subscription_licenses_by_status': subscription_licenses_by_status,
            'subscription_license': subscription_license,
            'subscription_plan': subscription_plan,
            'show_expiration_notifications': show_expiration_notifications,
        }

    def _current_subscription_licenses_for_status(self, status):
        """
        Filter subscription licenses by license status and current subscription plan.
        """
        current_licenses_for_status = [
            _license for _license in self.subscription_licenses_by_status.get(status, [])
            if _license['subscription_plan']['is_current']
        ]
        return current_licenses_for_status

    @property
    def current_activated_licenses(self):
        """
        Returns list of current, activated licenses, if any, for the user.
        """
        activated_licenses = self._current_subscription_licenses_for_status(LicenseStatuses.ACTIVATED)
        return activated_licenses

    @property
    def current_activated_license(self):
        """
        Returns an activated license for the user iff the related subscription plan is current,
        otherwise returns None.
        """
        return self.current_activated_licenses[0] if self.current_activated_licenses else None

    @property
    def current_revoked_licenses(self):
        """
        Returns a revoked license for the user iff the related subscription plan is current,
        otherwise returns None.
        """
        return self._current_subscription_licenses_for_status(LicenseStatuses.REVOKED)

    @property
    def current_assigned_licenses(self):
        """
        Returns an assigned license for the user iff the related subscription plan is current,
        otherwise returns None.
        """
        return self._current_subscription_licenses_for_status(LicenseStatuses.ASSIGNED)

    def process_subscription_licenses(self):
        """
        Process loaded subscription licenses, including performing side effects such as:
            * Checking if there is an activated license
            * Checking and activating assigned licenses
            * Checking and auto applying licenses

        This method is called after `load_subscription_licenses` to handle further actions based
        on the loaded data.
        """
        if not self.subscriptions:
            # Skip process if there are no subscriptions data
            logger.warning("No subscription data found for the request user %s", self.context.lms_user_id)
            return

        if self.current_activated_license:
            # Skip processing if request user already has an activated license(s)
            logger.info("User %s already has an activated license", self.context.lms_user_id)
            return

        # Check if there are 'assigned' licenses that need to be activated
        self.check_and_activate_assigned_license()

        # Check if the user should be auto-applied a license
        self.check_and_auto_apply_license()

    def load_and_process_subscription_licenses(self):
        """
        Helper to load subscription licenses into the context then processes them
        by determining by:
            * Checking if there is an activated license
            * Checking and activating assigned licenses
            * Checking and auto applying licenses
        """
        self.load_subscription_licenses()
        self.process_subscription_licenses()

    def check_and_activate_assigned_license(self):
        """
        Check if there are assigned licenses that need to be activated.
        """
        subscription_licenses_by_status = self.subscription_licenses_by_status
        activated_licenses = []
        for subscription_license in self.current_assigned_licenses:
            activation_key = subscription_license.get('activation_key')
            if activation_key:
                try:
                    # Perform side effect: Activate the assigned license
                    activated_license = self.license_manager_user_api_client.activate_license(activation_key)

                    # Invalidate the subscription licenses cache as the cached data changed
                    # with the now-activated license.
                    invalidate_subscription_licenses_cache(
                        enterprise_customer_uuid=self.context.enterprise_customer_uuid,
                        lms_user_id=self.context.lms_user_id,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    license_uuid = subscription_license.get('uuid')
                    logger.exception(f"Error activating license {license_uuid}")
                    self.add_error(
                        user_message="Unable to activate subscription license",
                        developer_message=f"Could not activate subscription license {license_uuid}, Error: {exc}",
                    )
                    return

                # Update the subscription_license data with the activation status and date; the activated license is not
                # returned from the API, so we need to manually update the license object we have available.
                transformed_activated_subscription_licenses = [activated_license]
                activated_licenses.append(transformed_activated_subscription_licenses[0])
            else:
                license_uuid = subscription_license.get('uuid')
                logger.error(f"Activation key not found for license {license_uuid}")
                self.add_error(
                    user_message="No subscription license activation key found",
                    developer_message=f"Activation key not found for license {license_uuid}",
                )

        # Update the subscription_licenses_by_status data with the activated licenses
        updated_activated_licenses = self.current_activated_licenses
        updated_activated_licenses.extend(activated_licenses)
        if updated_activated_licenses:
            subscription_licenses_by_status[LicenseStatuses.ACTIVATED] = updated_activated_licenses

        activated_license_uuids = {license['uuid'] for license in activated_licenses}
        remaining_assigned_licenses = [
            subscription_license
            for subscription_license in self.current_assigned_licenses
            if subscription_license['uuid'] not in activated_license_uuids
        ]
        if remaining_assigned_licenses:
            subscription_licenses_by_status[LicenseStatuses.ASSIGNED] = remaining_assigned_licenses
        else:
            subscription_licenses_by_status.pop(LicenseStatuses.ASSIGNED, None)

        self.context.data['enterprise_customer_user_subsidies']['subscriptions'].update({
            'subscription_licenses_by_status': subscription_licenses_by_status,
        })

        # Update the subscription_licenses data with the activated licenses
        updated_subscription_licenses = []
        for subscription_license in self.subscription_licenses:
            for activated_license in activated_licenses:
                if subscription_license.get('uuid') == activated_license.get('uuid'):
                    updated_subscription_licenses.append(activated_license)
                    break
                updated_subscription_licenses.append(subscription_license)
        if updated_subscription_licenses:
            self.context.data['enterprise_customer_user_subsidies']['subscriptions'].update({
                'subscription_licenses': updated_subscription_licenses,
            })

        # Update the subscription_license and subscription_plan data given the activated license
        subscription_license = self._extract_subscription_license(subscription_licenses_by_status)
        subscription_plan = subscription_license.get('subscription_plan') if subscription_license else None
        self.context.data['enterprise_customer_user_subsidies']['subscriptions'].update({
            'subscription_license': subscription_license,
            'subscription_plan': subscription_plan,
        })

    def check_and_auto_apply_license(self):
        """
        Check if auto-apply licenses are available and apply them to the user.
        """
        if (self.subscription_licenses or not self.context.is_request_user_linked_to_enterprise_customer):
            # Skip auto-apply if:
            #   - User has assigned/current license(s)
            #   - User has activated/current license(s)
            #   - User has revoked/current license(s)
            #   - User is not explicitly linked to the enterprise customer (e.g., staff request user)
            return

        subscription_licenses_by_status = self.subscription_licenses_by_status
        customer_agreement = self.subscriptions.get('customer_agreement') or {}
        has_subscription_plan_for_auto_apply = (
            bool(customer_agreement.get('subscription_for_auto_applied_licenses')) and
            customer_agreement.get('net_days_until_expiration') > 0
        )
        has_idp_or_univeral_link_enabled = (
            self.context.enterprise_customer.get('identity_provider') or
            customer_agreement.get('enable_auto_applied_subscriptions_with_universal_link')
        )
        is_eligible_for_auto_apply = has_subscription_plan_for_auto_apply and has_idp_or_univeral_link_enabled
        if not is_eligible_for_auto_apply:
            # Skip auto-apply if the customer agreement does not have a subscription plan for auto-apply
            return

        try:
            # Perform side effect: Auto-apply license
            auto_applied_license = self.license_manager_user_api_client.auto_apply_license(
                customer_agreement.get('uuid')
            )
            # Invalidate the subscription licenses cache as the cached data changed with the auto-applied license.
            invalidate_subscription_licenses_cache(
                enterprise_customer_uuid=self.context.enterprise_customer_uuid,
                lms_user_id=self.context.lms_user_id,
            )
            # Update the context with the auto-applied license data
            licenses = self.subscription_licenses + [auto_applied_license]
            subscription_licenses_by_status['activated'] = [auto_applied_license]
            self.context.data['enterprise_customer_user_subsidies']['subscriptions'].update({
                'subscription_licenses': licenses,
                'subscription_licenses_by_status': subscription_licenses_by_status,
                'subscription_license': auto_applied_license,
                'subscription_plan': auto_applied_license.get('subscription_plan'),
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Error auto-applying subscription license for user %s and "
                "enterprise customer %s and customer agreement %s",
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
                customer_agreement.get('uuid'),
            )
            self.add_error(
                user_message="Unable to auto-apply a subscription license.",
                developer_message=(
                    f"Could not auto-apply a subscription license for "
                    f"customer agreement {customer_agreement.get('uuid')}, Error: {exc}",
                )
            )

    def load_default_enterprise_enrollment_intentions(self):
        """
        Load default enterprise course enrollments (stubbed)
        """
        if not self.context.is_request_user_linked_to_enterprise_customer:
            # Skip loading default enterprise enrollment intentions if the request
            # user is not linked to specified enterprise customer (e.g., staff request user)
            logger.info(
                'Request user %s is not linked to enterprise customer %s. Skipping default '
                'enterprise enrollment intentions.',
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            return

        try:
            default_enterprise_enrollment_intentions =\
                get_and_cache_default_enterprise_enrollment_intentions_learner_status(
                    request=self.context.request,
                    enterprise_customer_uuid=self.context.enterprise_customer_uuid,
                )
            self.context.data['default_enterprise_enrollment_intentions'] = default_enterprise_enrollment_intentions
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Error loading default enterprise enrollment intentions for user %s and enterprise customer %s",
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            self.add_error(
                user_message="Could not load default enterprise enrollment intentions",
                developer_message=f"Could not load default enterprise enrollment intentions. Error: {e}",
            )

    def enroll_in_redeemable_default_enterprise_enrollment_intentions(self):
        """
        Enroll in redeemable courses.
        """
        enrollment_statuses = self.default_enterprise_enrollment_intentions.get('enrollment_statuses', {})
        needs_enrollment = enrollment_statuses.get('needs_enrollment', {})
        needs_enrollment_enrollable = needs_enrollment.get('enrollable', [])

        if not needs_enrollment_enrollable:
            # Skip enrolling in default enterprise courses if there are no enrollable courses for which to enroll
            logger.info(
                "No default enterprise enrollment intentions courses for which to enroll "
                "for request user %s and enterprise customer %s",
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            return

        if not self.current_activated_license:
            # Skip enrolling in default enterprise courses if there is no activated license
            logger.info(
                "No activated license found for request user %s and enterprise customer %s. "
                "Skipping realization of default enterprise enrollment intentions.",
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
            )
            return

        license_uuids_by_course_run_key = {}
        for enrollment_intention in needs_enrollment_enrollable:
            subscription_plan = self.current_activated_license.get('subscription_plan', {})
            subscription_catalog = subscription_plan.get('enterprise_catalog_uuid')
            applicable_catalog_to_enrollment_intention = enrollment_intention.get(
                'applicable_enterprise_catalog_uuids'
            )
            if subscription_catalog in applicable_catalog_to_enrollment_intention:
                course_run_key = enrollment_intention['course_run_key']
                license_uuids_by_course_run_key[course_run_key] = self.current_activated_license['uuid']

        response_payload = self._request_default_enrollment_realizations(license_uuids_by_course_run_key)

        if failures := response_payload.get('failures'):
            # Log and add error if there are failures realizing default enrollments
            failures_str = json.dumps(failures)
            logger.error(
                'Default realization enrollment failures for request user %s and '
                'enterprise customer %s: %s',
                self.context.lms_user_id,
                self.context.enterprise_customer_uuid,
                failures_str,
            )
            self.add_error(
                user_message='There were failures realizing default enrollments',
                developer_message='Default realization enrollment failures: ' + failures_str,
            )

        if not self.context.data.get('default_enterprise_enrollment_realizations'):
            self.context.data['default_enterprise_enrollment_realizations'] = []

        if successful_enrollments := response_payload.get('successes', []):
            # Invalidate the default enterprise enrollment intentions and enterprise course enrollments cache
            # as the previously redeemable enrollment intentions have been processed/enrolled.
            self.invalidate_default_enrollment_intentions_cache()
            self.invalidate_enrollments_cache()

        for enrollment in successful_enrollments:
            course_run_key = enrollment.get('course_run_key')
            self.context.data['default_enterprise_enrollment_realizations'].append({
                'course_key': course_run_key,
                'enrollment_status': 'enrolled',
                'subscription_license_uuid': license_uuids_by_course_run_key.get(course_run_key),
            })

    def _request_default_enrollment_realizations(self, license_uuids_by_course_run_key):
        """
        Sends the request to bulk enroll into default enrollment intentions via the LMS
        API client.
        """
        bulk_enrollment_payload = []
        for course_run_key, license_uuid in license_uuids_by_course_run_key.items():
            bulk_enrollment_payload.append({
                'user_id': self.context.lms_user_id,
                'course_run_key': course_run_key,
                'license_uuid': license_uuid,
                'is_default_auto_enrollment': True,
            })

        try:
            response_payload = self.lms_api_client.bulk_enroll_enterprise_learners(
                self.context.enterprise_customer_uuid,
                bulk_enrollment_payload,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception('Error realizing default enterprise enrollment intentions')
            self.add_error(
                user_message='There was an exception realizing default enrollments',
                developer_message=f'Default realization enrollment exception: {exc}',
            )
            response_payload = {}

        return response_payload

    def invalidate_default_enrollment_intentions_cache(self):
        invalidate_default_enterprise_enrollment_intentions_learner_status_cache(
            enterprise_customer_uuid=self.context.enterprise_customer_uuid,
            lms_user_id=self.context.lms_user_id,
        )

    def invalidate_enrollments_cache(self):
        invalidate_enterprise_course_enrollments_cache(
            enterprise_customer_uuid=self.context.enterprise_customer_uuid,
            lms_user_id=self.context.lms_user_id,
        )


class DashboardHandler(LearnerDashboardDataMixin, BaseLearnerPortalHandler):
    """
    A handler class for processing the learner dashboard route.

    The `DashboardHandler` extends `BaseLearnerPortalHandler` to handle the loading and processing
    of data specific to the learner dashboard.
    """

    def _get_concurrent_tasks(self):
        """
        Add additional concurrent tasks for the dashboard.
        """
        tasks = super()._get_concurrent_tasks()
        tasks[self.CONCURRENCY_GROUPS.DEFAULT].extend([
            self.load_enterprise_course_enrollments,
        ])
        return tasks

    def load_enterprise_course_enrollments(self):
        """
        Temporary override to add delay.
        """
        time.sleep(MOCK_TASK_DELAY)
        # raise Exception('Failed to load enterprise course enrollments?!')
        return super().load_enterprise_course_enrollments()


class SearchHandler(BaseLearnerPortalHandler):
    """
    A handler class for processing the learner search route.

    Extends `BaseLearnerPortalHandler` to handle the loading and processing
    of data specific to the learner search.
    """


class AcademyHandler(BaseLearnerPortalHandler):
    """
    A handler class for processing the learner academy detail route.

    Extends `BaseLearnerPortalHandler` to handle the loading and processing
    of data specific to the learner academy detail route.
    """


class SkillsQuizHandler(BaseLearnerPortalHandler):
    """
    A handler class for processing the learner skills quiz route.

    Extends `BaseLearnerPortalHandler` to handle the loading and processing
    of data specific to the learner skills quiz route.
    """
