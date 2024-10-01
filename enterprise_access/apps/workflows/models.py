"""
Models for the workflows app.
"""

import collections
import importlib
import logging
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.urls import reverse
from django_extensions.db.models import TimeStampedModel
from jsonfield.encoder import JSONEncoder
from jsonfield.fields import JSONField
from ordered_model.models import OrderedModel, OrderedModelManager, OrderedModelQuerySet

from enterprise_access.apps.workflows.constants import WorkflowStatus
from enterprise_access.apps.workflows.registry import WorkflowActionStepRegistry
from enterprise_access.utils import localized_utcnow

logger = logging.getLogger(__name__)


def ensure_workflow_execution_update(method):
    """Decorator to ensure that `update_workflow_execution` is called after the method."""
    def wrapper(self, *args, **kwargs):
        result = method(self, *args, **kwargs)  # Call the original method
        self.update_workflow_execution()  # Ensure the workflow execution is updated
        return result
    return wrapper


class WorkflowExecutionStepStatus(TimeStampedModel):
    """
    Tracks the execution status and progress of an individual workflow step during a workflow execution.
    This includes the current status, timestamps, results, and errors for each step of a specific workflow run.
    """

    # Foreign key to the workflow execution
    workflow_execution = models.ForeignKey(
        'WorkflowExecutionStatus',
        on_delete=models.CASCADE,
        related_name='step_statuses',
    )

    # Foreign key to the step being executed
    step = models.ForeignKey(
        'WorkflowActionStep',
        on_delete=models.CASCADE,
        related_name='workflow_execution_statuses',
    )

    # Status of the step within this execution, using the shared WorkflowStatus enum
    status = models.CharField(
        max_length=20,
        choices=WorkflowStatus.choices,
        default=WorkflowStatus.PENDING
    )

    # Timestamps for tracking when the step started, completed, or failed
    started_at = models.DateTimeField(null=True, blank=True, help_text="When the step started")
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the step was completed (e.g., succeeded or failed)"
    )

    # Result of the step execution (could be any output data or success message)
    result = JSONField(
        blank=True,
        null=True,
        load_kwargs={'object_pairs_hook': collections.OrderedDict},
        dump_kwargs={'indent': 4, 'cls': JSONEncoder, 'separators': (',', ':')},
        help_text="Stores the result of the step execution",
    )

    # Error message in case the step fails
    error_message = models.TextField(null=True, blank=True, help_text="Error message if the step failed")

    # Store the Celery task ID or other metadata
    task_id = models.CharField(max_length=255, null=True, blank=True, help_text="Task ID for async tracking")

    # Optional metadata related to the step execution (e.g., logs or other details)
    metadata = JSONField(
        blank=True,
        null=True,
        load_kwargs={'object_pairs_hook': collections.OrderedDict},
        dump_kwargs={'indent': 4, 'cls': JSONEncoder, 'separators': (',', ':')},
        help_text="Additional metadata related to the step execution",
    )

    class Meta:
        verbose_name_plural = "Workflow execution step statuses"

    @transaction.atomic
    def update_workflow_execution(self):
        """
        Updates the workflow execution by evaluating the current step and tracking the executed steps.
        """
        self.workflow_execution.current_step = self
        self.workflow_execution.executed_steps.add(self)
        self.workflow_execution.save()

    @ensure_workflow_execution_update
    @transaction.atomic
    def mark_in_progress(self):
        """Marks the step as 'in progress' and sets the started_at timestamp."""
        self.status = WorkflowStatus.IN_PROGRESS
        self.started_at = localized_utcnow()
        self.save()
        logger.info(f"Step {self.step.name} started for workflow {self.workflow_execution.workflow_definition.uuid}")

    @ensure_workflow_execution_update
    @transaction.atomic
    def mark_completed(self, task_id=None, result=None):
        """Marks the step as 'completed' and sets the completed_at timestamp."""
        self.status = WorkflowStatus.COMPLETED
        self.ended_at = localized_utcnow()
        if task_id is not None:
            self.task_id = task_id
        if result is not None:
            self.result = result
        self.save()
        logger.info(f"Step {self.step.name} completed for workflow {self.workflow_execution.workflow_definition.uuid}")

    @ensure_workflow_execution_update
    @transaction.atomic
    def mark_failed(self, task_id=None, exc=None):
        """Marks the step as 'failed' and sets the failed_at timestamp."""
        self.status = WorkflowStatus.FAILED
        self.ended_at = localized_utcnow()
        if task_id is not None:
            self.task_id = task_id
        if exc is not None:
            self.error_message = str(exc)
        self.save()
        logger.error(f"Step {self.step.name} failed for workflow {self.workflow_execution.workflow_definition.uuid}")

    @ensure_workflow_execution_update
    @transaction.atomic
    def mark_skipped(self):
        """Marks the step as 'skipped'."""
        self.status = WorkflowStatus.SKIPPED
        self.ended_at = localized_utcnow()
        self.save()
        logger.info(f"Step {self.step.name} skipped for workflow {self.workflow_execution.workflow_definition.uuid}")

    @ensure_workflow_execution_update
    @transaction.atomic
    def mark_aborted(self):
        """Marks the step as 'aborted'."""
        self.status = WorkflowStatus.ABORTED
        self.ended_at = localized_utcnow()
        self.save()
        logger.info(f"Step {self.step.name} aborted for workflow {self.workflow_execution.workflow_definition.uuid}")

    def __str__(self):
        return f"<WorkflowExecutionStepStatus> Step: {self.step.name} " \
               f"in {self.workflow_execution.workflow_definition.name} - Status: {self.status}"


class WorkflowExecutionStatus(TimeStampedModel):
    """
    Tracks the execution status and progress of a workflow.
    This includes the current step, status of the execution, and completion time.
    """

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
        help_text='The uuid that uniquely identifies this workflow execution status.',
    )

    workflow_definition = models.ForeignKey(
        'WorkflowDefinition',
        on_delete=models.CASCADE,
        related_name='workflow_executions',
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Optionally associate this workflow execution with a user.",
        related_name='workflow_executions',
    )

    current_step = models.ForeignKey(
        'WorkflowExecutionStepStatus',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='current_workflow_executions',
    )

    executed_steps = models.ManyToManyField(
        'WorkflowExecutionStepStatus',
        related_name="executed_workflow_executions",
        blank=True,
        help_text="Steps that have already been successfully executed."
    )

    status = models.CharField(
        max_length=20,
        choices=WorkflowStatus.choices,
        default=WorkflowStatus.PENDING,
    )

    @property
    def admin_change_url(self):
        """
        Returns the URL to the admin change page for the instance.
        """
        return reverse('admin:workflows_workflowexecutionstatus_change', args=[self.pk])

    @property
    def remaining_workflow_steps(self):
        """
        Get all workflow steps that have not yet been successfully executed.
        """
        # Get the executed steps via WorkflowExecutionStepStatus
        executed_steps_status = (
            self.step_statuses.select_related('step')
            .values_list('step_id', flat=True)
        )

        # If no steps have been executed, return all steps
        if not executed_steps_status:
            return self.workflow_definition.workflow_steps.prefetch_related('workflow_action_step_through')

        # Use the through model to order the remaining steps and exclude executed ones
        remaining_steps = (
            WorkflowGroupActionStepThrough.objects
            .filter(workflow_definition=self.workflow_definition)
            .exclude(step_id__in=executed_steps_status)
            .select_related('step')
            .order_by('order')
        ).values_list('step', flat=True)  # Extract the actual steps

        return (
            self.workflow_definition.workflow_steps
            .filter(id__in=remaining_steps)
            .prefetch_related('workflow_action_step_through')
        )

    class Meta:
        verbose_name_plural = "Workflow execution statuses"

    def get_group_status(self, group):
        """
        Returns the inferred status of a WorkflowStepGroup based on its steps.
        """
        step_statuses = self.step_statuses.filter(step__in=group.workflow_action_steps.all())

        if step_statuses.filter(status=WorkflowStatus.FAILED).exists():
            return WorkflowStatus.FAILED
        elif step_statuses.filter(status=WorkflowStatus.IN_PROGRESS).exists():
            return WorkflowStatus.IN_PROGRESS
        elif step_statuses.count() == step_statuses.filter(status=WorkflowStatus.COMPLETED).count():
            return WorkflowStatus.COMPLETED
        else:
            return WorkflowStatus.PENDING

    def update_execution_status(self):
        """
        Updates the overall workflow execution status by evaluating the groups (or steps).
        """
        # Get all step groups in the workflow
        step_groups = self.workflow_definition.step_groups.all()

        for group in step_groups:
            group_status = self.get_group_status(group)

            if group_status == WorkflowStatus.FAILED:
                self.status = WorkflowStatus.FAILED
                self.save()
                return  # Exit early if any group has failed

            elif group_status == WorkflowStatus.IN_PROGRESS:
                self.status = WorkflowStatus.IN_PROGRESS
                self.save()

        # If all groups (or steps if no groups) are completed, mark workflow as completed
        if all(self.get_group_status(group) == WorkflowStatus.COMPLETED for group in step_groups):
            self.status = WorkflowStatus.COMPLETED
            self.save()

    def mark_completed(self):
        self.status = WorkflowStatus.COMPLETED
        self.save()
        logger.info(f"WorkflowExecutionStatus {self.uuid} completed.")

    def mark_failed(self):
        self.status = WorkflowStatus.FAILED
        self.save()
        logger.info(f"WorkflowExecutionStatus {self.uuid} failed.")

    def __str__(self):
        return f"<WorkflowExecutionStatus> of workflow {self.workflow_definition.uuid} is {self.status}"


class WorkflowActionStep(TimeStampedModel):
    """
    Represents a single step (action) in a workflow.
    Each step is tied to a specific action (i.e., a function) and
    ordered relative to other steps in the workflow.
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
        help_text='The uuid that uniquely identifies this workflow action step.',
    )
    name = models.CharField(max_length=255, help_text="The name of the step.")
    action_reference = models.CharField(
        max_length=255,
        help_text="Reference to the action (function) executed by this step."
    )

    def clean(self):
        """
        Validates that the action reference exists in the registry.
        """
        if not WorkflowActionStepRegistry.get(self.action_reference):
            raise ValidationError(f"Action reference '{self.action_reference}' is not registered.")

    def __str__(self):
        """Returns the step name and its action reference."""
        return f"{self.name} (Action: {self.action_reference})"


class WorkflowDefinitionQuerySet(OrderedModelQuerySet):
    """
    Custom queryset for WorkflowDefinition to handle filtering by active status.
    """

    def active(self):
        """
        Filters the workflows that are active.
        :return: A queryset of active workflows.
        """
        return self.filter(is_active=True)


class WorkflowDefinitionManager(OrderedModelManager):
    """
    Custom manager for WorkflowDefinition to handle querying workflows for an enterprise customer.
    """

    def get_queryset(self):
        """
        Returns a WorkflowDefinitionQuerySet, enabling additional query methods such as active status filtering.
        :return: A WorkflowDefinitionQuerySet.
        """
        return WorkflowDefinitionQuerySet(self.model, using=self._db)

    def get_all_for_enterprise_customer(self, enterprise_customer_uuid):
        """
        Returns all workflows (default, shared, and custom) for a specific enterprise customer.
        :param enterprise_customer_uuid: The UUID of the enterprise customer.
        :return: A queryset of all workflows related to the enterprise customer.
        """
        is_default = Q(is_default=True)
        is_shared = Q(shared_workflow_enterprise_customers__enterprise_customer_uuid=enterprise_customer_uuid)
        is_custom = Q(enterprise_customer_uuid=enterprise_customer_uuid)
        return self.active().filter(is_default | is_shared | is_custom).distinct()


class WorkflowDefinition(TimeStampedModel):
    """
    Defines a reusable workflow structure.
    A workflow can be:
    - Default: Applies to all enterprise customers.
    - Shared: Shared across multiple enterprise customers.
    - Custom: Tailored to specific enterprise customers.
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
        help_text='The uuid that uniquely identifies this workflow definition.',
    )
    name = models.CharField(max_length=255, help_text="The name of the workflow.")
    is_active = models.BooleanField(default=True, help_text="Indicates if this workflow is active.")
    is_default = models.BooleanField(default=False, help_text="Indicates if this workflow applies to all customers.")
    enterprise_customer_uuid = models.UUIDField(
        null=True,
        blank=True,
        help_text="UUID of the enterprise customer this custom workflow is for.",
    )
    objects = WorkflowDefinitionManager.from_queryset(WorkflowDefinitionQuerySet)()

    @property
    def is_shared(self):
        """
        Returns True if the workflow is shared with multiple enterprise customers.
        """
        return WorkflowEnterpriseCustomer.objects.filter(workflow_definition=self).exists()

    @property
    def is_custom(self):
        """
        Returns True if the workflow is custom for a specific enterprise customer.
        """
        return bool(self.enterprise_customer_uuid)

    @property
    def workflow_steps(self):
        """
        Returns a queryset of all steps in the workflow, sorted by their order in the workflow.
        """
        return (
            self.workflow_items
            .select_related('action_step', 'step_group')
            .prefetch_related('action_step__workflow_action_step_through', 'step_group__workflow_action_step_through')
            .order_by('order')
        )

    def __str__(self):
        """Returns a string representation of the workflow, including its type (default, shared, or custom)."""
        if self.is_default:
            return f"<WorkflowDefinition> Default Workflow: {self.name}"
        elif self.is_custom:
            return f"<WorkflowDefinition> Custom Workflow for Enterprise " \
                   f"Customer UUID: {self.enterprise_customer_uuid}: {self.name}"
        elif self.is_shared:
            return f"<WorkflowDefinition> Shared Workflow: {self.name}"
        else:
            return f"<WorkflowDefinition>: {self.name}"

    def clean(self):
        """
        Ensures that the workflow configuration is valid:
        - Default workflows cannot be shared or custom.
        - A workflow cannot be both shared and custom simultaneously.
        """
        is_shared_workflow = WorkflowEnterpriseCustomer.objects.filter(workflow_definition=self).exists()
        if self.is_default and (self.enterprise_customer_uuid or is_shared_workflow):
            raise ValidationError('A default workflow cannot be associated with specific customers.')

        if self.enterprise_customer_uuid and is_shared_workflow:
            raise ValidationError(
                'A workflow cannot be both custom for one customer and shared across multiple customers.'
            )

        super().clean()


class WorkflowStepGroupQuerySet(OrderedModelQuerySet):
    """
    Custom queryset for WorkflowStepGroup to handle any necessary queries.
    """


class WorkflowStepGroupManager(OrderedModelManager):
    """
    Custom manager for WorkflowStepGroup to handle any necessary queries.
    """

    def get_queryset(self):
        """
        Returns a WorkflowStepGroupQuerySet, enabling additional query methods.
        :return: A WorkflowStepGroupQuerySet.
        """
        return WorkflowStepGroupQuerySet(self.model, using=self._db)


class WorkflowStepGroup(TimeStampedModel):
    """
    Represents a group of steps that can be run in parallel within a workflow.
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
        help_text='The uuid that uniquely identifies this workflow step group.',
    )
    name = models.CharField(max_length=255)
    run_in_parallel = models.BooleanField(
        default=True,
        help_text="Indicates if the steps in this group should run in parallel."
    )

    objects = WorkflowStepGroupManager.from_queryset(WorkflowStepGroupQuerySet)()

    def __str__(self):
        return f"<WorkflowStepGroup> {self.name}"


class WorkflowGroupActionStepThrough(OrderedModel, TimeStampedModel):
    """
    Through model to handle ordering of WorkflowActionSteps within WorkflowStepGroups.
    """
    step_group = models.ForeignKey(
        'WorkflowStepGroup',
        on_delete=models.CASCADE,
        related_name='steps',
        help_text="The group containing steps"
    )
    step = models.ForeignKey(
        'WorkflowActionStep',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='groups',
        help_text="An individual action step"
    )
    group = models.ForeignKey(
        'WorkflowStepGroup',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="The sub-group within this parent group"
    )
    order_with_respect_to = 'step_group'

    class Meta(OrderedModel.Meta):
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(
                fields=['step_group', 'step'],
                name='unique_step_in_group'
            ),
            models.UniqueConstraint(
                fields=['step_group', 'group'],
                name='unique_group_in_group'
            ),
        ]

    def clean(self):
        """
        Ensure that either step or group is populated, but not both.
        """
        if self.step and self.group:
            raise ValidationError('Only one of step or group can be set, not both.')
        if not self.step and not self.group:
            raise ValidationError('Either action_step or sub_group must be set.')

    def __str__(self):
        return f"{self.id}"


class WorkflowItemThrough(OrderedModel, TimeStampedModel):
    """
    Unified model to order both WorkflowActionSteps and WorkflowStepGroups within a WorkflowDefinition.
    Either action_step or step_group should be populated, not both.
    """
    workflow_definition = models.ForeignKey(
        'WorkflowDefinition',
        on_delete=models.CASCADE,
        related_name='workflow_items'
    )
    action_step = models.ForeignKey(
        'WorkflowActionStep',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="An individual action step"
    )
    step_group = models.ForeignKey(
        'WorkflowStepGroup',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="A group of steps"
    )
    order_with_respect_to = 'workflow_definition'

    def clean(self):
        """
        Ensure that either action_step or step_group is populated, but not both.
        """
        if self.action_step and self.step_group:
            raise ValidationError('Only one of action_step or step_group can be set, not both.')
        if not self.action_step and not self.step_group:
            raise ValidationError('Either action_step or step_group must be set.')

    class Meta(OrderedModel.Meta):
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(
                fields=['workflow_definition', 'action_step'],
                name='unique_action_step_in_workflow'
            ),
            models.UniqueConstraint(
                fields=['workflow_definition', 'step_group'],
                name='unique_step_group_in_workflow'
            ),
        ]


class WorkflowEnterpriseCustomer(models.Model):
    """
    Represents a relationship between a workflow definition and an enterprise customer UUID for shared workflows.
    This model stores the UUIDs of enterprise customers that a specific workflow is shared with.
    """
    workflow_definition = models.ForeignKey(
        'WorkflowDefinition',
        on_delete=models.CASCADE,
        related_name='shared_workflow_enterprise_customers'
    )
    enterprise_customer_uuid = models.UUIDField()

    class Meta:
        unique_together = ('workflow_definition', 'enterprise_customer_uuid')

    def clean(self):
        """
        Validates the shared workflow relationship.
        """
        if self.workflow_definition.is_default:
            raise ValidationError(
                f"Cannot associate default workflow '{self.workflow_definition.name}' to a specific EnterpriseCustomer."
            )
        super().clean()

    def __str__(self):
        return f"<WorkflowEnterpriseCustomer> {self.workflow_definition.name} shared with " \
               f"Enterprise Customer UUID: {self.enterprise_customer_uuid}"
