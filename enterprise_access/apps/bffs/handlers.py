""""
Handlers for bffs app.
"""

import logging

from enterprise_access.apps.api_client.license_manager_client import LicenseManagerUserApiClient
from enterprise_access.apps.api_client.lms_client import LmsUserApiClient
from enterprise_access.apps.bffs.context import HandlerContext
from enterprise_access.apps.bffs.mixins import BaseLearnerDataMixin
from enterprise_access.apps.bffs.serializers import EnterpriseCustomerUserSubsidiesSerializer

logger = logging.getLogger(__name__)


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

    def add_error(self, **kwargs):
        """
        Adds an error to the context.
        Output fields determined by the ErrorSerializer
        """
        self.context.add_error(**kwargs)

    def add_warning(self, **kwargs):
        """
        Adds an error to the context.
        Output fields determined by the WarningSerializer
        """
        self.context.add_warning(**kwargs)


class BaseLearnerPortalHandler(BaseHandler, BaseLearnerDataMixin):
    """
    A base handler class for learner-focused routes.

    The `BaseLearnerHandler` extends `BaseHandler` and provides shared core functionality
    across all learner-focused page routes, such as the learner dashboard, search, and course routes.
    """

    def __init__(self, context):
        """
         Initializes the BaseLearnerPortalHandler with a HandlerContext and API clients.
         Args:
             context (HandlerContext): The context object containing request information and data.
         """
        super().__init__(context)

        # API Clients
        self.license_manager_client = LicenseManagerUserApiClient(self.context.request)
        self.lms_user_api_client = LmsUserApiClient(self.context.request)

    def load_and_process(self):
        """
        Loads and processes data. This is a basic implementation that can be overridden by subclasses.

        The method in this class simply calls common learner logic to ensure the context is set up.
        """
        if not self.context.enterprise_customer:
            self.add_error(
                user_message="An error occurred while loading the learner portal handler.",
                developer_message="Enterprise customer not found in the context.",
            )
            return

        try:
            # Transform enterprise customer data
            self.transform_enterprise_customers()

            # Retrieve and process subscription licenses. Handles activation and auto-apply logic.
            self.load_and_process_subsidies()

            # Retrieve default enterprise courses and enroll in the redeemable ones
            self.load_default_enterprise_enrollment_intentions()
            self.enroll_in_redeemable_default_enterprise_enrollment_intentions()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error loading learner portal handler")
            self.add_error(
                user_message="An error occurred while loading and processing common learner logic.",
                developer_message=f"Error: {e}",
            )

    def transform_enterprise_customers(self):
        """
        Transform enterprise customer metadata retrieved by self.context.
        """
        for customer_record_key in ('enterprise_customer', 'active_enterprise_customer', 'staff_enterprise_customer'):
            if not (customer_record := getattr(self.context, customer_record_key, None)):
                continue
            self.context.data[customer_record_key] = self.transform_enterprise_customer(customer_record)

        if enterprise_customer_users := self.context.all_linked_enterprise_customer_users:
            self.context.data['all_linked_enterprise_customer_users'] = [
                self.transform_enterprise_customer_user(enterprise_customer_user)
                for enterprise_customer_user in enterprise_customer_users
            ]

    def load_and_process_subsidies(self):
        """
        Load and process subsidies for learners
        """
        empty_subsidies = {
            'subscriptions': {
                'customer_agreement': None,
            },
        }
        self.context.data['enterprise_customer_user_subsidies'] =\
            EnterpriseCustomerUserSubsidiesSerializer(empty_subsidies).data
        self.load_and_process_subscription_licenses()

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
        if not enterprise_customer or not enterprise_customer.get('enable_learner_portal', False):
            # If the enterprise customer does not exist or the learner portal is not enabled, return None
            return None

        # Learner Portal is enabled, so transform the enterprise customer data.
        identity_provider = enterprise_customer.get("identity_provider")
        disable_search = bool(
            not enterprise_customer.get("enable_integrated_customer_learner_portal_search", False) and
            identity_provider
        )
        show_integration_warning = bool(not disable_search and identity_provider)

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
            subscriptions_result = self.license_manager_client.get_subscription_licenses_for_learner(
                enterprise_customer_uuid=self.context.enterprise_customer_uuid,
                include_revoked=True,
                current_plans_only=False,
            )
            subscriptions_data = self.transform_subscriptions_result(subscriptions_result)
            self.context.data['enterprise_customer_user_subsidies'].update({
                'subscriptions': subscriptions_data,
            })
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error loading subscription licenses")
            self.add_error(
                user_message="An error occurred while loading subscription licenses.",
                developer_message=f"Error: {e}",
            )

    def transform_subscription_licenses(self, subscription_licenses):
        """
        Transform subscription licenses data if needed.
        """
        return [
            {
                'uuid': subscription_license.get('uuid'),
                'status': subscription_license.get('status'),
                'user_email': subscription_license.get('user_email'),
                'activation_date': subscription_license.get('activation_date'),
                'last_remind_date': subscription_license.get('last_remind_date'),
                'revoked_date': subscription_license.get('revoked_date'),
                'activation_key': subscription_license.get('activation_key'),
                'subscription_plan': subscription_license.get('subscription_plan', {}),
            }
            for subscription_license in subscription_licenses
        ]

    def transform_subscriptions_result(self, subscriptions_result):
        """
        Transform subscription licenses data if needed.
        """
        subscription_licenses = subscriptions_result.get('results', [])
        subscription_licenses_by_status = {}

        transformed_licenses = self.transform_subscription_licenses(subscription_licenses)

        for subscription_license in transformed_licenses:
            status = subscription_license.get('status')
            if status not in subscription_licenses_by_status:
                subscription_licenses_by_status[status] = []
            subscription_licenses_by_status[status].append(subscription_license)

        return {
            'customer_agreement': subscriptions_result.get('customer_agreement'),
            'subscription_licenses': transformed_licenses,
            'subscription_licenses_by_status': subscription_licenses_by_status,
        }

    @property
    def current_active_license(self):
        """
        Returns an activated license for the user iff the related subscription plan is current,
        otherwise returns None.
        """
        current_active_licenses = [
            _license for _license in self.subscription_licenses_by_status.get('activated', [])
            if _license['subscription_plan']['is_current']
        ]
        if current_active_licenses:
            return current_active_licenses[0]
        return None

    def process_subscription_licenses(self):
        """
        Process loaded subscription licenses, including performing side effects such as:
            * Checking if there is an activated license
            * Checking and activating assigned licenses
            * Checking and auto applying licenses

        This method is called after `load_subscription_licenses` to handle further actions based
        on the loaded data.
        """
        if not self.subscriptions or self.current_active_license:
            # Skip processing if:
            # - there is no subscriptions data
            # - user already has an activated license
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
        assigned_licenses = subscription_licenses_by_status.get('assigned', [])
        activated_licenses = []
        for subscription_license in assigned_licenses:
            activation_key = subscription_license.get('activation_key')
            if activation_key:
                try:
                    # Perform side effect: Activate the assigned license
                    activated_license = self.license_manager_client.activate_license(activation_key)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.exception(f"Error activating license {subscription_license.get('uuid')}")
                    self.add_error(
                        user_message="An error occurred while activating a subscription license.",
                        developer_message=f"License UUID: {subscription_license.get('uuid')}, Error: {e}",
                    )
                    return

                # Update the subscription_license data with the activation status and date; the activated license is not
                # returned from the API, so we need to manually update the license object we have available.
                transformed_activated_subscription_licenses = self.transform_subscription_licenses([activated_license])
                activated_licenses.append(transformed_activated_subscription_licenses[0])
            else:
                logger.error(f"Activation key not found for license {subscription_license.get('uuid')}")
                self.add_error(
                    user_message="An error occurred while activating a subscription license.",
                    developer_message=f"Activation key not found for license {subscription_license.get('uuid')}",
                )

        # Update the subscription_licenses_by_status data with the activated licenses
        updated_activated_licenses = subscription_licenses_by_status.get('activated', [])
        updated_activated_licenses.extend(activated_licenses)
        if updated_activated_licenses:
            subscription_licenses_by_status['activated'] = updated_activated_licenses

        activated_license_uuids = {license['uuid'] for license in activated_licenses}
        remaining_assigned_licenses = [
            subscription_license
            for subscription_license in assigned_licenses
            if subscription_license['uuid'] not in activated_license_uuids
        ]
        if remaining_assigned_licenses:
            subscription_licenses_by_status['assigned'] = remaining_assigned_licenses
        else:
            subscription_licenses_by_status.pop('assigned', None)

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

    def check_and_auto_apply_license(self):
        """
        Check if auto-apply licenses are available and apply them to the user.
        """
        subscription_licenses_by_status = self.subscription_licenses_by_status
        assigned_licenses = subscription_licenses_by_status.get('assigned', [])

        if assigned_licenses or self.current_active_license:
            # Skip auto-apply if user already has assigned license(s) or an already-activated license
            return

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
            auto_applied_license = self.license_manager_client.auto_apply_license(customer_agreement.get('uuid'))
            if auto_applied_license:
                # Update the context with the auto-applied license data
                transformed_auto_applied_licenses = self.transform_subscription_licenses([auto_applied_license])
                licenses = self.subscription_licenses + transformed_auto_applied_licenses
                subscription_licenses_by_status['activated'] = transformed_auto_applied_licenses
                self.context.data['enterprise_customer_user_subsidies']['subscriptions'].update({
                    'subscription_licenses': licenses,
                    'subscription_licenses_by_status': subscription_licenses_by_status,
                })
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error auto-applying license")
            self.add_error(
                user_message="An error occurred while auto-applying a license.",
                developer_message=f"Customer agreement UUID: {customer_agreement.get('uuid')}, Error: {e}",
            )

    def load_default_enterprise_enrollment_intentions(self):
        """
        Load default enterprise course enrollments (stubbed)
        """
        client = self.lms_user_api_client
        try:
            default_enrollment_intentions = client.get_default_enterprise_enrollment_intentions_learner_status(
                enterprise_customer_uuid=self.context.enterprise_customer_uuid,
            )
            self.context.data['default_enterprise_enrollment_intentions'] = default_enrollment_intentions
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error loading default enterprise courses")
            self.add_error(
                user_message="An error occurred while loading default enterprise courses.",
                developer_message=f"Error: {e}",
            )

    def enroll_in_redeemable_default_enterprise_enrollment_intentions(self):
        """
        Enroll in redeemable courses.
        """
        needs_enrollment = self.default_enterprise_enrollment_intentions.get('needs_enrollment', {})
        needs_enrollment_enrollable = needs_enrollment.get('enrollable', [])

        activated_subscription_licenses = self.subscription_licenses_by_status.get('activated', [])

        if not (needs_enrollment_enrollable or activated_subscription_licenses):
            # Skip enrollment if there are no:
            # - default enterprise enrollment intentions that should be enrolled OR
            # - activated subscription licenses
            return

        redeemable_default_courses = []
        for enrollment_intention in needs_enrollment_enrollable:
            for subscription_license in activated_subscription_licenses:
                subscription_plan = subscription_license.get('subscription_plan', {})
                subscription_catalog = subscription_plan.get('enterprise_catalog_uuid')
                applicable_catalog_to_enrollment_intention = enrollment_intention.get(
                    'applicable_enterprise_catalog_uuids'
                )
                if subscription_catalog in applicable_catalog_to_enrollment_intention:
                    redeemable_default_courses.append((enrollment_intention, subscription_license))
                    break

        for redeemable_course, subscription_license in redeemable_default_courses:
            # TODO: enroll in redeemable courses (stubbed)
            if not self.context.data.get('default_enterprise_enrollment_realizations'):
                self.context.data['default_enterprise_enrollment_realizations'] = []

            self.context.data['default_enterprise_enrollment_realizations'].append({
                'course_key': redeemable_course.get('key'),
                'enrollment_status': 'enrolled',
                'subscription_license_uuid': subscription_license.get('uuid'),
            })


class DashboardHandler(BaseLearnerPortalHandler):
    """
    A handler class for processing the learner dashboard route.

    The `DashboardHandler` extends `BaseLearnerPortalHandler` to handle the loading and processing
    of data specific to the learner dashboard.
    """

    def load_and_process(self):
        """
        Loads and processes data for the learner dashboard route.

        This method overrides the `load_and_process` method in `BaseLearnerPortalHandler`.
        """
        super().load_and_process()

        try:
            # Load data specific to the dashboard route
            self.load_enterprise_course_enrollments()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error retrieving enterprise_course_enrollments")
            self.add_error(
                user_message="An error occurred while processing the learner dashboard.",
                developer_message=f"Error: {e}",
            )

    def load_enterprise_course_enrollments(self):
        """
        Loads enterprise course enrollments data.

        Returns:
            list: A list of enterprise course enrollments.
        """
        try:
            enterprise_course_enrollments = self.lms_user_api_client.get_enterprise_course_enrollments(
                enterprise_customer_uuid=self.context.enterprise_customer_uuid,
                is_active=True,
            )
            self.context.data['enterprise_course_enrollments'] = enterprise_course_enrollments
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error retrieving enterprise course enrollments")
            self.add_error(
                user_message="An error occurred while retrieving enterprise course enrollments.",
                developer_message=f"Error: {e}",
            )
