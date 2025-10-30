"""
API client for calls to the license-manager service.
"""
import logging

import requests
from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient
from enterprise_access.apps.api_client.base_user import BaseUserApiClient
from enterprise_access.apps.api_client.exceptions import APIClientException, safe_error_response_content

logger = logging.getLogger(__name__)


NEW_SUBSCRIPTION_CHANGE_REASON = 'new'


class LicenseManagerApiClient(BaseOAuthClient):
    """
    API client for calls to the license-manager service.
    """
    api_base_url = settings.LICENSE_MANAGER_URL + '/api/v1/'
    subscriptions_endpoint = api_base_url + 'subscriptions/'
    admin_license_view_endpoint = api_base_url + 'admin-license-view/'
    customer_agreement_endpoint = api_base_url + 'customer-agreement/'
    customer_agreement_provisioning_endpoint = api_base_url + 'provisioning-admins/customer-agreement/'
    subscription_provisioning_endpoint = api_base_url + 'provisioning-admins/subscriptions/'

    def get_subscription_overview(self, subscription_uuid):
        """
        Call license-manager API for data about a SubscriptionPlan.

        Arguments:
            subscription_uuid (UUID): UUID of the SubscriptionPlan in license-manager
        Returns:
            dict: Dictionary represention of json returned from API

        Example response:
        [
            { "status": "assigned", "count": 5 },
            { "status": "activated", "count": 20 },
        ]
        """
        try:
            endpoint = self.subscriptions_endpoint + str(subscription_uuid) + '/licenses/overview'
            response = self.client.get(endpoint, timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT)
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise APIClientException(
                f'Could not fetch subscription data for {subscription_uuid}',
                exc,
            ) from exc

    def get_learner_subscription_licenses_for_admin(
        self,
        enterprise_customer_uuid,
        user_email,
        traverse_pagination=False
    ):
        """
        Get licenses for a learner with the provided learner's email address and enterprise customer uuid.

        Arguments:
            user_email (string): The email address for a learner within an enterprise
            enterprise_customer_uuid (string): the uuid of an enterprise customer
        Returns:
            dict: Dictionary representation of json returned from API
        """
        query_params = {
            'enterprise_customer_uuid': enterprise_customer_uuid,
            'user_email': user_email
        }
        results = []
        current_response = None
        next_url = self.admin_license_view_endpoint
        try:
            while next_url:
                current_response = self.client.get(
                    next_url,
                    params=query_params,
                    timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT
                )
                current_response.raise_for_status()
                data = current_response.json()
                results.extend(data.get('results', []))

                next_url = data.get('next') if traverse_pagination else None
            return results
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                f"Failed to get learner subscription licenses for admin : {safe_error_response_content(exc)}"
            )
            raise

    def assign_licenses(self, user_emails, subscription_uuid):
        """
        Given a list of emails, assign each email a license under the given subscription.

        Arguments:
            user_emails (list of str): Emails to assign licenses to
        """

        try:
            endpoint = f'{self.subscriptions_endpoint}{subscription_uuid}/licenses/assign/'
            payload = {
                'user_emails': user_emails,
                # Skip license assignment email since we have a request approved email
                'notify_users': False
            }
            response = self.client.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise

    def get_customer_agreement(self, customer_uuid):
        """
        Fetches the first customer agreement for the customer with the given uuid,
        returns None if no such agreements exist. The resulting dictionary also contains
        a list of all active subscription plans for the agreement in the "subscriptions" key.
        """
        endpoint = self.customer_agreement_endpoint + f'?enterprise_customer_uuid={customer_uuid}'
        response = self.client.get(endpoint)

        try:
            response.raise_for_status()
            results = response.json().get('results', [])
            if not results:
                return None
            return results[0]
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                'Error fetching customer agreements for customer id %s, response: %s, exc: %s',
                customer_uuid, safe_error_response_content(exc), exc,
            )
            raise APIClientException(
                f'Could not fetch customer agreement for customer {customer_uuid}',
                exc,
            ) from exc

    def create_customer_agreement(self, customer_uuid, customer_slug, default_catalog_uuid=None, **kwargs):
        """
        Creates a Customer Agreement record for the provided customer.

        Other allowed kwargs:
          disable_expiration_notifications (boolean)
          enable_auto_applied_subscriptions_with_universal_link (boolean)
        """
        endpoint = self.customer_agreement_provisioning_endpoint
        payload = {
            'enterprise_customer_uuid': str(customer_uuid),
            'enterprise_customer_slug': customer_slug,
            'default_enterprise_catalog_uuid': str(default_catalog_uuid) if default_catalog_uuid else None,
        }
        payload.update(kwargs)
        response = self.client.post(endpoint, json=payload)
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                'Error creating customer agreement for customer id %s, response: %s, exc: %s',
                customer_uuid, safe_error_response_content(exc), exc
            )
            raise APIClientException(
                f'Could not create customer agreement for customer {customer_uuid}',
                exc,
            ) from exc

    def create_subscription_plan(
        self, customer_agreement_uuid, salesforce_opportunity_line_item, title,
        start_date, expiration_date, desired_num_licenses, enterprise_catalog_uuid=None, product_id=None,
        **kwargs,
    ):
        """
        Creates a Subscription Plan associated with the provided customer agreement.
        """
        endpoint = self.subscription_provisioning_endpoint
        payload = {
            'customer_agreement': str(customer_agreement_uuid),
            'salesforce_opportunity_line_item': salesforce_opportunity_line_item,
            'title': title,
            'start_date': start_date,
            'expiration_date': expiration_date,
            'desired_num_licenses': desired_num_licenses,
            'change_reason': NEW_SUBSCRIPTION_CHANGE_REASON,
            'for_internal_use_only': settings.PROVISIONING_DEFAULTS['subscription']['for_internal_use_only'],
            'product': product_id or settings.PROVISIONING_DEFAULTS['subscription']['product_id'],
            'is_active': settings.PROVISIONING_DEFAULTS['subscription']['is_active'],
        }

        payload.update(kwargs)
        if enterprise_catalog_uuid:
            payload['enterprise_catalog_uuid'] = str(enterprise_catalog_uuid)

        response = self.client.post(endpoint, json=payload)
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                'Failed to create subscription plan, response %s, exception: %s',
                safe_error_response_content(exc),
                exc,
            )
            raise APIClientException(
                f'Could not create subscription plan for customer agreement {customer_agreement_uuid}',
                exc,
            ) from exc

    def update_subscription_plan(self, subscription_uuid, salesforce_opportunity_line_item):
        """
        Update a SubscriptionPlan's Salesforce Opportunity Line Item.

        Arguments:
            subscription_uuid (str): UUID of the SubscriptionPlan to update
            salesforce_opportunity_line_item (str): Salesforce OLI to associate with the plan

        Returns:
            dict: Updated subscription plan data from the API

        Raises:
            APIClientException: If the API call fails
        """
        endpoint = f"{self.api_base_url}subscription-plans/{subscription_uuid}/"
        payload = {
            'salesforce_opportunity_line_item': salesforce_opportunity_line_item
        }

        try:
            response = self.client.patch(
                endpoint,
                json=payload,
                timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                'Failed to update subscription plan %s with OLI %s, response %s, exception: %s',
                subscription_uuid,
                salesforce_opportunity_line_item,
                safe_error_response_content(exc),
                exc,
            )
            raise APIClientException(
                f'Could not update subscription plan {subscription_uuid}',
                exc,
            ) from exc


class LicenseManagerUserApiClient(BaseUserApiClient):
    """
    API client for calls to the license-manager service. This client is used for user-specific calls,
    passing the original Authorization header from the originating request.
    """

    api_base_url = f"{settings.LICENSE_MANAGER_URL}/api/v1/"
    learner_licenses_endpoint = f"{api_base_url}learner-licenses/"
    license_activation_endpoint = f"{api_base_url}license-activation/"

    def auto_apply_license_endpoint(self, customer_agreement_uuid):
        return f"{self.api_base_url}customer-agreement/{customer_agreement_uuid}/auto-apply/"

    def get_subscription_licenses_for_learner(self, enterprise_customer_uuid, **kwargs):
        """
        Get subscription licenses for a learner.

        Arguments:
            enterprise_customer_uuid (str): UUID of the enterprise customer
        Returns:
            dict: Dictionary representation of json returned from API
        """
        query_params = {
            'enterprise_customer_uuid': enterprise_customer_uuid,
            'page_size': 100,
            **kwargs,
        }
        url = self.learner_licenses_endpoint
        try:
            response = self.get(url, params=query_params, timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(f"Failed to get subscription licenses for learner: {exc}")
            raise

    def activate_license(self, activation_key):
        """
        Activate a license.

        Arguments:
            license_uuid (str): UUID of the license to activate
        """
        try:
            url = self.license_activation_endpoint
            query_params = {
                'activation_key': activation_key,
            }
            response = self.post(url, params=query_params, timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(f"Failed to activate license: {exc}")
            raise

    def auto_apply_license(self, customer_agreement_uuid):
        """
        Activate a license.

        Arguments:
            license_uuid (str): UUID of the license to activate
        """
        try:
            url = self.auto_apply_license_endpoint(customer_agreement_uuid=customer_agreement_uuid)
            response = self.post(url, timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(f"Failed to auto-apply license: {exc}")
            raise
