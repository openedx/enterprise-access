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
        cursor.execute("ALTER TABLE content_assignments_assignment MODIFY uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE content_assignments_assignment MODIFY enterprise_customer_uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE content_assignments_learnercontentassignment MODIFY uuid uuid NOT NULL")
        cursor.execute("ALTER TABLE content_assignments_learnercontentassignment MODIFY transaction_uuid uuid NULL")
        cursor.execute("ALTER TABLE content_assignments_learnercontentassignment MODIFY allocation_batch_id uuid NULL")
        cursor.execute("ALTER TABLE content_assignments_assignmentconfiguration MODIFY uuid uuid NOT NULL")


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
        cursor.execute("ALTER TABLE content_assignments_assignment MODIFY uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE content_assignments_assignment MODIFY enterprise_customer_uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE content_assignments_learnercontentassignment MODIFY uuid char(32) NOT NULL")
        cursor.execute("ALTER TABLE content_assignments_learnercontentassignment MODIFY transaction_uuid char(32) NULL")
        cursor.execute("ALTER TABLE content_assignments_learnercontentassignment MODIFY allocation_batch_id char(32) NULL")
        cursor.execute("ALTER TABLE content_assignments_assignmentconfiguration MODIFY uuid char(32) NOT NULL")


class Migration(migrations.Migration):
    dependencies = [
        ('content_assignments', '0023_historicallearnercontentassignment_is_assigned_course_run_and_more'),
    ]
    operations = [
        migrations.RunPython(
            code=apply_mariadb_migration,
            reverse_code=reverse_mariadb_migration,
        ),
    ]
