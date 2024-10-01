"""
Decorators for workflows app.
"""

from django.db.models import Q
from functools import wraps

from enterprise_access.apps.workflows.models import WorkflowActionStep
from enterprise_access.apps.workflows.registry import WorkflowActionStepRegistry


def workflow_action_step(slug, name, required_params=None):
    """
    A single decorator that registers the workflow action with the registry
    and ensures that a WorkflowActionStep exists in the database.

    :param slug: Unique identifier for the workflow action step.
    :param name: Human-readable name for the workflow action step.
    """

    if required_params is None:
        required_params = []

    def decorator(func):
        # Register the action step in the workflow registry
        WorkflowActionStepRegistry.register_action_step(slug, name, func, required_params)

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

        @wraps(func)
        def wrapper(*args, context=None, result=None, **kwargs):
            """
            Wrapper function to validate required parameters in the context.
            """
            # Validate required parameters in the context
            missing_params = [param for param in required_params if param not in context]
            if missing_params:
                raise ValueError(f"Missing required parameters: {', '.join(missing_params)}")

            # Call the actual function, passing the context and result
            return func(context=context, result=result, *args, **kwargs)

        # Return the original function to allow it to be used as normal
        return wrapper

    return decorator
