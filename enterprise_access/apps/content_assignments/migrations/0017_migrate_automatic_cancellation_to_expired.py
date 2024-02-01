from django.db import migrations, models

from enterprise_access.apps.content_assignments.constants import AssignmentActions, LearnerContentAssignmentStateChoices


def automatic_cancellation_to_expired_state(apps, schema_editor):
    """
    Migrate existing LearnerContentAssignmentAction records with the now-removed "automatic_cancellation" action type
    in the cancelled state to utilize the new "expired" action type and assignment state.
    """
    LearnerContentAssignmentAction = apps.get_model('content_assignments', 'LearnerContentAssignmentAction')
    actions_automatically_cancelled = LearnerContentAssignmentAction.objects.filter(
        action_type='automatic_cancellation',
        assignment__state=LearnerContentAssignmentStateChoices.CANCELLED,
    )
    for action in actions_automatically_cancelled:
        action.action_type = AssignmentActions.EXPIRED
        action.assignment.state = LearnerContentAssignmentStateChoices.EXPIRED
        action.assignment.save()

    LearnerContentAssignmentAction.objects.bulk_update(actions_automatically_cancelled, ['action_type'])

def expired_state_to_automatic_cancellation(apps, schema_editor):
    """
    Reverses the migration of existing LearnerContentAssignmentAction records with the "automatic_cancellation"
    action type to utilize the new "expired" action type and assignment state.
    """
    LearnerContentAssignmentAction = apps.get_model('content_assignments', 'LearnerContentAssignmentAction')
    actions_automatically_cancelled = LearnerContentAssignmentAction.objects.filter(
        action_type=AssignmentActions.EXPIRED,
        assignment__state=LearnerContentAssignmentStateChoices.EXPIRED,
    )
    for action in actions_automatically_cancelled:
        action.action_type = 'automatic_cancellation'
        action.assignment.state = LearnerContentAssignmentStateChoices.CANCELLED
        action.assignment.save()

    LearnerContentAssignmentAction.objects.bulk_update(actions_automatically_cancelled, ['action_type'])


class Migration(migrations.Migration):

    dependencies = [
        ('content_assignments', '0016_assignment_error_state_with_state_timestamps'),
    ]
        

    operations = [
        migrations.RunPython(
            code=automatic_cancellation_to_expired_state,
            reverse_code=expired_state_to_automatic_cancellation,
        )
    ]
