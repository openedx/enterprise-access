""""
Handlers for bffs app.
"""

import logging

from enterprise_access.apps.api_client.license_manager_client import LicenseManagerUserApiClient
from enterprise_access.apps.bffs.context import HandlerContext

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


class BaseLearnerPortalHandler(BaseHandler):
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
        self.license_manager_client = LicenseManagerUserApiClient(self.context.request)

    def load_and_process(self):
        """
        Loads and processes data. This is a basic implementation that can be overridden by subclasses.

        The method in this class simply calls common learner logic to ensure the context is set up.
        """
        try:
            # Retrieve and process subscription licenses. Handles activation and auto-apply logic.
            # TODO: retrieve enterprise customer metadata,ENT-9629
            # self.load_enterprise_customer()
            self.load_and_process_subscription_licenses()

            # Retrieve default enterprise courses and enroll in the redeemable ones
            self.load_default_enterprise_courses()
            self.enroll_in_redeemable_default_courses()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error loading learner portal handler")
            self.add_error(
                user_message="An error occurred while loading and processing common learner logic.",
                developer_message=f"Error: {e}",
            )

    def load_subscription_licenses(self):
        """
        Load subscription licenses for the learner.
        """
        subscriptions_result = self.license_manager_client.get_subscription_licenses_for_learner(
            enterprise_customer_uuid=self.context.enterprise_customer_uuid
        )
        self.transform_subscriptions_result(subscriptions_result)

    def get_subscription_licenses(self):
        """
        Get subscription licenses.
        """
        return self.context.data['subscriptions'].get('subscription_licenses', [])

    def get_subscription_licenses_by_status(self):
        """
        Get subscription licenses by status.
        """
        return self.context.data['subscriptions'].get('subscription_licenses_by_status', {})

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

        subscriptions_data = {
            'customer_agreement': subscriptions_result.get('customer_agreement', {}),
            'subscription_licenses': transformed_licenses,
            'subscription_licenses_by_status': subscription_licenses_by_status,
        }
        self.context.data['subscriptions'] = subscriptions_data

    def check_has_activated_license(self):
        """
        Check if the user has an activated license.

        Returns:
            bool: True if the user has an activated license, False otherwise.
        """
        subscription_licenses_by_status = self.get_subscription_licenses_by_status()
        return bool(subscription_licenses_by_status.get('activated'))

    def process_subscription_licenses(self):
        """
        Process loaded subscription licenses, including performing side effects such as:
            * Checking if there is an activated license
            * Checking and activating assigned licenses
            * Checking and auto applying licenses

        This method is called after `load_subscription_licenses` to handle further actions based
        on the loaded data.

        If the `subscriptions` field does not exist, raises a KeyError
        """
        if not self.context.data['subscriptions']:
            raise KeyError("Unable to retrieve subscriptions.")

        # Check if user already has 'activated' license(s). If so, no further action is needed.
        if self.check_has_activated_license():
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
        subscription_licenses = self.get_subscription_licenses()
        subscription_licenses_by_status = self.get_subscription_licenses_by_status()
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
                transformed_activated_subscription_license = self.transform_subscription_licenses(activated_license)
                activated_licenses.append(transformed_activated_subscription_license)
            else:
                logger.error(f"Activation key not found for license {subscription_license.get('uuid')}")
                self.add_error(
                    user_message="An error occurred while activating a subscription license.",
                    developer_message=f"Activation key not found for license {subscription_license.get('uuid')}",
                )

        # Update the subscriptions.subscription_licenses_by_status context with the modified licenses data
        updated_activated_licenses = subscription_licenses_by_status.get('activated', [])
        updated_activated_licenses.extend(activated_licenses)
        subscription_licenses_by_status['activated'] = updated_activated_licenses
        remaining_assigned_licenses = [
            subscription_license
            for subscription_license in assigned_licenses
            if subscription_license not in activated_licenses
        ]
        if remaining_assigned_licenses:
            subscription_licenses_by_status['assigned'] = remaining_assigned_licenses
        else:
            subscription_licenses_by_status.pop('assigned', None)
        self.context.data['subscriptions']['subscription_licenses_by_status'] = subscription_licenses_by_status

        # Update the subscriptions.subscription_licenses context with the modified licenses data
        updated_subscription_licenses = []
        for subscription_license in subscription_licenses:
            for activated_license in activated_licenses:
                if subscription_license.get('uuid') == activated_license.get('uuid'):
                    updated_subscription_licenses.append(activated_license)
                    break
                updated_subscription_licenses.append(subscription_license)
        self.context.data['subscriptions']['subscription_licenses'] = updated_subscription_licenses

    def check_and_auto_apply_license(self):
        """
        Check if auto-apply licenses are available and apply them to the user.
        """
        subscription_licenses_by_status = self.get_subscription_licenses_by_status()
        has_assigned_licenses = subscription_licenses_by_status.get('assigned', [])
        if has_assigned_licenses or self.check_has_activated_license():
            # Skip auto-apply if user already has an activated license or assigned licenses
            return

        customer_agreement = self.context.data['subscriptions'].get('customer_agreement', {})
        has_subscription_plan_for_auto_apply = (
            bool(customer_agreement.get('subscription_for_auto_applied_licenses')) and
            customer_agreement.get('net_days_until_expiration') > 0
        )
        idp_or_univeral_link_enabled = (
            # TODO: IDP from customer, ENT-9629
            customer_agreement.get('enable_auto_applied_subscriptions_with_universal_link')
        )
        is_eligible_for_auto_apply = has_subscription_plan_for_auto_apply and idp_or_univeral_link_enabled
        if not is_eligible_for_auto_apply:
            # Skip auto-apply if the customer agreement does not have a subscription plan for auto-apply
            return

        try:
            # Perform side effect: Auto-apply license
            auto_applied_license = self.license_manager_client.auto_apply_license(customer_agreement.get('uuid'))
            if auto_applied_license:
                # Update the context with the auto-applied license data
                subscription_licenses_by_status['activated'] =\
                    self.transform_subscription_licenses([auto_applied_license])
                self.context.data['subscriptions']['subscription_licenses_by_status'] = subscription_licenses_by_status
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error auto-applying license")
            self.add_error(
                user_message="An error occurred while auto-applying a license.",
                developer_message=f"Customer agreement UUID: {customer_agreement.get('uuid')}, Error: {e}",
            )

    def load_default_enterprise_courses(self):
        """
        Load default enterprise course enrollments (stubbed)
        """
        mock_catalog_uuid = 'f09ff39b-f456-4a03-b53b-44cd70f52108'

        self.context.data['default_enterprise_courses'] = [
            {
                'current_course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'applicable_catalog_uuids': [mock_catalog_uuid],
            },
            {
                'current_course_run_key': 'course-v1:edX+SampleX+Sample_Course',
                'applicable_catalog_uuids': [mock_catalog_uuid],
            },
        ]

    def enroll_in_redeemable_default_courses(self):
        """
        Enroll in redeemable courses.
        """
        default_enterprise_courses = self.context.data.get('default_enterprise_courses', [])
        activated_subscription_licenses = self.get_subscription_licenses_by_status().get('activated', [])

        if not (default_enterprise_courses or activated_subscription_licenses):
            # Skip enrollment if there are no default enterprise courses or activated subscription licenses
            return

        redeemable_default_courses = []
        for course in default_enterprise_courses:
            for subscription_license in activated_subscription_licenses:
                subscription_plan = subscription_license.get('subscription_plan', {})
                if subscription_plan.get('enterprise_catalog_uuid') in course.get('applicable_catalog_uuids'):
                    redeemable_default_courses.append((course, subscription_license))
                    break

        for redeemable_course, subscription_license in redeemable_default_courses:
            # Enroll in redeemable courses (stubbed)
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
        # Call the common learner logic from the base class
        try:
            # Load data specific to the dashboard route
            self.context.data['enterprise_course_enrollments'] = self.get_enterprise_course_enrollments()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error retrieving enterprise_course_enrollments")
            self.add_error(
                user_message="An error occurred while processing the learner dashboard.",
                developer_message=f"Error: {e}",
            )

    def get_enterprise_course_enrollments(self):
        """
        Loads enterprise course enrollments data.

        Returns:
            list: A list of enterprise course enrollments.
        """
        # Placeholder logic for loading enterprise course enrollments data
        return [
            {
                "certificate_download_url": None,
                "emails_enabled": False,
                "course_run_id": "course-v1:BabsonX+MIS01x+1T2019",
                "course_run_status": "in_progress",
                "created": "2023-09-29T14:24:45.409031+00:00",
                "start_date": "2019-03-19T10:00:00Z",
                "end_date": "2024-12-31T04:30:00Z",
                "display_name": "AI for Leaders",
                "course_run_url": "https://learning.edx.org/course/course-v1:BabsonX+MIS01x+1T2019/home",
                "due_dates": [],
                "pacing": "self",
                "org_name": "BabsonX",
                "is_revoked": False,
                "is_enrollment_active": True,
                "mode": "verified",
                "resume_course_run_url": None,
                "course_key": "BabsonX+MIS01x",
                "course_type": "verified-audit",
                "product_source": "edx",
                "enroll_by": "2024-12-21T23:59:59Z",
            }
        ]
