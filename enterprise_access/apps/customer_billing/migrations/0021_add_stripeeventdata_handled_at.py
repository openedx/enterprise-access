from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('customer_billing', '0020_alter_stripeeventsummary_subscription_plan_uuid_help_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='stripeeventdata',
            name='handled_at',
            field=models.DateTimeField(null=True, blank=True, help_text='Timestamp when this Stripe event was successfully handled.'),
        ),
    ]
