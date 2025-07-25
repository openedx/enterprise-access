# Generated by Django 4.2.21 on 2025-06-30 09:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subsidy_request', '0019_rename_couponcoderequest_uuid_state_subsidy_req_uuid_8c5efe_idx_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historicallearnercreditrequestactions',
            name='error_reason',
            field=models.CharField(blank=True, choices=[('failed_approval', 'Failed: Approval'), ('failed_decline', 'Failed: Decline'), ('failed_cancellation', 'Failed: Cancellation'), ('failed_redemption', 'Failed: Redemption'), ('failed_reversal', 'Failed: Reversal')], db_index=True, help_text='The type of error that occurred during the action, if any.', max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name='learnercreditrequestactions',
            name='error_reason',
            field=models.CharField(blank=True, choices=[('failed_approval', 'Failed: Approval'), ('failed_decline', 'Failed: Decline'), ('failed_cancellation', 'Failed: Cancellation'), ('failed_redemption', 'Failed: Redemption'), ('failed_reversal', 'Failed: Reversal')], db_index=True, help_text='The type of error that occurred during the action, if any.', max_length=255, null=True),
        ),
    ]
