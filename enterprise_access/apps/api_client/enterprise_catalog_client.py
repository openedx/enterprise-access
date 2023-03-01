"""
API client for enterprise-catalog service.
"""

from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient


class EnterpriseCatalogApiClient(BaseOAuthClient):
    """
    API client for calls to the enterprise catalog service.
    """
    api_base_url = settings.ENTERPRISE_CATALOG_URL + '/api/v1/'
    enterprise_catalog_endpoint = api_base_url + 'enterprise-catalogs/'

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
