"""
Utility methods for provisioning app.
"""
import logging
from datetime import datetime
from typing import Optional, Tuple
from uuid import UUID

from attrs import validators
from django.core.validators import validate_email

from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.customer_billing.stripe_api import get_stripe_trialing_subscription

logger = logging.getLogger(__name__)


def attrs_validate_email(instance, attribute, value):  # pylint: disable=unused-argument
    """
    Validator callable with a signature expected by attrs.
    See: https://www.attrs.org/en/stable/api.html#attrs.field

    ``validator(Callable|list[Callable]) Callable that is called by attrs-generated __init__
    methods after the instance has been initialized.
    They receive the initialized instance, the Attribute(), and the passed value.``
    """
    return validate_email(value)


def is_list_of_type(the_type, extra_member_validators=None):
    extra_inner = extra_member_validators or []
    member_validators = [validators.instance_of(the_type)] + extra_inner

    return validators.deep_iterable(
        member_validator=member_validators,
        iterable_validator=validators.instance_of(list),
    )


is_uuid = validators.instance_of(UUID)

is_str = validators.instance_of(str)

is_int = validators.instance_of(int)

is_datetime = validators.instance_of(datetime)

is_bool = validators.instance_of(bool)


def validate_trial_subscription(enterprise_slug: str) -> Tuple[bool, Optional[dict]]:
    """
    Validate and get trial subscription information for an enterprise.

    This function checks if a valid trial subscription exists for a given enterprise
    by validating the checkout intent and stripe subscription status.

    Args:
        enterprise_slug (str): The enterprise slug to check

    Returns:
        Tuple[bool, Optional[dict]]: A tuple containing:
            - bool: Whether the validation passed
            - Optional[dict]: Trial subscription data if found, None otherwise
    """
    try:
        intent = CheckoutIntent.objects.filter(enterprise_slug=enterprise_slug).first()
        if not intent or not intent.stripe_customer_id:
            logger.warning(
                'No valid checkout intent found for enterprise %s',
                enterprise_slug
            )
            return False, None

        subscription = get_stripe_trialing_subscription(intent.stripe_customer_id)
        if not subscription:
            logger.info(
                'No trial subscription found for enterprise %s',
                enterprise_slug
            )
            return False, None

        return True, subscription

    except Exception as exc:
        logger.exception(
            'Error validating trial subscription for enterprise %s: %s',
            enterprise_slug,
            str(exc)
        )
        return False, None
