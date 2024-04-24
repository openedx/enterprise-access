"""
Management command for making instances of models with test factories.
"""
from edx_django_utils.data_generation.management.commands.manufacture_data import Command as BaseCommand

from enterprise_access.apps.content_assignments.tests.factories import *
from enterprise_access.apps.core.tests.factories import *
from enterprise_access.apps.subsidy_access_policy.tests.factories import *
from enterprise_access.apps.subsidy_request.tests.factories import *


class Command(BaseCommand):
    """
    Management command for generating Django records from factories with custom attributes

    Example usage:
        $ ./manage.py manufacture_data /
            --model enterprise_access.apps.content_assignments.models.LearnerContentAssignment /
            --learner_email "test@email.com"
    """
