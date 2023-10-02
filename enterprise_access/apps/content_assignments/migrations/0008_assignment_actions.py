# Generated by Django 3.2.21 on 2023-10-02 16:18

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields
import simple_history.models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('content_assignments', '0007_index_assignment_state'),
    ]

    operations = [
        migrations.CreateModel(
            name='LearnerContentAssignmentAction',
            fields=[
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('action_type', models.CharField(choices=[('learner_linked', 'Learner linked to customer'), ('notified', 'Learner notified of assignment'), ('reminded', 'Learner reminded about assignment')], db_index=True, help_text='The type of action take on the related assignment record.', max_length=255)),
                ('completed_at', models.DateTimeField(blank=True, help_text='The time at which the action was successfully completed.', null=True)),
                ('error_reason', models.CharField(blank=True, choices=[('email_error', 'Email error'), ('internal_api_error', 'Internal API error')], db_index=True, help_text='The type of error that occurred during the action, if any.', max_length=255, null=True)),
                ('traceback', models.TextField(blank=True, editable=False, help_text='Any traceback we recorded when an error was encountered.', null=True)),
                ('assignment', models.ForeignKey(help_text='The LearnerContentAssignment on which this action was performed.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='actions', to='content_assignments.learnercontentassignment')),
            ],
            options={
                'get_latest_by': 'modified',
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='HistoricalLearnerContentAssignmentAction',
            fields=[
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('uuid', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False)),
                ('action_type', models.CharField(choices=[('learner_linked', 'Learner linked to customer'), ('notified', 'Learner notified of assignment'), ('reminded', 'Learner reminded about assignment')], db_index=True, help_text='The type of action take on the related assignment record.', max_length=255)),
                ('completed_at', models.DateTimeField(blank=True, help_text='The time at which the action was successfully completed.', null=True)),
                ('error_reason', models.CharField(blank=True, choices=[('email_error', 'Email error'), ('internal_api_error', 'Internal API error')], db_index=True, help_text='The type of error that occurred during the action, if any.', max_length=255, null=True)),
                ('traceback', models.TextField(blank=True, editable=False, help_text='Any traceback we recorded when an error was encountered.', null=True)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField()),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('assignment', models.ForeignKey(blank=True, db_constraint=False, help_text='The LearnerContentAssignment on which this action was performed.', null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='content_assignments.learnercontentassignment')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'historical learner content assignment action',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': 'history_date',
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
