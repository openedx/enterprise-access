"""
API client for calls to the LMS.
"""
import logging
import os

import requests
from django.conf import settings
from rest_framework import status

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient
from enterprise_access.apps.api_client.exceptions import FetchGroupMembersConflictingParamsException
from enterprise_access.utils import should_send_email_to_pecu

logger = logging.getLogger(__name__)


class LmsApiClient(BaseOAuthClient):
    """
    API client for calls to the LMS service.
    """
    enterprise_api_base_url = settings.LMS_URL + '/enterprise/api/v1/'
    enterprise_learner_endpoint = enterprise_api_base_url + 'enterprise-learner/'
    enterprise_customer_endpoint = enterprise_api_base_url + 'enterprise-customer/'
    pending_enterprise_learner_endpoint = enterprise_api_base_url + 'pending-enterprise-learner/'
    enterprise_group_membership_endpoint = enterprise_api_base_url + 'enterprise-group/'

    def enterprise_group_endpoint(self, group_uuid):
        return os.path.join(
            self.enterprise_api_base_url + 'enterprise-group/',
            f"{group_uuid}/",
        )

    def enterprise_group_members_endpoint(self, group_uuid):
        return os.path.join(
            self.enterprise_group_endpoint(group_uuid),
            "learners/",
        )

    def get_enterprise_customer_data(self, enterprise_customer_uuid):
        """
        Gets the data for an EnterpriseCustomer for the given uuid.

        Arguments:
            enterprise_customer_uuid (string): id of the enterprise customer
        Returns:
            dictionary containing enterprise customer metadata
        """

        try:
            endpoint = f'{self.enterprise_customer_endpoint}{enterprise_customer_uuid}/'
            response = self.client.get(endpoint, timeout=settings.LMS_CLIENT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise

    def get_enterprise_admin_users(self, enterprise_customer_uuid):
        """
        Gets a list of admin users for a given enterprise customer.
        Arguments:
            enterprise_customer_uuid (UUID): UUID of the enterprise customer associated with an enterprise
        Returns:
            A list of dicts in the form of
                {
                    'id': str,
                    'username': str,
                    'first_name': str,
                    'last_name': str,
                    'email': str,
                    'is_staff': bool,
                    'is_active': bool,
                    'date_joined': str,
                    'ecu_id': str,
                    'created': str
                }
        """

        query_params = f'?enterprise_customer_uuid={str(enterprise_customer_uuid)}&role=enterprise_admin'

        try:
            url = self.enterprise_learner_endpoint + query_params
            results = []

            while url:
                response = self.client.get(url, timeout=settings.LMS_CLIENT_TIMEOUT)
                response.raise_for_status()
                resp_json = response.json()
                url = resp_json['next']

                for result in resp_json['results']:
                    user_data = result['user']
                    user_data.update(ecu_id=result['id'], created=result['created'])
                    results.append(user_data)

        except requests.exceptions.HTTPError as exc:
            logger.error(
                'Failed to fetch enterprise admin users for %r because %r',
                enterprise_customer_uuid,
                response.text,
            )
            raise exc

        return results

    def unlink_users_from_enterprise(self, enterprise_customer_uuid, user_emails, is_relinkable=True):
        """
        Unlink users with the given emails from the enterprise.

        Arguments:
            enterprise_customer_uuid (str): id of the enterprise customer
            emails (list of str): emails of the users to remove

        Returns:
            None
        """

        try:
            endpoint = f'{self.enterprise_customer_endpoint}{enterprise_customer_uuid}/unlink_users/'
            payload = {
                "user_emails": user_emails,
                "is_relinkable": is_relinkable
            }
            response = self.client.post(endpoint, payload)
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            msg = 'Failed to unlink users from %s.'
            logger.exception(msg, enterprise_customer_uuid)
            raise

    def fetch_group_members(
        self,
        group_uuid,
        sort_by=None,
        user_query=None,
        fetch_removed=False,
        is_reversed=False,
        traverse_pagination=False,
        page=1,
    ):
        """
        Fetches enterprise group member records from edx-platform.

        Params:
            - ``group_uuid`` (string, UUID): The group record PK to fetch members from.
            - ``sort_by`` (string, optional): Specify how the returned members should be ordered. Supported sorting
            values
            are `member_details`, `member_status`, and `recent_action`.
            - ``user_query`` (string, optional): Filter the returned members by user email with a provided sub-string.
            - ``fetch_removed`` (bool, optional): Include removed membership records.
            - ``is_reversed`` (bool, optional): Reverse the order of the returned members.
            - ``traverse_pagination`` (bool, optional): Indicates that the lms client should traverse and fetch all
            pages.
            of data. Cannot be supplied if ``page`` is supplied.
            - ``page`` (int, optional): Which page of paginated data to return. Cannot be supplied if
            ``traverse_pagination`` is supplied.
        """
        if traverse_pagination and page:
            raise FetchGroupMembersConflictingParamsException(
                'Params `traverse_pagination` and `page` are in conflict, only supply one or the other'
            )

        group_members_url = self.enterprise_group_members_endpoint(group_uuid)
        params = {
            "sort_by": sort_by,
            "user_query": user_query,
            "fetch_removed": fetch_removed,
            "page": page,
        }
        if is_reversed:
            params['is_reversed'] = is_reversed

        response = self.client.get(group_members_url, params=params)
        response.raise_for_status()
        response_json = response.json()
        results = response_json.get('results', [])
        if traverse_pagination:
            next_page = response.json().get("next")
            while next_page:
                response = self.client.get(next_page)
                response.raise_for_status()
                response_json = response.json()
                next_page = response_json.get('next')
                results.extend(response_json.get('results', []))

            response_json['results'] = results
            response_json['next'] = None
            response_json['previous'] = None
        return response_json

    def get_enterprise_user(self, enterprise_customer_uuid, learner_id):
        """
        Verify if `learner_id` is a part of an enterprise represented by `enterprise_customer_uuid`.

        Arguments:
            enterprise_customer_uuid (UUID): UUID of the enterprise customer.
            learner_id (int): LMS user id of a learner.

        Returns:
            None or the enterprise customer user record
        """
        ec_uuid = str(enterprise_customer_uuid)
        query_params = {'enterprise_customer_uuid': ec_uuid, 'user_ids': learner_id}

        try:
            url = self.enterprise_learner_endpoint
            response = self.client.get(url, params=query_params, timeout=settings.LMS_CLIENT_TIMEOUT)
            response.raise_for_status()
            json_response = response.json()
            results = json_response.get('results', [])
            if isinstance(results, list):
                for result in results:
                    returned_customer = result.get('enterprise_customer', {})
                    returned_user = result.get('user', {})
                    if returned_customer.get('uuid') == ec_uuid and returned_user.get('id') == learner_id:
                        return result
            else:
                logger.exception(f'get_enterprise_user got unexpected results: {results} from {url} ')
        except requests.exceptions.HTTPError:
            logger.exception('Failed to fetch data from LMS. URL: [%s].', url)
        except KeyError:
            logger.exception('Incorrect data received from LMS. [%s]', url)

        return None

    def create_pending_enterprise_users(self, enterprise_customer_uuid, user_emails):
        """
        Creates a pending enterprise user in the given ``enterprise_customer_uuid`` for each of the
        specified ``user_emails``.

        Args:
            enterprise_customer_uuid (UUID): UUID of the enterprise customer in which pending user records are created.
            user_emails (list(str)): The emails for which pending enterprise users will be created.

        Returns:
            A ``requests.Response`` object representing the pending-enterprise-learner endpoint response. HTTP status
            codes include:
                * 201 CREATED: Any pending enterprise users were created.
                * 204 NO CONTENT: No pending enterprise users were created (they ALL existed already).

        Raises:
            ``requests.exceptions.HTTPError`` on any endpoint response with an unsuccessful status code.
        """
        data = [
            {
                'enterprise_customer': str(enterprise_customer_uuid),
                'user_email': user_email,
            }
            for user_email in user_emails
        ]
        response = self.client.post(self.pending_enterprise_learner_endpoint, json=data)
        try:
            response.raise_for_status()
            if response.status_code == status.HTTP_201_CREATED:
                logger.info(
                    'Successfully created PendingEnterpriseCustomerUser records for customer %r',
                    enterprise_customer_uuid,
                )
            else:
                logger.info(
                    'Found existing PendingEnterpriseCustomerUser records for customer %r',
                    enterprise_customer_uuid,
                )
            return response
        except requests.exceptions.HTTPError as exc:
            logger.error(
                'Failed to create %r PendingEnterpriseCustomerUser records for customer %r because %r',
                len(data),
                enterprise_customer_uuid,
                response.text,
            )
            raise exc

    def get_pending_enterprise_group_memberships(self, enterprise_group_uuid):
        """
        Gets pending enterprise group memberships

        Arguments:
            enterprise_group_uuid (str): uuid of the enterprise group uuid

        Returns:
            A list of dicts of pecus in the form of that reminder emails should
            be sent to:
                {
                    'enterprise_customer_user_id': integer,
                    'lms_user_id': integer,
                    'pending_enterprise_customer_user_id': integer,
                    'enterprise_group_membership_uuid': UUID,
                    'member_details': {
                      'user_email': string,
                    },
                    'recent_action': string,
                }
        """
        try:
            url = f'{self.enterprise_group_membership_endpoint}' + (
                f'{enterprise_group_uuid}/learners/?pending_users_only=true')
            results = []

            while url:
                response = self.client.get(url, timeout=settings.LMS_CLIENT_TIMEOUT)
                response.raise_for_status()
                resp_json = response.json()
                url = resp_json.get('next')
                for result in resp_json['results']:
                    pending_learner_id = result['pending_enterprise_customer_user_id']
                    recent_action = result['recent_action']
                    user_email = result['member_details']['user_email']

                    recent_action_time = result['recent_action'].partition(': ')[2]

                    if should_send_email_to_pecu(recent_action_time):
                        results.append({
                            'pending_enterprise_customer_user_id': pending_learner_id,
                            'recent_action': recent_action,
                            'user_email': user_email,
                        })
            return results
        except requests.exceptions.HTTPError:
            logger.exception('Failed to fetch data from LMS. URL: [%s].', url)
        except KeyError:
            logger.exception('Incorrect data received from LMS. [%s]', url)

        return None
