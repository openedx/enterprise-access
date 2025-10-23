# Generated migration for MariaDB UUID field conversion (Django 5.2)
"""
Migration to convert UUIDField from char(32) to uuid type for MariaDB compatibility.
"""

from django.db import migrations


def apply_mariadb_migration(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'mysql':
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        if 'mariadb' not in version.lower():
            return
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicy MODIFY uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicy MODIFY enterprise_customer_uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicy MODIFY catalog_uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicy MODIFY subsidy_uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_groupassociation MODIFY enterprise_group_uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicyredemptioncount MODIFY uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicyredemptioncount MODIFY transaction_uuid uuid NOT NULL")


def reverse_mariadb_migration(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'mysql':
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        if 'mariadb' not in version.lower():
            return
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicy MODIFY uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicy MODIFY enterprise_customer_uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicy MODIFY catalog_uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicy MODIFY subsidy_uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_groupassociation MODIFY enterprise_group_uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicyredemptioncount MODIFY uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_access_policy_subsidyaccesspolicyredemptioncount MODIFY transaction_uuid char(32) NOT NULL")


class Migration(migrations.Migration):
    dependencies = [
        ('subsidy_access_policy', '0029_historicalsubsidyaccesspolicy_learner_credit_request_config_and_more'),
    ]
    operations = [
        migrations.RunPython(
            code=apply_mariadb_migration,
            reverse_code=reverse_mariadb_migration,
        ),
    ]
