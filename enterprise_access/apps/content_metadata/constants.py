"""
Constants about content metadata.
"""
from enum import Enum


class ProductSources(Enum):
    """
    Content metadata product_source keys
    """
    EDX = "edX"  # e.g. OCM courses
    TWOU = "2u"  # e.g. ExecEd courses


class CourseModes(Enum):
    """
    Content metadata course mode keys
    """
    EDX_VERIFIED = "verified"  # e.g. edX Verified Courses
    EXECUTIVE_EDUCATION = "paid-executive-education"  # e.g. ExecEd courses


DEFAULT_CONTENT_PRICE = 0.0
CENTS_PER_DOLLAR = 100
