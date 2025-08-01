# Generated by Django 4.2.21 on 2025-07-29 18:53

from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields
import re
import simple_history.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('provisioning', '0005_triggerprovisionsubscriptiontrialcustomerworkflow'),
        ('customer_billing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalCheckoutIntent',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('state', models.CharField(choices=[('created', 'Created'), ('paid', 'Paid'), ('fulfilled', 'Fulfilled'), ('errored_stripe_checkout', 'Errored (Stripe Checkout)'), ('errored_provisioning', 'Errored (Provisioning)'), ('expired', 'Expired')], default='created', max_length=255)),
                ('enterprise_name', models.CharField(help_text='Checkout intent enterprise customer name', max_length=255)),
                ('enterprise_slug', models.SlugField(help_text='Checkout intent enterprise customer slug', max_length=255, validators=[django.core.validators.RegexValidator(re.compile('^[-a-zA-Z0-9_]+\\Z'), 'Enter a valid “slug” consisting of letters, numbers, underscores or hyphens.', 'invalid')])),
                ('expires_at', models.DateTimeField(db_index=True, help_text='Checkout intent expiration timestamp')),
                ('stripe_checkout_session_id', models.CharField(blank=True, db_index=True, help_text='Associated Stripe checkout session ID', max_length=255, null=True)),
                ('quantity', models.PositiveIntegerField(help_text='How many licenses to create.')),
                ('last_checkout_error', models.TextField(blank=True, null=True)),
                ('last_provisioning_error', models.TextField(blank=True, null=True)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField()),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('workflow', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='provisioning.provisionnewcustomerworkflow')),
            ],
            options={
                'verbose_name': 'historical Enterprise Checkout Intent',
                'verbose_name_plural': 'historical Enterprise Checkout Intents',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='CheckoutIntent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('state', models.CharField(choices=[('created', 'Created'), ('paid', 'Paid'), ('fulfilled', 'Fulfilled'), ('errored_stripe_checkout', 'Errored (Stripe Checkout)'), ('errored_provisioning', 'Errored (Provisioning)'), ('expired', 'Expired')], default='created', max_length=255)),
                ('enterprise_name', models.CharField(help_text='Checkout intent enterprise customer name', max_length=255)),
                ('enterprise_slug', models.SlugField(help_text='Checkout intent enterprise customer slug', max_length=255, validators=[django.core.validators.RegexValidator(re.compile('^[-a-zA-Z0-9_]+\\Z'), 'Enter a valid “slug” consisting of letters, numbers, underscores or hyphens.', 'invalid')])),
                ('expires_at', models.DateTimeField(db_index=True, help_text='Checkout intent expiration timestamp')),
                ('stripe_checkout_session_id', models.CharField(blank=True, db_index=True, help_text='Associated Stripe checkout session ID', max_length=255, null=True)),
                ('quantity', models.PositiveIntegerField(help_text='How many licenses to create.')),
                ('last_checkout_error', models.TextField(blank=True, null=True)),
                ('last_provisioning_error', models.TextField(blank=True, null=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('workflow', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='provisioning.provisionnewcustomerworkflow')),
            ],
            options={
                'verbose_name': 'Enterprise Checkout Intent',
                'verbose_name_plural': 'Enterprise Checkout Intents',
                'indexes': [models.Index(fields=['state'], name='customer_bi_state_50f734_idx'), models.Index(fields=['enterprise_slug'], name='customer_bi_enterpr_19a87f_idx'), models.Index(fields=['enterprise_name'], name='customer_bi_enterpr_1eb230_idx'), models.Index(fields=['expires_at'], name='customer_bi_expires_97774f_idx'), models.Index(fields=['stripe_checkout_session_id'], name='customer_bi_stripe__1e3320_idx')],
            },
        ),
    ]
