# Generated by Django 4.2.20 on 2025-04-13 18:33

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('subsidy_request', '0014_learnercreditrequestconfiguration_and_more'),
        ('subsidy_access_policy', '0028_alter_historicalsubsidyaccesspolicy_retired_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicalsubsidyaccesspolicy',
            name='learner_credit_request_config',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='subsidy_request.learnercreditrequestconfiguration'),
        ),
        migrations.AddField(
            model_name='subsidyaccesspolicy',
            name='learner_credit_request_config',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='learner_credit_config', to='subsidy_request.learnercreditrequestconfiguration'),
        ),
    ]
