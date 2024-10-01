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


def seed_full_workflow_example():
    """
    Seed the database with a full example workflow.
    """


class Command(BaseCommand):
    """
    Seed the database with test workflows.
    """
    help = 'Seeds the database with test workflows'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.steps = {}
        self.group_retrieve_subsidies = None
        self.group_ensure_activated_subscription_license = None
        self.group_retrieve_learner_portal_metadata = None
        self.group_retrieve_subscription_licenses_default_enterprise_course_enrollments = None

    def handle(self, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Seed the database with test workflows.
        """
        self.validate_registry_workflow_action_steps()

        self.create_workflow_step_groups()

        # Seed example workflows
        self.seed_simple_workflow_example()
        self.seed_complex_workflow_example()

        logger.info('Successfully seeded workflows.')

    def validate_registry_workflow_action_steps(self):
        """
        Validate the WorkflowActionSteps from the registered actions in the WorkflowActionStepRegistry.
        """
        # Get all registered slugs from the registry
        registry_actions = WorkflowActionStepRegistry.list_actions()

        for slug, _ in registry_actions:
            try:
                self.steps[slug] = WorkflowActionStep.objects.get(action_reference=slug)
            except WorkflowActionStep.DoesNotExist:
                logger.error(
                    f"WorkflowActionStep with action_reference '{slug}' not found. Ensure it is registered properly."
                )
        logger.info('Validated WorkflowActionSteps from registered actions in the WorkflowActionStepRegistry.')

    def create_workflow_step_groups(self):
        """
        Create and return a WorkflowStepGroup instance.
        """
        # Example: Adding WorkflowStepGroup (retrieve subsidies)
        self.group_retrieve_subsidies, _ = WorkflowStepGroup.objects.get_or_create(
            name="Retrieve subsidies for enterprise customer user",
            run_in_parallel=True,
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=self.group_retrieve_subsidies,
            step=self.steps.get('retrieve_subscription_licenses'),
            defaults={'order': 0},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=self.group_retrieve_subsidies,
            step=self.steps.get('retrieve_credits_available'),
            defaults={'order': 1},
        )

        # Example: Adding WorkflowStepGroup (ensure activated subscription license)
        self.group_ensure_activated_subscription_license, _ = WorkflowStepGroup.objects.get_or_create(
            name="Ensure activated subscription license",
            run_in_parallel=False,
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=self.group_ensure_activated_subscription_license,
            step=self.steps.get('retrieve_subscription_licenses'),
            defaults={'order': 0},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=self.group_ensure_activated_subscription_license,
            step=self.steps.get('activate_subscription_license'),
            defaults={'order': 1},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=self.group_ensure_activated_subscription_license,
            step=self.steps.get('auto_apply_subscription_license'),
            defaults={'order': 2},
        )

        # Example: Adding WorkflowStepGroup (retrieve Learner Portal metadata)
        self.group_retrieve_learner_portal_metadata, _ = WorkflowStepGroup.objects.get_or_create(
            name="Retrieve metadata for Learner Portal page view",
            run_in_parallel=True,
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=self.group_retrieve_learner_portal_metadata,
            group=self.group_retrieve_subsidies,
            defaults={'order': 0},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=self.group_retrieve_learner_portal_metadata,
            step=self.steps.get('retrieve_enterprise_course_enrollments'),
            defaults={'order': 1},
        )

        # Example: Adding WorkflowStepGroup (enroll default enterprise course enrollments)
        self.group_retrieve_subscription_licenses_default_enterprise_course_enrollments, _ = (
            WorkflowStepGroup.objects.get_or_create(
                name="Retrieve subscription licenses and default enterprise course enrollments",
                run_in_parallel=True,
            )
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=self.group_retrieve_subscription_licenses_default_enterprise_course_enrollments,
            step=self.steps.get('retrieve_default_enterprise_course_enrollments'),
            defaults={'order': 0},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=self.group_retrieve_subscription_licenses_default_enterprise_course_enrollments,
            step=self.steps.get('retrieve_subscription_licenses'),
            defaults={'order': 1},
        )

        logger.info('Created WorkflowStepGroups and added WorkflowActionSteps to the groups.')

    def create_workflow_definition(self, **kwargs):
        """
        Create and return a WorkflowDefinition instance.
        """
        workflow_definition, _ = WorkflowDefinition.objects.get_or_create(**kwargs)
        logger.info(f'Created WorkflowDefinition: {workflow_definition.name}')
        return workflow_definition

    def seed_simple_workflow_example(self):
        """
        Seed the database with a simple workflow.
        """
        workflow_definition = self.create_workflow_definition(
            name="[Learner Portal] Process default enterprise course enrollments",
            is_active=True,
            is_default=True,
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            step_group=self.group_retrieve_subscription_licenses_default_enterprise_course_enrollments,
            defaults={'order': 0},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            action_step=self.steps.get('enroll_default_enterprise_course_enrollments'),
            defaults={'order': 2},
        )
        logger.info(f"Added WorkflowActionSteps and WorkflowStepGroups to the {workflow_definition}")

    def seed_complex_workflow_example(self):
        """
        Seed the database with a more complex workflow.
        """
        workflow_definition = self.create_workflow_definition(
            name="[Learner Portal] Enterprise slug page view",
            is_active=True,
            is_default=True,
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            action_step=self.steps.get('activate_enterprise_customer_user'),
            defaults={'order': 0},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            step_group=self.group_ensure_activated_subscription_license,
            defaults={'order': 1},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            step_group=self.group_retrieve_subscription_licenses_default_enterprise_course_enrollments,
            defaults={'order': 2},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            action_step=self.steps.get('enroll_default_enterprise_course_enrollments'),
            defaults={'order': 3},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            step_group=self.group_retrieve_learner_portal_metadata,
            defaults={'order': 4},
        )
        logger.info(f"Added WorkflowActionSteps and WorkflowStepGroups to the {workflow_definition}")
