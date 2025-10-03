"""
Django admin configuration for customer billing app.
"""
import stripe
from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from django.utils.html import format_html

from .constants import CheckoutIntentState
from .models import CheckoutIntent, StripeEventData
from .stripe_event_handlers import StripeEventHandler


class CheckoutIntentAdminForm(forms.ModelForm):
    """
    Admin form to help prevent duplicate CheckoutIntents from
    being created via the admin interface.
    """
    class Meta:
        model = CheckoutIntent
        fields = '__all__'

    def clean(self):
        """
        We override the clean() method to prevent conflicts with existing non-expired intents.
        """
        cleaned_data = super().clean()

        # If the form is valid so far, call the model's clean method
        if not self.errors:
            try:
                # Get the instance and update it with form data
                instance = self.instance if self.instance.pk else CheckoutIntent()

                # Update the instance with the form data (without saving)
                for field, value in cleaned_data.items():
                    setattr(instance, field, value)

                # Call the model's clean method
                instance.clean()

            except ValidationError as e:
                # Add model validation errors to the form
                # Django handles mapping field-specific errors automatically
                self.add_error(None, e)

        return cleaned_data


@admin.register(CheckoutIntent)
class CheckoutIntentAdmin(admin.ModelAdmin):
    """
    Admin interface for managing checkout intents.
    """
    form = CheckoutIntentAdminForm

    list_display = (
        'enterprise_name',
        'enterprise_slug',
        'user_email',
        'state_display',
        'quantity',
        'created',
        'time_remaining',
        'has_workflow',
        'country',
    )

    list_filter = (
        'state',
        'created',
        'expires_at',
    )

    search_fields = (
        'enterprise_slug',
        'enterprise_name',
        'stripe_checkout_session_id',
    )

    readonly_fields = (
        'created',
        'modified',
        'state_display',
        'time_remaining',
        'admin_portal_url_display',
        'stripe_session_link',
    )

    autocomplete_fields = [
        'user',
    ]

    ordering = ('-created',)

    fieldsets = (
        ('Enterprise Information', {
            'fields': (
                'enterprise_name',
                'enterprise_slug',
                'enterprise_uuid',
                'quantity',
                'admin_portal_url_display',
                'country',
            )
        }),
        ('Status', {
            'fields': (
                'user',
                'state',
                'state_display',
                'time_remaining',
                'expires_at',
            )
        }),
        ('Timestamps', {
            'fields': (
                'created',
                'modified',
            ),
            'classes': ('collapse',),
        }),
        ('Integration Details', {
            'fields': (
                'stripe_customer_id',
                'stripe_checkout_session_id',
                'stripe_session_link',
                'workflow',
            ),
            'classes': ('collapse',),
        }),
        ('Error Information', {
            'fields': (
                'last_checkout_error',
                'last_provisioning_error',
            ),
            'classes': ('collapse',),
        }),
    )

    actions = [
        'cleanup_expired_intents',
        'mark_as_expired',
    ]

    def user_email(self, obj):
        """Display user email in list view."""
        return obj.user.email
    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'

    def state_display(self, obj):
        """Display checkout state with color coding."""
        colors = {
            CheckoutIntentState.CREATED: 'blue',
            CheckoutIntentState.PAID: 'orange',
            CheckoutIntentState.FULFILLED: 'green',
            CheckoutIntentState.ERRORED_STRIPE_CHECKOUT: 'red',
            CheckoutIntentState.ERRORED_PROVISIONING: 'red',
            CheckoutIntentState.EXPIRED: 'gray',
        }

        color = colors.get(obj.state, 'black')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_state_display()
        )
    state_display.short_description = 'Status'

    def time_remaining(self, obj):
        """Show time remaining until expiration or expired time."""
        if obj.state != CheckoutIntentState.CREATED:
            return "-"

        if obj.is_expired():
            time_diff = timezone.now() - obj.expires_at
            return format_html(
                '<span style="color: red;">Expired {} ago</span>',
                self._format_timedelta(time_diff)
            )
        elif obj.expires_at:
            time_diff = obj.expires_at - timezone.now()
            return format_html(
                '<span style="color: green;">{} remaining</span>',
                self._format_timedelta(time_diff)
            )
        return None
    time_remaining.short_description = 'Time Remaining'

    def admin_portal_url_display(self, obj):
        """Display admin portal URL if available."""
        url = obj.admin_portal_url
        if url:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener">Open Portal</a>',
                url
            )
        return "-"
    admin_portal_url_display.short_description = 'Admin Portal'

    def stripe_session_link(self, obj):
        """Create a link to Stripe dashboard if session ID exists."""
        if not obj.stripe_checkout_session_id:
            return '-'

        stripe_url = f"https://dashboard.stripe.com/checkout/sessions/{obj.stripe_checkout_session_id}"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">View in Stripe</a>',
            stripe_url
        )
    stripe_session_link.short_description = 'Stripe Session'

    def has_workflow(self, obj):
        """Display if a workflow exists for this intent."""
        return bool(obj.workflow)

    has_workflow.short_description = 'Has Workflow'
    has_workflow.boolean = True

    def _format_timedelta(self, td):
        """Format timedelta for human reading."""
        total_seconds = int(td.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def get_queryset(self, request):
        """Optimize queries by selecting related user."""
        return super().get_queryset(request).select_related('user', 'workflow')

    # Custom admin actions

    @admin.action(description='Mark selected intents as expired')
    def mark_as_expired(self, request, queryset):
        """Admin action to manually mark intents as expired."""
        updated = queryset.update(state=CheckoutIntentState.EXPIRED)
        self.message_user(
            request,
            f"Marked {updated} intents as expired."
        )

    @admin.action(description='Clean up expired intents')
    def cleanup_expired_intents(self, request, queryset):  # pylint: disable=unused-argument
        """Admin action to clean up expired intents."""
        call_command('cleanup_checkout_intents')
        self.message_user(
            request,
            "Cleanup command executed successfully. Check server logs for details."
        )


@admin.register(StripeEventData)
class StripeEventDataAdmin(admin.ModelAdmin):
    """
    The admin class for StripeEventData.
    """
    list_display = [
        'event_id',
        'event_type',
        'created',
        'modified',
        'checkout_intent_id',
    ]
    list_filter = [
        'event_type',
    ]
    search_fields = [
        'event_id',
    ]
    actions = ['handle_event']
    select_related = ['checkout_intent']

    def checkout_intent_id(self, obj):
        return obj.checkout_intent.id if obj.checkout_intent else None

    @admin.action(description='Handle the selected events')
    def handle_event(self, request, queryset):
        for obj in queryset:
            event = stripe.Event.construct_from(obj.data, stripe.api_key)
            StripeEventHandler.dispatch(event)
