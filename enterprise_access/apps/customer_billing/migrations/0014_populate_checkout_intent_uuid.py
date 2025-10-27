"""
Callable default on unique field checkoutintent.uuid will not generate unique values
upon migrating. This migration generates unique values described here:
https://docs.djangoproject.com/en/4.2/howto/writing-migrations/#migrations-that-add-unique-fields
"""
from django.db import migrations
import uuid


def gen_uuid(apps, schema_editor):
    CheckoutIntentClass = apps.get_model("customer_billing", "CheckoutIntent")
    for row in CheckoutIntentClass.objects.all():
        row.uuid = uuid.uuid4()
        row.save(update_fields=["uuid"])


class Migration(migrations.Migration):
    dependencies = [
        ("customer_billing", "0013_add_checkout_intent_uuid"),
    ]

    operations = [
        # No reverse operation
        migrations.RunPython(gen_uuid, reverse_code=migrations.RunPython.noop),
    ]
