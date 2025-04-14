"""
API client for calls to the LMS.
"""
import logging
import os

import requests
from django.conf import settings
from edx_django_utils.cache import TieredCache
from rest_framework import status

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient
from enterprise_access.apps.api_client.base_user import BaseUserApiClient
from enterprise_access.apps.api_client.exceptions import FetchGroupMembersConflictingParamsException
from enterprise_access.apps.enterprise_groups.constants import GROUP_MEMBERSHIP_EMAIL_ERROR_STATUS
from enterprise_access.cache_utils import versioned_cache_key
from enterprise_access.utils import localized_utcnow, should_send_email_to_pecu

logger = logging.getLogger(__name__)


def all_pages_enterprise_group_members_cache_key(
    group_uuid,
    sort_by,
    user_query,
    show_removed,
    is_reversed,
    learners,
):
    """
    helper method to retrieve the all enterprise group members cache key
    """
    return versioned_cache_key(
        'all_enterprise_group_members',
        group_uuid,
        sort_by,
        user_query,
        show_removed,
        is_reversed,
        learners,
    )


class LmsApiClient(BaseOAuthClient):
    """
    API client for calls to the LMS service.
    """
    enterprise_base_url = settings.LMS_URL + '/enterprise/'
    enterprise_api_v1_base_url = enterprise_base_url + 'api/v1/'
    enterprise_learner_endpoint = enterprise_api_v1_base_url + 'enterprise-learner/'
    enterprise_customer_endpoint = enterprise_api_v1_base_url + 'enterprise-customer/'
    enterprise_catalogs_endpoint = enterprise_api_v1_base_url + 'enterprise_catalogs/'
    create_enterprise_catalog_endpoint = enterprise_api_v1_base_url + 'enterprise_customer_catalog/'
    pending_enterprise_learner_endpoint = enterprise_api_v1_base_url + 'pending-enterprise-learner/'
    enterprise_group_membership_endpoint = enterprise_api_v1_base_url + 'enterprise-group/'
    pending_enterprise_admin_endpoint = enterprise_api_v1_base_url + 'pending-enterprise-admin/'
    enterprise_flex_membership_endpoint = enterprise_api_v1_base_url + 'enterprise-group-membership/'
    enterprise_course_enrollment_admin_endpoint = enterprise_api_v1_base_url + 'enterprise-course-enrollment-admin/'

    def get_course_enrollments_for_learner_profile(self, enterprise_uuid, lms_user_id):
        """
        Retrieves all course enrollments for a learner to be viewed by admin.

        Arguments:
            enterprise_uuid (UUID): the UUID of the enterprise customer
            lms_user_id (int): the lms user id of the user

        Returns:
            dict: Dictionary representation of the JSON response from the API
        """
        query_params = {
            'enterprise_uuid': enterprise_uuid,
            'lms_user_id': lms_user_id,
        }

        try:
            current_response = self.client.get(
                self.enterprise_course_enrollment_admin_endpoint,
                params=query_params,
                timeout=settings.LMS_CLIENT_TIMEOUT
            )
            current_response.raise_for_status()
            results = current_response.json().get('results', {})
            return results
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                f"Failed to fetch course enrollments for {lms_user_id}: {exc}"
            )
            raise

    def get_enterprise_group_memberships_for_learner(self, enterprise_uuid, lms_user_id, traverse_pagination=False):
        """
        Retrieves all flex group memberships for a learner.

        Arguments:
            enterprise_uuid (UUID): the UUID of the enterprise customer
            lms_user_id (int): the lms user id of the user

        Returns:
            dict: Dictionary representation of the JSON response from the API
        """
        query_params = {
            'enterprise_uuid': enterprise_uuid,
            'lms_user_id': lms_user_id,
        }

        results = []
        current_response = None
        next_url = self.enterprise_flex_membership_endpoint
        try:
            while next_url:
                current_response = self.client.get(
                    next_url,
                    params=query_params,
                    timeout=settings.LMS_CLIENT_TIMEOUT
                )
                current_response.raise_for_status()
                data = current_response.json()
                results.extend(data.get('results', []))

                next_url = data.get('next') if traverse_pagination else None
            return results
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                f"Failed to fetch enterprise flex group memberships for learner {lms_user_id}: {exc} "
                f"Response content: {current_response.content if current_response else None}"
            )
            raise

    def enterprise_customer_url(self, enterprise_customer_uuid):
        return os.path.join(
            self.enterprise_customer_endpoint,
            f"{enterprise_customer_uuid}/",
        )

    def enterprise_group_endpoint(self, group_uuid):
        return os.path.join(
            self.enterprise_api_v1_base_url + 'enterprise-group/',
            f"{group_uuid}/",
        )

    def enterprise_group_members_endpoint(self, group_uuid):
        return os.path.join(
            self.enterprise_group_endpoint(group_uuid),
            "learners/",
        )

    def enterprise_customer_bulk_enrollment_url(self, enterprise_customer_uuid):
        return os.path.join(
            self.enterprise_customer_url(enterprise_customer_uuid),
            "enroll_learners_in_courses/",
        )

    def get_enterprise_customer_data(self, enterprise_customer_uuid=None, enterprise_customer_slug=None):
        """
        Gets the data for an EnterpriseCustomer for the given uuid or slug.

        Arguments:
            enterprise_customer_uuid (string): id of the enterprise customer
            enterprise_customer_slug (string): slug of the enterprise customer
        Returns:
            dictionary containing enterprise customer metadata
        """
        if enterprise_customer_uuid:
            # Returns a dict
            endpoint = f'{self.enterprise_customer_endpoint}{enterprise_customer_uuid}/'
        elif enterprise_customer_slug:
            # Returns a list of dicts
            endpoint = f'{self.enterprise_customer_endpoint}?slug={enterprise_customer_slug}'
        else:
            raise ValueError('Either enterprise_customer_uuid or enterprise_customer_slug is required.')

        try:
            response = self.client.get(endpoint, timeout=settings.LMS_CLIENT_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if 'count' in payload:
                if results := payload.get('results'):
                    return results[0]
                return {}
            return payload
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise

    def create_enterprise_customer(self, *, name, slug, country, **kwargs):
        """
        Creates a new enterprise customer record.

        Arguments:
            name (string): The name of the customer.
            slug (string): Slug of the enterprise customer.
            country (string): The country code of the customer.
            kwargs (dict): Any other fields to specify for the newly-created customer.
        Returns:
            dictionary containing enterprise customer metadata
        """
        payload = {
            'name': name,
            'slug': slug,
            'country': country,
            'site': {
                'domain': settings.PROVISIONING_DEFAULTS['customer']['site_domain'],
            },
            **kwargs,
        }
        response = self.client.post(
            self.enterprise_customer_endpoint,
            json=payload,
            timeout=settings.LMS_CLIENT_TIMEOUT,
        )
        try:
            response.raise_for_status()
            payload = response.json()
            logger.info(
                'Successfully created customer %s with data %s',
                name, payload,
            )
            return payload
        except requests.exceptions.HTTPError:
            logger.exception(
                'Failed to create enterprise customer with name %s, response content %s',
                name, response.content.decode(),
            )
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

    def get_enterprise_pending_admin_users(self, enterprise_customer_uuid):
        """
        Gets all pending enterprise admin records for the given customer uuid.

        Arguments:
            enterprise_customer_uuid (UUID): UUID of the enterprise customer.
        Returns:
            List of dictionaries of pending admin users.
        """
        response = self.client.get(
            self.pending_enterprise_admin_endpoint + f'?enterprise_customer={enterprise_customer_uuid}',
            timeout=settings.LMS_CLIENT_TIMEOUT,
        )
        try:
            response.raise_for_status()
            logger.info(
                'Fetched pending admin records for customer %s', enterprise_customer_uuid,
            )
            payload = response.json()
            return payload.get('results', [])
        except requests.exceptions.HTTPError:
            logger.exception(
                'Failed to fetch pending admin record for customer %s: %s',
                enterprise_customer_uuid, response.content.decode()
            )
            raise

    def create_enterprise_admin_user(self, enterprise_customer_uuid, user_email):
        """
        Creates a new enterprise pending admin record.

        Arguments:
            enterprise_customer_uuid (UUID): UUID of the enterprise customer.
            user_email (string): The email address of the admin.
        Returns:
            dictionary describing the created pending admin record.
        """
        payload = {
            'enterprise_customer': enterprise_customer_uuid,
            'user_email': user_email,
        }
        response = self.client.post(
            self.pending_enterprise_admin_endpoint,
            json=payload,
            timeout=settings.LMS_CLIENT_TIMEOUT,
        )
        try:
            response.raise_for_status()
            logger.info(
                'Successfully created pending admin record for customer %s, email %s',
                enterprise_customer_uuid, user_email,
            )
            payload = response.json()
            return payload
        except requests.exceptions.HTTPError:
            logger.exception(
                'Failed to create pending admin record for customer %s, email %s: %s',
                enterprise_customer_uuid, user_email, response.content.decode()
            )
            raise

    def get_enterprise_catalogs(self, enterprise_customer_uuid, catalog_query_id=None):
        """
        Arguments:
            enterprise_customer_uuid (str): id of the enterprise customer
            catalog_query_id (int): Optional id of the catalog query record
              on which catalog records should be filtered.

        Returns:
            A list of all catalog records for the given customer, optionally filtered
            to only those with the provided catalog query id.
        """
        endpoint = self.enterprise_catalogs_endpoint + f'?enterprise_customer={enterprise_customer_uuid}'
        if catalog_query_id is not None:
            endpoint += f'&enterprise_catalog_query={catalog_query_id}'

        response = self.client.get(endpoint, timeout=settings.LMS_CLIENT_TIMEOUT)

        try:
            response.raise_for_status()
            payload = response.json()
            results = payload.get('results', [])
            logger.info(
                'Fetched %s catalog record(s) for customer %s', len(results), enterprise_customer_uuid,
            )
            return results
        except requests.exceptions.HTTPError:
            msg = 'Failed to fetch catalogs for customer %s and catalog query %s. Response content: %s'
            logger.exception(msg, enterprise_customer_uuid, catalog_query_id, response.content.decode())
            raise

    def create_enterprise_catalog(self, enterprise_customer_uuid, catalog_title, catalog_query_id):
        """
        Arguments:
            enterprise_customer_uuid (str): id of the enterprise customer
            catalog_title (str): title of the catalog
            catalog_query_id (int): id of the catalog query record associated with the catalog

        Returns:
            A create enterprise catalog dict.
        """
        query_id = catalog_query_id or settings.PROVISIONING_DEFAULTS['catalog']['catalog_query_id']
        payload = {
            'enterprise_customer': enterprise_customer_uuid,
            'title': catalog_title,
            'enterprise_catalog_query': query_id,
        }

        response = self.client.post(
            self.create_enterprise_catalog_endpoint,
            json=payload,
            timeout=settings.LMS_CLIENT_TIMEOUT,
        )

        try:
            response.raise_for_status()
            payload = response.json()
            logger.info('Created catalog record %s', payload)
            return payload
        except requests.exceptions.HTTPError:
            msg = 'Failed to create catalog for customer %s with title %s and catalog query %s. Response content: %s'
            logger.exception(
                msg, enterprise_customer_uuid, catalog_title, catalog_query_id, response.content.decode(),
            )
            raise

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
        show_removed=False,
        is_reversed=False,
        traverse_pagination=False,
        page=1,
        learners=None,
    ):
        """
        Fetches enterprise group member records from edx-platform.

        Params:
            - ``group_uuid`` (string, UUID): The group record PK to fetch members from.
            - ``sort_by`` (string, optional): Specify how the returned members should be ordered. Supported sorting
            values
            are `member_details`, `member_status`, and `recent_action`.
            - ``user_query`` (string, optional): Filter the returned members by user email with a provided sub-string.
            - ``show_removed`` (bool, optional): Include removed membership records.
            - ``is_reversed`` (bool, optional): Reverse the order of the returned members.
            - ``traverse_pagination`` (bool, optional): Indicates that the lms client should traverse and fetch all
            pages.
            of data. Cannot be supplied if ``page`` is supplied.
            - ``page`` (int, optional): Which page of paginated data to return. Cannot be supplied if
            ``traverse_pagination`` is supplied.
        """
        if bool(traverse_pagination) == bool(page):
            raise FetchGroupMembersConflictingParamsException(
                'Params `traverse_pagination` and `page` are in conflict, must supply exactly one or the other.'
            )

        group_members_url = self.enterprise_group_members_endpoint(group_uuid)
        params = {
            "sort_by": sort_by,
            "user_query": user_query,
            "page": page,
        }
        if show_removed:
            params['show_removed'] = show_removed
        if is_reversed:
            params['is_reversed'] = is_reversed
        if learners:
            params['learners'] = learners
        if traverse_pagination:
            cache_key = all_pages_enterprise_group_members_cache_key(
                group_uuid,
                sort_by,
                user_query,
                show_removed,
                is_reversed,
                learners,
            )
            cached_response = TieredCache.get_cached_response(cache_key)
            if cached_response.is_found:
                logger.info(
                    f'all_enterprise_group_members cache hit for group_uuid {group_uuid}.'
                )
                return cached_response.value

            params['page_size'] = 500

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

            TieredCache.set_all_tiers(cache_key, response_json, settings.ALL_ENTERPRISE_GROUP_MEMBERS_CACHE_TIMEOUT)
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

                    if (settings.BRAZE_GROUP_EMAIL_FORCE_REMIND_ALL_PENDING_LEARNERS or
                            should_send_email_to_pecu(recent_action_time)):
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

    def update_pending_learner_status(self, enterprise_group_uuid, learner_email):
        """
        Partially updates learners

        Arguments:
            enterprise_group_uuid (str): uuid of the enterprise group uuid
            learner_email (str): email for learner

        """
        try:
            url = f'{self.enterprise_group_membership_endpoint}' + (
                f'{enterprise_group_uuid}/learners/')
            payload = {'learner': learner_email,
                       'status': GROUP_MEMBERSHIP_EMAIL_ERROR_STATUS,
                       'errored_at': localized_utcnow()}
            response = self.client.patch(url, data=payload)
            return response.json()
        except requests.exceptions.HTTPError:
            logger.exception('failed to update group membership status. URL: [%s].', url)
        except KeyError:
            logger.exception('failed to update group membership status. [%s]', url)
        return None

    def bulk_enroll_enterprise_learners(self, enterprise_customer_uuid, enrollments_info):
        """
        Calls the Enterprise Bulk Enrollment API to enroll learners in courses.

        Arguments:
            enterprise_customer_uuid (UUID): UUID representation of the customer that the enrollment will be linked to
            enrollment_info (list[dicts]): List of enrollment information required to enroll.
                Each entry must contain key/value pairs as follows:
                    user_id: ID of the learner to be enrolled
                    course_run_key: the course run key to be enrolled in by the user
                    [transaction_id,license_uuid]: uuid representation of the subsidy identifier
                      that allows the enrollment
                    is_default_auto_enrollment (optional): boolean indicating whether the enrollment
                      is the realization of a default enrollment intention.
                Example::
                    [
                        {
                            'user_id': 1234,
                            'course_run_key': 'course-v2:edX+FunX+Fun_Course',
                            'transaction_id': '84kdbdbade7b4fcb838f8asjke8e18ae',
                        },
                        {
                            'user_id': 1234,
                            'course_run_key': 'course-v2:edX+FunX+Fun_Course',
                            'license_uuid': '00001111de7b4fcb838f8asjke8effff',
                            'is_default_auto_enrollment': True,
                        },
                        ...
                    ]
        Returns:
            response (dict): JSON response data
        Raises:
            requests.exceptions.HTTPError: if service is down/unavailable or status code comes back >= 300,
            the method will log and throw an HTTPError exception.
        """
        bulk_enrollment_url = self.enterprise_customer_bulk_enrollment_url(enterprise_customer_uuid)
        options = {'enrollments_info': enrollments_info}
        response = self.client.post(
            bulk_enrollment_url,
            json=options,
        )
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.error(
                f'Failed to generate enterprise enrollments for enterprise: {enterprise_customer_uuid} '
                f'with options: {options}. Failed with error: {exc} and payload %s',
                response.json(),
            )
            raise exc


class LmsUserApiClient(BaseUserApiClient):
    """
    API client for user-specific calls to the LMS service.
    """
    enterprise_base_url = settings.LMS_URL + "/enterprise/"
    enterprise_api_v1_base_url = enterprise_base_url + "api/v1/"
    enterprise_learner_portal_api_base_url = f"{settings.LMS_URL}/enterprise_learner_portal/api/v1/"
    enterprise_learner_endpoint = f"{enterprise_api_v1_base_url}enterprise-learner/"
    default_enterprise_enrollment_intentions_learner_status_endpoint = (
        f'{enterprise_api_v1_base_url}default-enterprise-enrollment-intentions/learner-status/'
    )
    enterprise_course_enrollments_endpoint = (
        f'{enterprise_learner_portal_api_base_url}enterprise_course_enrollments/'
    )

    def get_enterprise_customers_for_user(self, username, traverse_pagination=False):
        """
        Fetches enterprise learner data for a given username.

        Arguments:
            username (str): Username of the learner

        Returns:
            dict: Dictionary representation of the JSON response from the API
        """
        query_params = {
            'username': username,
        }
        results = []
        initial_response_data = None
        current_response = None
        next_url = self.enterprise_learner_endpoint
        try:
            while next_url:
                current_response = self.get(
                    next_url,
                    params=query_params,
                    timeout=settings.LMS_CLIENT_TIMEOUT
                )
                current_response.raise_for_status()
                data = current_response.json()

                if not initial_response_data:
                    # Store the initial response data (first page) for later use
                    initial_response_data = data

                # Collect results from the current page
                results.extend(data.get('results', []))

                # If pagination is enabled, continue with the next page; otherwise, break
                next_url = data.get('next') if traverse_pagination else None

            consolidated_response = {
                **initial_response_data,
                'next': None,
                'previous': None,
                'count': len(results),
                'num_pages': 1,
                'results': results,
            }
            return consolidated_response
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                f"Failed to fetch enterprise learner for learner {username}: {exc} "
                f"Response content: {current_response.content if current_response else None}"
            )
            raise

    def get_default_enterprise_enrollment_intentions_learner_status(self, enterprise_customer_uuid):
        """
        Fetches learner status from the default enterprise enrollment intentions endpoint.

        Arguments:
            enterprise_customer_uuid (str): UUID of the enterprise customer

        Returns:
            dict: Dictionary representation of the JSON response from the API
        """
        query_params = {'enterprise_customer_uuid': enterprise_customer_uuid}
        response = None
        try:
            response = self.get(
                self.default_enterprise_enrollment_intentions_learner_status_endpoint,
                params=query_params,
                timeout=settings.LMS_CLIENT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                f"Failed to fetch default enterprise enrollment intentions for enterprise customer "
                f"{enterprise_customer_uuid} and learner {self.request_user.lms_user_id}: {exc} "
                f"Response content: {response.content if response else None}"
            )
            raise

    def get_enterprise_course_enrollments(self, enterprise_customer_uuid, **params):
        """
        Fetches course enrollments for a given enterprise customer.

        Arguments:
            enterprise_customer_uuid (str): UUID of the enterprise customer
            params (dict): Additional query parameters to include in the request

        Returns:
            dict: Dictionary representation of the JSON response from the API
        """
        query_params = {
            'enterprise_id': enterprise_customer_uuid,
            **params,
        }
        response = None
        try:
            response = self.get(
                self.enterprise_course_enrollments_endpoint,
                params=query_params,
                timeout=settings.LMS_CLIENT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(
                f"Failed to fetch enterprise course enrollments for enterprise customer "
                f"{enterprise_customer_uuid} and learner {self.request_user.lms_user_id}: {exc} "
                f"Response content: {response.content if response else None}"
            )
            raise
