import logging

from django.core.management.base import BaseCommand

from enterprise_access.apps.workflows.models import (
    WorkflowActionStep,
    WorkflowDefinition,
    WorkflowGroupActionStepThrough,
    WorkflowItemThrough,
    WorkflowStepGroup
)

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

        # Example: Adding WorkflowActionSteps
        step_activate_enterprise_customer_user, _ = WorkflowActionStep.objects.get_or_create(
            name="Activate Enterprise Customer User",
            action_reference="enterprise_access.apps.workflows.handlers.activate_enterprise_customer_user"
        )
        step_activate_subsciption_license, _ = WorkflowActionStep.objects.get_or_create(
            name="Activate Subscription License",
            action_reference="enterprise_access.apps.workflows.handlers.activate_subscription_license"
        )
        step_auto_apply_subscription_license, _ = WorkflowActionStep.objects.get_or_create(
            name="Auto-apply Subscription License",
            action_reference="enterprise_access.apps.workflows.handlers.auto_apply_subscription_license"
        )
        step_retrieve_subscription_licenses, _ = WorkflowActionStep.objects.get_or_create(
            name="Retrieve Subscription Licenses",
            action_reference="enterprise_access.apps.workflows.handlers.retrieve_subscription_licenses"
        )
        step_retrieve_credits_available, _ = WorkflowActionStep.objects.get_or_create(
            name="Retrieve Credits Available",
            action_reference="enterprise_access.apps.workflows.handlers.retrieve_credits_available"
        )
        step_enroll_default_enterprise_course_enrollments, _ = WorkflowActionStep.objects.get_or_create(
            name="Enroll Default Enterprise Course Enrollments",
            action_reference="enterprise_access.apps.workflows.handlers.enroll_default_enterprise_course_enrollments"
        )
        step_retrieve_enterprise_course_enrollments, _ = WorkflowActionStep.objects.get_or_create(
            name="Retrieve Enterprise Course Enrollments",
            action_reference="enterprise_access.apps.workflows.handlers.retrieve_enterprise_course_enrollments"
        )

        logger.info('Created WorkflowActionSteps')

        # Example: Adding WorkflowStepGroup (retrieve subsidies)
        group_retrieve_subsidies, _ = WorkflowStepGroup.objects.get_or_create(
            name="Retrieve subsidies for enterprise customer user",
            run_in_parallel=True,
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_retrieve_subsidies,
            step=step_retrieve_subscription_licenses,
            defaults={'order': 0},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_retrieve_subsidies,
            step=step_retrieve_credits_available,
            defaults={'order': 1},
        )

        # Example: Adding WorkflowStepGroup (ensure activated subscription license)
        group_ensure_activated_subscription_license, _ = WorkflowStepGroup.objects.get_or_create(
            name="Ensure activated subscription license",
            run_in_parallel=False,
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_ensure_activated_subscription_license,
            step=step_retrieve_subscription_licenses,
            defaults={'order': 0},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_ensure_activated_subscription_license,
            step=step_activate_subsciption_license,
            defaults={'order': 1},
        )
        WorkflowGroupActionStepThrough.objects.get_or_create(
            step_group=group_ensure_activated_subscription_license,
            step=step_auto_apply_subscription_license,
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
            step=step_retrieve_enterprise_course_enrollments,
            defaults={'order': 1},
        )

        logger.info('Created WorkflowStepGroups and associated sub-steps')

        # Example: Add the WorkflowActionSteps and WorkflowStepGroups to the WorkflowDefinition
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            action_step=step_activate_enterprise_customer_user,
            defaults={'order': 0},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            step_group=group_ensure_activated_subscription_license,
            defaults={'order': 1},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            action_step=step_enroll_default_enterprise_course_enrollments,
            defaults={'order': 2},
        )
        WorkflowItemThrough.objects.get_or_create(
            workflow_definition=workflow_definition,
            step_group=group_retrieve_learner_portal_metadata,
            defaults={'order': 3},
        )

        logger.info('Added WorkflowActionSteps and WorkflowStepGroups to the WorkflowDefinition')

        logger.info('Result: Successfully seeded a test workflow!')
