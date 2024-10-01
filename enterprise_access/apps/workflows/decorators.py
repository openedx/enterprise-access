"""
Decorators for workflows app.
"""

from functools import wraps

from django.db.models import Q

from enterprise_access.apps.workflows.models import WorkflowActionStep
from enterprise_access.apps.workflows.registry import WorkflowActionStepRegistry


def workflow_action_step(
    slug,
    name,
    required_params=None,
    prerequisite_steps=None,
    validate_func=None
):
    """
    Decorator to register a workflow action step and ensure its existence in the database.

    :param slug: Unique identifier for the workflow action step.
    :param name: Human-readable name for the workflow action step.
    :param [required_params]: A list of parameters required to execute this action.
    :param [prerequisite_steps]: A list of prerequisite steps that must be executed before this action step.
    :param [validate_func]: A custom validation function to run before executing the action step.
    """

    required_params = required_params or []
    prerequisite_steps = prerequisite_steps or []

    def decorator(func):
        # Register the action step in the workflow registry
        WorkflowActionStepRegistry.register_action_step(slug, name, func, required_params)

        # Create or update the WorkflowActionStep if it does not exist
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
            Wrapper function to validate:
              - required context parameters
              - prerequisite steps
              - custom validation function

            If validation succeeds, the actual function is called with the context and result.

            :param context: The context dictionary containing the required parameters.
            :param result: The shared result dictionary to store the results of each action step.
            :return The result dictionary with the action step's result added.
            """
            if result is None:
                result = {}

            # Validate the workflow action step
            validate_workflow_action_step(
                context=context,
                result=result,
                prerequisite_steps=prerequisite_steps,
                required_params=required_params,
                validate_func=validate_func,
            )

            # Call the actual function, passing the context and result
            workflow_step_result = func(context=context, result=result, *args, **kwargs)

            # Mutate the shared result dict to include the WorkflowActionStep's
            # slug as the key and the actual result as the value. This ensures that
            # the result of the action step is available to other steps in the workflow.
            result[slug] = workflow_step_result

            return result

        return wrapper

    return decorator


def validate_prerequisites_in_registry(prerequisite_steps):
    """
    Validates that all prerequisite steps are registered in the registry.

    :param prerequisite_steps: A list of prerequisite step slugs.
    :raises ValueError: If any prerequisite steps are not registered.
    """
    invalid_prerequisites = [
        step_slug for step_slug in prerequisite_steps
        if not WorkflowActionStepRegistry.is_step_registered(step_slug)
    ]
    if invalid_prerequisites:
        raise ValueError(f"Invalid prerequisite steps: {', '.join(invalid_prerequisites)}")


def check_prerequisite_steps(prerequisite_steps, result):
    """
    Checks that all prerequisite steps have been executed and are available in the result.

    :param prerequisite_steps: A list of prerequisite step slugs.
    :param result: The shared result dictionary containing outputs from previous steps.
    :raises ValueError: If any prerequisite steps are missing from the result.
    """
    missing_prerequisites = [
        step_slug for step_slug in prerequisite_steps
        if step_slug not in result
    ]
    if missing_prerequisites:
        raise ValueError(f"Missing prerequisite steps: {', '.join(missing_prerequisites)}")


def check_required_params(required_params, context):
    """
    Checks that all required parameters are available in the context.

    :param required_params: A list of required parameter names.
    :param context: The context dictionary with input parameters.
    :raises ValueError: If any required parameters are missing from the context.
    """
    missing_params = [
        param for param in required_params
        if param not in context
    ]
    if missing_params:
        raise ValueError(f"Missing required parameters: {', '.join(missing_params)}")


def check_custom_validation(context, validate_func):
    """
    Checks that the custom validation function is callable and runs it.
    """
    # If a custom validation function is provided and is callable, run it
    if validate_func and callable(validate_func):
        validate_func(context)  # The validation function can raise exceptions if validation fails


def validate_workflow_action_step(
    context,
    result,
    prerequisite_steps,
    required_params,
    validate_func,
):
    """
    Validates the workflow action step by checking prerequisite steps, and required parameters.
    """

    # Validate that all prerequisite steps are registered
    validate_prerequisites_in_registry(prerequisite_steps)

    # Ensure prerequisite steps have been executed and present in the result
    check_prerequisite_steps(prerequisite_steps, result)

    # Ensure required parameters are available in the context
    check_required_params(required_params, context)

    # Run custom validation function, if provided
    check_custom_validation(context, validate_func)
