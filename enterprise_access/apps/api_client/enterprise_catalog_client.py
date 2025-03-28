"""
API client for enterprise-catalog service.
"""
from urllib.parse import urljoin

import backoff
from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient
from enterprise_access.apps.api_client.base_user import BaseUserApiClient
from enterprise_access.apps.api_client.constants import autoretry_for_exceptions


class EnterpriseCatalogApiClient(BaseOAuthClient):
    """
    V2 API client for calls to the enterprise catalog service.
    """
    api_version = 'v2'

    def __init__(self):
        self.api_base_url = urljoin(settings.ENTERPRISE_CATALOG_URL, f'api/{self.api_version}/')
        self.enterprise_catalog_endpoint = urljoin(self.api_base_url, 'enterprise-catalogs/')
        super().__init__()

    @backoff.on_exception(wait_gen=backoff.expo, exception=autoretry_for_exceptions)
    def contains_content_items(self, catalog_uuid, content_ids):
        """
        Check whether the specified enterprise catalog contains the given content.

        Arguments:
            catalog_uuid (UUID): UUID of the enterprise catalog to check.
            content_ids (list of str): List of content ids to check whether the catalog contains. The endpoint does not
            differentiate between course_run_ids and program_uuids so they can be used interchangeably.

        Returns:
            bool: Whether the given content_ids were found in the specified enterprise catalog.
        """
        query_params = {'course_run_ids': content_ids}
        endpoint = self.enterprise_catalog_endpoint + str(catalog_uuid) + '/contains_content_items/'
        response = self.client.get(endpoint, params=query_params)
        response.raise_for_status()
        response_json = response.json()
        return response_json.get('contains_content_items', False)

    @backoff.on_exception(wait_gen=backoff.expo, exception=autoretry_for_exceptions)
    def catalog_content_metadata(self, catalog_uuid, content_keys, traverse_pagination=True, **kwargs):
        """
        Returns a list of requested content metadata records for the given catalog_uuid.
        See the enterprise-catalog ``EnterpriseCatalogGetContentMetadata`` view.

        Arguments:
            catalog_uuid (UUID): UUID of the enterprise catalog to check.
            content_keys (list of str): List of content keys in the catalog for which metadata should be fetched.
                Note that the endpoint called by this client only supports up to 100 keys per request.
            traverse_pagination (bool, default True): If true, forces the requested endpoint
                to tranverse pagination for us.
                This means a single response payload will contain all results
                and there's no need for us to fetch multiple pages.

        Returns:
            A paginated results dict, where the "results" key contains
            a list of dicts. These are content metadata dicts for the requested keys
            (as long as they are associated with the given catalog_uuid).
            When the "next" key of results is not null, there are further
            pages of results that can be fetched - it's up to the caller to fetch these.
        """
        if not content_keys and traverse_pagination:
            raise Exception('Cannot request all metadata for a catalog when traverse_pagination is true.')

        query_params = {
            'content_keys': content_keys,
            'traverse_pagination': traverse_pagination,
            **kwargs,
        }
        endpoint = f'{self.enterprise_catalog_endpoint}{catalog_uuid}/get_content_metadata/'

        response = self.client.get(endpoint, params=query_params)
        response.raise_for_status()
        return response.json()

    @backoff.on_exception(wait_gen=backoff.expo, exception=autoretry_for_exceptions)
    def get_content_metadata_count(self, catalog_uuid):
        """
        Returns the count of content metadata for a catalog.
        Arguments:
            catalog_uuid (UUID): UUID of the enterprise catalog to check.
        Returns:
            The number of content metadata for a catalog.
        """
        endpoint = self.enterprise_catalog_endpoint + str(catalog_uuid) + '/get_content_metadata/'
        response = self.client.get(endpoint)
        response.raise_for_status()
        return response.json()['count']

    def content_metadata(self, content_id):
        raise NotImplementedError('There is currently no v2 API implementation for this endpoint.')


class EnterpriseCatalogApiV1Client(EnterpriseCatalogApiClient):
    """
    V1 API client for calls to the enterprise catalog service.
    """
    api_version = 'v1'

    def __init__(self):
        super().__init__()
        self.content_metadata_endpoint = urljoin(self.api_base_url, 'content-metadata/')

    @backoff.on_exception(wait_gen=backoff.expo, exception=autoretry_for_exceptions)
    def content_metadata(self, content_id, coerce_to_parent_course=False):
        """
        Fetch catalog-/customer-agnostic content metadata.

        Arguments:
            content_id (str): ID of content to fetch.

        Returns:
            dict: serialized content metadata, or None if not found.
        """
        query_params = {}
        if coerce_to_parent_course:
            query_params |= {'coerce_to_parent_course': True}
        kwargs = {'params': query_params} if query_params else {}
        endpoint = self.content_metadata_endpoint + content_id
        response = self.client.get(endpoint, **kwargs)
        response.raise_for_status()
        return response.json()


class EnterpriseCatalogUserV1ApiClient(BaseUserApiClient):
    """
    API client for user-specific calls to the enterprise catalog V1 service
    """

    api_version = 'v1'
    api_base_url = urljoin(settings.ENTERPRISE_CATALOG_URL, f'api/{api_version}/')

    def secured_algolia_api_key_endpoint(self, enterprise_customer_uuid: str) -> str:
        """
         Constructs the API endpoint URL for fetching the secured Algolia API key
         for a specific enterprise customer.

         Args:
             enterprise_customer_uuid (str): The UUID of the enterprise customer. Must not be empty.

         Returns:
             str: Fully-qualified URL for the secured Algolia API key endpoint.

         Raises:
             ValueError: If `enterprise_customer_uuid` is not provided or is an empty string.
         """
        if not enterprise_customer_uuid:
            raise ValueError(f'Invalid enterprise_customer_uuid: {enterprise_customer_uuid!r}')

        secured_algolia_api_key_path: str = f'enterprise-customer/{enterprise_customer_uuid}/secured-algolia-api-key/'
        return urljoin(self.api_base_url, secured_algolia_api_key_path)

    def get_secured_algolia_api_key(self, enterprise_customer_uuid):
        """
        Fetch secured algolia API keys

        Arguments:
            enterprise_customer_uuid (uuid): UUID of the enterprise customer

        Returns:
            200:
                'algolia': {
                    'secured_api_key' (str): The secured api key for algolia to consume in the client
                    'valid_until' (Datetime): The date until the secured api key is valid until in the client
                }
                'catalog_uuids_to_catalog_query_uuids': A dictionary of catalog uuids mapped to their corresponding
                    catalog query uuids.
            400:
                'user_message' (str): Message of corresponding error indicating a user oriented message
                'developer_message' (str): Message of corresponding error indicating an actionable developer message
        """
        response = self.get(self.secured_algolia_api_key_endpoint(enterprise_customer_uuid))
        response.raise_for_status()
        return response.json()
