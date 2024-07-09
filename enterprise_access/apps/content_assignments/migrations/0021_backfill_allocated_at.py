"""
Data migration to backfill the ``LearnerContentAssignment.allocated_at`` field.
"""

from django.db import migrations
from django.utils import timezone

from enterprise_access.apps.content_assignments.constants import AssignmentActions


BULK_OPERATION_BATCH_SIZE = 50


def forwards_func(apps, schema_editor):
    """
    Populates the ``allocated_at`` field for all assignments whose value of that field is null.
    """
    LearnerContentAssignment = apps.get_model('content_assignments', 'LearnerContentAssignment')
    HistoricalLearnerContentAssignment = apps.get_model('content_assignments', 'HistoricalLearnerContentAssignment')
    LearnerContentAssignmentAction = apps.get_model('content_assignments', 'LearnerContentAssignmentAction')

    records_to_backfill = LearnerContentAssignment.objects.filter(
        allocated_at=None,
    )

    records_to_save = []
    historical_records_to_save = []

    for assignment_record in records_to_backfill:
        last_notify_action = LearnerContentAssignmentAction.objects.filter(
            assignment=assignment_record,
            action_type=AssignmentActions.NOTIFIED,
            error_reason=None,
        ).first()
        if not last_notify_action:
            allocation_time = assignment_record.created
        else:
            allocation_time = last_notify_action.completed_at

        assignment_record.allocated_at = allocation_time
        assignment_record.modified = timezone.now()
        records_to_save.append(assignment_record)

        # Note: The reason we need to manually create historical objects is that Django's bulk_update() built-in does not
        # call post_save hooks, which is normally where history objects are created. Next you might ask why we don't just
        # use django-simple-history's bulk_update_with_history() utility function: that's because it attempts to access the
        # custom simple history model manager, but unfortunately custom model attributes are unavailable from migrations.
        historical_field_values = {
            field.name: getattr(assignment_record, field.name)
            for field in assignment_record._meta.fields
        }
        historical_record = HistoricalLearnerContentAssignment(
            history_date=timezone.now(),
            history_type='~',
            history_change_reason='Data migration to backfill `allocated_at` field',
            **historical_field_values,
        )
        historical_records_to_save.append(historical_record)

    LearnerContentAssignment.objects.bulk_update(
        records_to_save,
        ['allocated_at', 'modified'],
        batch_size=BULK_OPERATION_BATCH_SIZE,
    )
    HistoricalLearnerContentAssignment.objects.bulk_create(
        historical_records_to_save,
        batch_size=BULK_OPERATION_BATCH_SIZE,
    )


def reverse_func(apps, schema_editor):
    """
    This migration's reverse operation is a no-op.
    """
    pass


class Migration(migrations.Migration):
    """
    Migration for backfilling the ``LearnerContentAssignment.allocated_at`` field.
    """
    dependencies = [
        ('content_assignments', '0020_assignment_reversal'),
    ]

    operations = [
        migrations.RunPython(forwards_func, reverse_func),
    ]
