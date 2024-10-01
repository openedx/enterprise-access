"""
This module contains the WorkflowRunner class, which is responsible for
executing a WorkflowDefinition by running each of its steps in order.
"""

import logging

from celery import chain
from django.contrib.auth import get_user_model

from enterprise_access.apps.workflows.constants import WorkflowStatus
from enterprise_access.apps.workflows.models import WorkflowExecutionStatus
from enterprise_access.apps.workflows.tasks import execute_workflow_step, handle_workflow_failure

logger = logging.getLogger(__name__)
User = get_user_model()


class WorkflowRunner:
    """
    A service to run a WorkflowDefinition and execute each of its steps in order.
    """
    def __init__(
            self,
            workflow_definition=None,
            lms_user_id=None,
            workflow_execution_status=None):
        # Lookup the user by the LMS ID, if provided.
        self.user = None
        self.lms_user_id = None
        if lms_user_id:
            self.lms_user_id = lms_user_id
            self.user = self.find_user_by_lms_user_id()

        # Initialize the workflow_execution_status and workflow_definition
        if workflow_execution_status is not None:
            self.workflow_execution_status = workflow_execution_status
            self.workflow_definition = workflow_execution_status.workflow_definition
        elif workflow_definition is not None:
            self.workflow_execution_status = self.create_workflow_execution_status(workflow_definition)
            self.workflow_definition = workflow_definition
        else:
            raise ValueError("Either workflow_definition or workflow_execution_status must be provided.")

    def find_user_by_lms_user_id(self):
        """
        Find the user associated with the LMS user ID.
        """
        try:
            return User.objects.get(lms_user_id=self.lms_user_id)
        except User.DoesNotExist as exc:
            raise ValueError(f"User with LMS ID {self.lms_user_id} not found.") from exc

    def create_workflow_execution_status(self, workflow_definition):
        """
        Create and return a WorkflowExecutionStatus instance to track the execution.
        """
        return WorkflowExecutionStatus.objects.create(
            workflow_definition=workflow_definition,
            user=self.user,
            status=WorkflowStatus.PENDING,
        )

    def run(self):
        """
        Executes the workflow by running each step in the correct order.
        """
        remaining_workflow_steps = self.workflow_execution_status.remaining_workflow_steps

        if not remaining_workflow_steps.exists():
            logger.info(
                f"No pending steps to run for workflow {self.workflow_definition.uuid} and "
                f"its workflow execution status {self.workflow_execution_status.uuid}"
            )
            self.workflow_execution_status.status = WorkflowStatus.COMPLETED
            self.workflow_execution_status.save()
            return

        logger.info(f"Starting workflow: {self.workflow_definition.name}")

        # Update the status to running
        self.workflow_execution_status.status = WorkflowStatus.IN_PROGRESS
        self.workflow_execution_status.save()

        task_chain = None

        # Iterate over each step in the workflow and create a task chain
        for step in remaining_workflow_steps:
            try:
                workflow_action_step_through = step.workflow_action_step_through.get(
                    workflow_definition=self.workflow_definition,
                    step=step,
                )
            except workflow_action_step_through.DoesNotExist as exc:
                raise ValueError(f"WorkflowGroupActionStepThrough does not exist for step {step.name}") from exc

            # Chain the execution of the workflow step's action_reference with a separate task to mark it as completed
            step_task = execute_workflow_step.s(
                workflow_action_step_through.id,
                step.action_reference,
                str(self.workflow_execution_status.uuid),
            )

            # Chain the remaining tasks
            if task_chain:
                task_chain |= step_task
            else:
                task_chain = step_task

        # Start the task chain asynchronously
        task_chain.apply_async(
            link_error=handle_workflow_failure.s(str(self.workflow_execution_status.uuid)),
        )
        logger.info(f"Task chain for workflow {self.workflow_execution_status.uuid} has been submitted.")
