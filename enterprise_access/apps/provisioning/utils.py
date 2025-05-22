"""
Utility methods for provisioning app.
"""
from datetime import datetime
from uuid import UUID

from attrs import validators
from django.core.validators import validate_email


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
