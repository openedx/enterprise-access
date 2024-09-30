from django.db import models


class WorkflowStatus(models.TextChoices):
    """
    Enum-style class for representing the different possible statuses of a workflow.
    This can be used in models to set a 'status' field.
    """
    PENDING = 'pending', 'Pending'
    IN_PROGRESS = 'in_progress', 'In Progress'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'
    ABORTED = 'aborted', 'Aborted'
    SKIPPED = 'skipped', 'Skipped'
