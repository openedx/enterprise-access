# Generated by Django 4.2.6 on 2023-11-06 19:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content_assignments', '0010_alter_learnercontentassignmentaction_assignment'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='learnercontentassignmentaction',
            options={'ordering': ['created']},
        ),
        migrations.AlterField(
            model_name='historicallearnercontentassignmentaction',
            name='action_type',
            field=models.CharField(choices=[('learner_linked', 'Learner linked to customer'), ('notified', 'Learner notified of assignment'), ('reminded', 'Learner reminded about assignment'), ('cancelled', 'Learner assignment cancelled')], db_index=True, help_text='The type of action take on the related assignment record.', max_length=255),
        ),
        migrations.AlterField(
            model_name='learnercontentassignmentaction',
            name='action_type',
            field=models.CharField(choices=[('learner_linked', 'Learner linked to customer'), ('notified', 'Learner notified of assignment'), ('reminded', 'Learner reminded about assignment'), ('cancelled', 'Learner assignment cancelled')], db_index=True, help_text='The type of action take on the related assignment record.', max_length=255),
        ),
    ]
