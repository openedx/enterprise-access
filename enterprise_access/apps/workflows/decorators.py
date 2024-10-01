"""
Decorators for workflows app.
"""

from django.db.models import Q

from enterprise_access.apps.workflows.models import WorkflowActionStep
from enterprise_access.apps.workflows.registry import WorkflowActionRegistry


def workflow_action_step(slug, name):
    """
    A single decorator that registers the workflow action with the registry
    and ensures that a WorkflowActionStep exists in the database.

    :param slug: Unique identifier for the workflow action step.
    :param name: Human-readable name for the workflow action step.
    """
    def decorator(func):
        # Register the action step in the workflow registry
        WorkflowActionRegistry.register_action_step(slug, name)(func)

        # Check if the action step already exists and whether its name has changed
        existing_step = WorkflowActionStep.objects.filter(
            Q(action_reference=slug) & Q(name=name)
        ).first()

        if not existing_step:
            # Only update or create if the existing step was not found
            WorkflowActionStep.objects.update_or_create(
                action_reference=slug,
                defaults={"name": name}
            )

        # Return the original function to allow it to be used as normal
        return func

    return decorator
