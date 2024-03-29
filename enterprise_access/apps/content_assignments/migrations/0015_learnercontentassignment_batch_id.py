# Generated by Django 4.2.9 on 2024-01-09 23:19

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content_assignments', '0014_alter_historicalassignmentconfiguration_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicallearnercontentassignment',
            name='allocation_batch_id',
            field=models.UUIDField(blank=True, default=None, help_text='A reference to the batch that this assignment was created in. Helpful for grouping assignments together.', null=True),
        ),
        migrations.AddField(
            model_name='learnercontentassignment',
            name='allocation_batch_id',
            field=models.UUIDField(blank=True, default=None, help_text='A reference to the batch that this assignment was created in. Helpful for grouping assignments together.', null=True),
        ),
    ]
