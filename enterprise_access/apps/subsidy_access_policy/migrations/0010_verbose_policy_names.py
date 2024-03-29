# Generated by Django 3.2.19 on 2023-06-08 14:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subsidy_access_policy', '0009_customer_uuid_required_and_help_text'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historicalsubsidyaccesspolicy',
            name='per_learner_enrollment_limit',
            field=models.IntegerField(blank=True, default=None, help_text='The maximum number of enrollments allowed for a single learner under this policy. Defaults to null, which means that no such maximum exists.', null=True, verbose_name='Per-learner enrollment limit'),
        ),
        migrations.AlterField(
            model_name='historicalsubsidyaccesspolicy',
            name='per_learner_spend_limit',
            field=models.IntegerField(blank=True, default=None, help_text='The maximum amount of allowed money spent for a single learner under this policy. Denoted in USD cents. Defaults to null, which means that no such maximum exists.', null=True, verbose_name='Per-learner spend limit (USD cents)'),
        ),
        migrations.AlterField(
            model_name='historicalsubsidyaccesspolicy',
            name='spend_limit',
            field=models.IntegerField(blank=True, default=None, help_text='The maximum number of allowed dollars to be spent, in aggregate, by all learners under this policy. Denoted in USD cents. Defaults to null, which means that no such maximum exists.', null=True, verbose_name='Policy-wide spend limit (USD cents)'),
        ),
        migrations.AlterField(
            model_name='subsidyaccesspolicy',
            name='per_learner_enrollment_limit',
            field=models.IntegerField(blank=True, default=None, help_text='The maximum number of enrollments allowed for a single learner under this policy. Defaults to null, which means that no such maximum exists.', null=True, verbose_name='Per-learner enrollment limit'),
        ),
        migrations.AlterField(
            model_name='subsidyaccesspolicy',
            name='per_learner_spend_limit',
            field=models.IntegerField(blank=True, default=None, help_text='The maximum amount of allowed money spent for a single learner under this policy. Denoted in USD cents. Defaults to null, which means that no such maximum exists.', null=True, verbose_name='Per-learner spend limit (USD cents)'),
        ),
        migrations.AlterField(
            model_name='subsidyaccesspolicy',
            name='spend_limit',
            field=models.IntegerField(blank=True, default=None, help_text='The maximum number of allowed dollars to be spent, in aggregate, by all learners under this policy. Denoted in USD cents. Defaults to null, which means that no such maximum exists.', null=True, verbose_name='Policy-wide spend limit (USD cents)'),
        ),
    ]
