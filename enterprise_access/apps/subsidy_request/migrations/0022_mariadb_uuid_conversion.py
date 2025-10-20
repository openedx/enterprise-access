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
        cursor.execute("ALTER TABLE subsidy_request_subsidyrequest MODIFY uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_subsidyrequest MODIFY enterprise_customer_uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_couponcoderequest MODIFY subscription_plan_uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_couponcoderequest MODIFY license_uuid uuid NULL")
        cursor.execute("ALTER TABLE subsidy_request_learnercreditrequest MODIFY enterprise_customer_uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_learnercreditrequest MODIFY uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_couponcoderequest MODIFY uuid uuid NOT NULL")


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
        cursor.execute("ALTER TABLE subsidy_request_subsidyrequest MODIFY uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_subsidyrequest MODIFY enterprise_customer_uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_couponcoderequest MODIFY subscription_plan_uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_couponcoderequest MODIFY license_uuid char(32) NULL")
        cursor.execute("ALTER TABLE subsidy_request_learnercreditrequest MODIFY enterprise_customer_uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_learnercreditrequest MODIFY uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE subsidy_request_couponcoderequest MODIFY uuid char(32) NOT NULL")


class Migration(migrations.Migration):
    dependencies = [
        ('subsidy_request', '0021_alter_historicallearnercreditrequestactions_error_reason_and_more'),
    ]
    operations = [
        migrations.RunPython(
            code=apply_mariadb_migration,
            reverse_code=reverse_mariadb_migration,
        ),
    ]
