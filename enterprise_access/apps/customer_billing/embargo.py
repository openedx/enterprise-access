"""Embargo country utilities."""
from django.conf import settings


def get_embargoed_countries():
    """Get list of 2-letter ISO embargoed country codes."""
    return settings.EMBARGOED_COUNTRY_CODES
