import logging

from django.core.management.base import BaseCommand

from enterprise_access.apps.workflows.models import (
    WorkflowActionStep,
    WorkflowDefinition,
    WorkflowGroupActionStepThrough,
    WorkflowItemThrough,
    WorkflowStepGroup
)
from enterprise_access.apps.workflows.registry import WorkflowActionStepRegistry

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Seed the database with test workflows.
    """
    help = 'Seeds the database with test workflows'

    def handle(self, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Seed the database with test workflows.
        """
        # Example: Creating a test WorkflowDefinition
        workflow_definition, _ = WorkflowDefinition.objects.get_or_create(
            name="[Learner Portal] Enterprise slug page view",
            is_active=True,
            is_default=True,
        )
        logger.info(f'Created WorkflowDefinition: {workflow_definition.name}')

        # Get all registered slugs from the registry
        registry_actions = WorkflowActionStepRegistry.list_actions()

        steps = {}
        for slug, _ in registry_actions:
            try:
                steps[slug] = WorkflowActionStep.objects.get(action_reference=slug)
            except WorkflowActionStep.DoesNotExist:
                logger.error(
                    f"WorkflowActionStep with action_reference '{slug}' not found. Ensure it is registered properly."
                )
        logger.info('Validated WorkflowActionSteps from registered actions in the WorkflowActionStepRegistry.')

        # Example: Adding WorkflowStepGroup (retrieve subsidies)
        group_retrieve_subsidies, _ = WorkflowStepGroup.objects.get_or_create(
            name="Retrieve subsidies for enterprise customer user",
            run_in_parallel=True,
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_retrieve_subsidies,
            step=steps.get('retrieve_subscription_licenses'),
            defaults={'order': 0},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_retrieve_subsidies,
            step=steps.get('retrieve_credits_available'),
            defaults={'order': 1},
        )

        # Example: Adding WorkflowStepGroup (ensure activated subscription license)
        group_ensure_activated_subscription_license, _ = WorkflowStepGroup.objects.get_or_create(
            name="Ensure activated subscription license",
            run_in_parallel=False,
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_ensure_activated_subscription_license,
            step=steps.get('retrieve_subscription_licenses'),
            defaults={'order': 0},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_ensure_activated_subscription_license,
            step=steps.get('activate_subscription_license'),
            defaults={'order': 1},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_ensure_activated_subscription_license,
            step=steps.get('auto_apply_subscription_license'),
            defaults={'order': 2},
        )

        # Example: Adding WorkflowStepGroup (retrieve Learner Portal metadata)
        group_retrieve_learner_portal_metadata, _ = WorkflowStepGroup.objects.get_or_create(
            name="Retrieve metadata for Learner Portal page view",
            run_in_parallel=True,
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_retrieve_learner_portal_metadata,
            group=group_retrieve_subsidies,
            defaults={'order': 0},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_retrieve_learner_portal_metadata,
            step=steps.get('retrieve_enterprise_course_enrollments'),
            defaults={'order': 1},
        )

        logger.info('Created WorkflowStepGroups and added WorkflowActionSteps to the groups.')

        # Example: Add the WorkflowActionSteps and WorkflowStepGroups to the WorkflowDefinition
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            action_step=steps.get('activate_enterprise_customer_user'),
            defaults={'order': 0},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            step_group=group_ensure_activated_subscription_license,
            defaults={'order': 1},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            action_step=steps.get('enroll_default_enterprise_course_enrollments'),
            defaults={'order': 2},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            step_group=group_retrieve_learner_portal_metadata,
            defaults={'order': 3},
        )

        logger.info('Added WorkflowActionSteps and WorkflowStepGroups to the WorkflowDefinition')

        logger.info('Successfully seeded a test workflow.')
