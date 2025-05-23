# Generated by Django 4.2.20 on 2025-03-17 16:04

from django.db import migrations, models
import django.utils.timezone
import jsonfield.fields
import model_utils.fields
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='TestSquaredWorkflowStep',
            fields=[
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('is_removed', models.BooleanField(default=False)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('input_data', jsonfield.fields.JSONField(blank=True, default=None)),
                ('output_data', jsonfield.fields.JSONField(blank=True, default=None, null=True)),
                ('succeeded_at', models.DateTimeField(blank=True, null=True)),
                ('failed_at', models.DateTimeField(blank=True, null=True)),
                ('exception_message', models.TextField(blank=True, null=True)),
                ('workflow_record_uuid', models.UUIDField(help_text='UUID of the workflow record')),
                ('preceding_step_uuid', models.UUIDField(help_text='UUID of the preceding workflow step record, if any', null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='TestTwoStepWorkflow',
            fields=[
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('is_removed', models.BooleanField(default=False)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('input_data', jsonfield.fields.JSONField(blank=True, default=None)),
                ('output_data', jsonfield.fields.JSONField(blank=True, default=None, null=True)),
                ('succeeded_at', models.DateTimeField(blank=True, null=True)),
                ('failed_at', models.DateTimeField(blank=True, null=True)),
                ('exception_message', models.TextField(blank=True, null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='TestWorkflow',
            fields=[
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('is_removed', models.BooleanField(default=False)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('input_data', jsonfield.fields.JSONField(blank=True, default=None)),
                ('output_data', jsonfield.fields.JSONField(blank=True, default=None, null=True)),
                ('succeeded_at', models.DateTimeField(blank=True, null=True)),
                ('failed_at', models.DateTimeField(blank=True, null=True)),
                ('exception_message', models.TextField(blank=True, null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='TestWorkflowStep',
            fields=[
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('is_removed', models.BooleanField(default=False)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('input_data', jsonfield.fields.JSONField(blank=True, default=None)),
                ('output_data', jsonfield.fields.JSONField(blank=True, default=None, null=True)),
                ('succeeded_at', models.DateTimeField(blank=True, null=True)),
                ('failed_at', models.DateTimeField(blank=True, null=True)),
                ('exception_message', models.TextField(blank=True, null=True)),
                ('workflow_record_uuid', models.UUIDField(help_text='UUID of the workflow record')),
                ('preceding_step_uuid', models.UUIDField(help_text='UUID of the preceding workflow step record, if any', null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
