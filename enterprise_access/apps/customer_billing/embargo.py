"""Embargo country utilities."""

EMBARGOED_COUNTRY_CODES = ['RU', 'IR', 'KP', 'SY', 'CU']


def get_embargoed_countries():
    """Get list of 2-letter ISO embargoed country codes."""
    return EMBARGOED_COUNTRY_CODES
