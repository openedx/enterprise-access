"""
API client for calls to the ecommerce service.
"""
import logging

import requests
from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient

logger = logging.getLogger(__name__)

class EcommerceApiClient(BaseOAuthClient):
    """
    API client for calls to the ecommerce service.
    """
    api_base_url = settings.ECOMMERCE_URL + '/api/v2/'
    enterprise_coupons_endpoint = api_base_url + 'enterprise/coupons/'

    def get_coupon_overview(self, enterprise_uuid, coupon_id):
        """
        Call ecommerce API overview endpoint for data about a coupon.

        The overview endpoint rolls up the total assignments remaining which
        is very convenient for us.

        Arguments:
            enterprise_uuid (uuid): enterprise customer identifier
            coupon_id (int): identifier for the coupon in ecommerce
        Returns:
            dict: Dictionary represention of json returned from API

        example response:
        {
          "id":123,
          "title": "Test coupon",
          "start_date":"2022-01-06T00:00:00Z",
          "end_date":"2023-05-31T00:00:00Z",
          "num_uses":0,
          "usage_limitation":"Multi-use",
          "num_codes":100,
          "max_uses":200,
          "num_unassigned":191,
          "errors":[],
          "available":true
        }
        """
        try:
            query_params = {'coupon_id': coupon_id}
            endpoint = self.enterprise_coupons_endpoint + str(enterprise_uuid) + '/overview/'
            response = self.client.get(endpoint, params=query_params, timeout=settings.ECOMMERCE_CLIENT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise

    def assign_coupon_codes(self, user_emails, coupon_id):
        """
        Given a list of emails, assign each email a coupon code under the given coupon.

        Arguments:
            user_emails (list of str): Emails to assign coupon codes to
        """

        try:
            endpoint = f'{self.enterprise_coupons_endpoint}{coupon_id}/assign/'
            payload = {
                'users': [{ 'email': email } for email in user_emails],
                # Skip code assignment email since we have a request approved email
                'notify_learners': False
            }
            response = self.client.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise
