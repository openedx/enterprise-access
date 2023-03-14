"""
Client to communicate with enterprise subside service through REST API.
"""
import logging
from urllib.parse import urljoin

import requests
from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient

logger = logging.getLogger(__name__)


class EnterpriseSubsidyApiClient(BaseOAuthClient):
    """
    API client for calls to the enterprise subsidy service.
    """
    enterprise_subsidy_api_base_url = urljoin(settings.ENTERPRISE_SUBSIDY_URL, 'api/v1')
    subsidies_endpoint = f'{enterprise_subsidy_api_base_url}/subsidies'
    transactions_endpoint = f'{enterprise_subsidy_api_base_url}/transactions'

    def get_subsidies(self, filters: dict = None):
        """
        Gets the data for all subsidies filtered by given criteria.

        Arguments:
            filters (dict): Query string parameters in thr form of a dict.

        Returns:
            response (dict): Dictionary containing subsidy data
        """
        try:
            response = self.client.get(
                self.subsidies_endpoint, timeout=settings.SUBSIDY_CLIENT_TIMEOUT, params=filters
            )
        except requests.exceptions.HTTPError as error:
            logger.exception(error)
            raise error

        return response.json()

    def get_transactions(self, filters: dict = None):
        """
        Gets the data for all transactions filtered by given criteria.

        Arguments:
            filters (dict): Query string parameters in thr form of a dict.

        Returns:
            response (dict): Dictionary containing subsidy data
        """
        try:
            response = self.client.get(
                self.transactions_endpoint, timeout=settings.SUBSIDY_CLIENT_TIMEOUT, params=filters
            )
        except requests.exceptions.HTTPError as error:
            logger.exception(error)
            raise error

        return response.json()

    def get_subsidy_data(self, subsidy_uuid: str):
        """
        Gets the data for a subsidy for the given subsidy_uuid.

        Arguments:
            subsidy_uuid (string): UUID of the subsidy.

        Returns:
            response (dict): Dictionary containing subsidy data
        """

        try:
            endpoint = f'{self.subsidies_endpoint}/{subsidy_uuid}/'
            response = self.client.get(endpoint, timeout=settings.SUBSIDY_CLIENT_TIMEOUT)
        except requests.exceptions.HTTPError as error:
            logger.exception(error)
            raise error

        return response.json()

    def get_transaction_data(self, transaction_uuid: str):
        """
        Gets the data for a transaction for the given transaction_uuid.

        Arguments:
            transaction_uuid (string): UUID of the transaction.

        Returns:
            response (dict): Dictionary containing transaction data
        """

        try:
            endpoint = f'{self.transactions_endpoint}/{transaction_uuid}/'
            response = self.client.get(endpoint, timeout=settings.SUBSIDY_CLIENT_TIMEOUT)
        except requests.exceptions.HTTPError as error:
            logger.exception(error)
            raise error

        return response.json()

    def create_transaction(self, payload: dict):
        """
        Create a transaction record on enterprise subsidy service.

        Arguments:
            payload (dict): A dictionary containing payload data for transaction.

        Returns:
            response (dict): Dictionary containing transaction data
        """

        try:
            endpoint = f'{self.transactions_endpoint}/'
            response = self.client.post(endpoint, payload, timeout=settings.SUBSIDY_CLIENT_TIMEOUT)
        except requests.exceptions.HTTPError as error:
            logger.exception(error)
            raise error

        return response.json()
