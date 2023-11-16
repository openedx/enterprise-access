"""
API file interacting with assignment metadata (created to avoid a circular
import error)
"""
from enterprise_access.apps.content_metadata.api import get_and_cache_catalog_content_metadata


def get_content_metadata_for_assignments(enterprise_catalog_uuid, assignments):
    """
    Fetches (from cache or enterprise-catalog API call) content metadata
    in bulk for the `content_keys` of the given assignments, provided
    such metadata is related to the given `enterprise_catalog_uuid`.

    Returns:
        A dict mapping every content key of the provided assignments
        to a content metadata dictionary, or null if no such dictionary
        could be found for a given key.
    """
    content_keys = sorted({assignment.content_key for assignment in assignments})
    content_metadata_list = get_and_cache_catalog_content_metadata(enterprise_catalog_uuid, content_keys)
    metadata_by_key = {
        record['key']: record for record in content_metadata_list
    }
    return {
        assignment.content_key: metadata_by_key.get(assignment.content_key)
        for assignment in assignments
    }
