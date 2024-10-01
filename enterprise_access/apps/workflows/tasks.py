"""
Tasks for the workflows app.
"""

import logging
from importlib import import_module

from celery import shared_task
from celery.result import AsyncResult

from enterprise_access.apps.workflows.constants import WorkflowStatus
from enterprise_access.apps.workflows.models import (
    WorkflowExecutionStatus,
    WorkflowExecutionStepStatus,
    WorkflowGroupActionStepThrough
)
from enterprise_access.apps.workflows.utils import resolve_action_reference
from enterprise_access.tasks import LoggedTaskWithRetry

logger = logging.getLogger(__name__)


@shared_task(base=LoggedTaskWithRetry)
def handle_workflow_step_success(
    workflow_execution_step_status_id, result, *args, **kwargs
):  # pylint: disable=unused-argument
    """
    Task to handle successful workflow step execution.
    """
    try:
        step_status = WorkflowExecutionStepStatus.objects.get(id=workflow_execution_step_status_id)
        workflow_execution = step_status.workflow_execution
        has_async_result = not result and step_status.task_id
        step_result = None

        # If the step was executed asynchronously, get the async result
        if has_async_result:
            async_result = AsyncResult(step_status.task_id)
            if async_result.successful():
                step_result = async_result.result
        else:
            # Otherwise, use the result passed to the task
            step_result = result

        # Mark the step as completed
        step_status.mark_completed(task_id=step_status.task_id, result=step_result)

        # Check if there are more steps to execute
        if not workflow_execution.remaining_workflow_steps:
            workflow_execution.mark_completed()

    except WorkflowExecutionStepStatus.DoesNotExist as exc:
        logger.error(f"WorkflowExecutionStepStatus not found for {workflow_execution_step_status_id}")
        workflow_execution.mark_failed(exc=exc)


@shared_task(base=LoggedTaskWithRetry)
def handle_workflow_step_failure(
    workflow_execution_step_status_id, exc, *args, **kwargs
):  # pylint: disable=unused-argument
    """
    Task to handle failed workflow step execution.
    """
    try:
        step_status = WorkflowExecutionStepStatus.objects.get(id=workflow_execution_step_status_id)
        workflow_execution = step_status.workflow_execution
        has_async_result = not exc and step_status.task_id
        step_exc = None

        # If the step failed asynchronously, get the async result
        if has_async_result:
            async_result = AsyncResult(step_status.task_id)
            if async_result.failed():
                step_exc = async_result.result
        else:
            # Otherwise, use the exception passed to the task
            step_exc = exc

        # Mark the step and workflow as failed
        step_status.mark_failed(exc=step_exc)
        workflow_execution.mark_failed()
        logger.error(f"{step_status} failed with exception: {exc}")

    except WorkflowExecutionStepStatus.DoesNotExist:
        workflow_execution.mark_failed()
        logger.error(f"WorkflowExecutionStepStatus not found for {workflow_execution_step_status_id}")


@shared_task(base=LoggedTaskWithRetry)
def handle_workflow_failure(workflow_execution_status_uuid, exc, *args, **kwargs):  # pylint: disable=unused-argument
    """
    Task to handle failed workflow execution.
    """
    try:
        workflow_execution = WorkflowExecutionStatus.objects.get(uuid=workflow_execution_status_uuid)
        workflow_execution.mark_failed()
        logger.error(f"{workflow_execution} failed with exception: {exc}")

    except WorkflowExecutionStatus.DoesNotExist:
        logger.error(f"WorkflowExecutionStatus not found for {workflow_execution_status_uuid}")


@shared_task(base=LoggedTaskWithRetry)
def execute_workflow_step(
    workflow_action_step_through_id,
    action_reference,
    workflow_execution_status_uuid,
    *args, **kwargs  # pylint: disable=unused-argument
):
    """
    Executes the workflow step and updates the status to in progress, completed, or failed.
    """
    workflow_execution = WorkflowExecutionStatus.objects.get(uuid=workflow_execution_status_uuid)
    workflow_action_step_through = WorkflowGroupActionStepThrough.objects.get(id=workflow_action_step_through_id)
    step_status, step_status_created = WorkflowExecutionStepStatus.objects.get_or_create(
        workflow_execution=workflow_execution,
        step=workflow_action_step_through.step,
        defaults={'status': WorkflowStatus.PENDING},
    )

    if step_status_created:
        logger.info(
            f"Created new WorkflowExecutionStepStatus for step {step_status.step.name} "
            f"and user {workflow_execution.user}."
        )

    # Mark the step as in progress
    step_status.mark_in_progress()

    try:
        func = resolve_action_reference(action_reference)

        # Handle async Celery tasks
        if hasattr(func, 'apply_async') or hasattr(func, 'delay'):
            result = func.apply_async(
                link=handle_workflow_step_success.s(workflow_execution_step_status_id=step_status.id),
                link_error=handle_workflow_step_failure.s(workflow_execution_step_status_id=step_status.id),
            )
            task_id = result.id
            step_status.task_id = task_id
            step_status.save()
        else:
            # Handle sync functions by executing them directly
            result = func()
            handle_workflow_step_success.apply_async((step_status.id, result))

    except Exception as exc:
        # Mark step and workflow as failed in case of an exception
        step_status.mark_failed(error_message=str(exc))
        workflow_execution.mark_failed()
        handle_workflow_step_failure.apply_async((step_status.id, exc))
        raise exc  # Ensure the task raises the exception to trigger error handling
