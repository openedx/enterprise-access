"""
Django admin configuration for customer billing app.
"""
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import EnterpriseSlugReservation


@admin.register(EnterpriseSlugReservation)
class EnterpriseSlugReservationAdmin(admin.ModelAdmin):
    """
    Admin interface for managing enterprise slug reservations.
    """
    list_display = (
        'slug',
        'user_email',
        'status_display',
        'created',
        'expires_at',
        'time_remaining',
    )

    list_filter = (
        'created',
        'expires_at',
    )

    search_fields = (
        'slug',
        'user__email',
        'user__username',
        'stripe_checkout_session_id',
    )

    readonly_fields = (
        'created',
        'modified',
        'status_display',
        'time_remaining',
        'stripe_session_link',
    )

    autocomplete_fields = [
        'user',
    ]

    ordering = ('-created',)

    fieldsets = (
        ('Reservation Details', {
            'fields': (
                'user',
                'slug',
                'expires_at',
                'status_display',
                'time_remaining',
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
                'stripe_checkout_session_id',
                'stripe_session_link',
            ),
            'classes': ('collapse',),
        }),
    )

    actions = [
        'cleanup_expired_reservations',
        'release_selected_reservations',
    ]

    def user_email(self, obj):
        """Display user email in list view."""
        return obj.user.email
    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'

    def status_display(self, obj):
        """Display reservation status with color coding."""
        if obj.is_expired():
            return format_html(
                '<span style="color: red; font-weight: bold;">EXPIRED</span>'
            )
        else:
            return format_html(
                '<span style="color: green; font-weight: bold;">ACTIVE</span>'
            )
    status_display.short_description = 'Status'

    def time_remaining(self, obj):
        """Show time remaining until expiration."""
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

    def stripe_session_link(self, obj):
        """Create a link to Stripe dashboard if session ID exists."""
        if not obj.stripe_checkout_session_id:
            return '-'

        # This assumes you have Stripe dashboard access - adjust URL as needed
        stripe_url = f"https://dashboard.stripe.com/payments/{obj.stripe_checkout_session_id}"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">View in Stripe</a>',
            stripe_url
        )
    stripe_session_link.short_description = 'Stripe Session'

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
        return super().get_queryset(request).select_related('user')

    # Custom admin actions

    @admin.action(description='Clean up expired reservations')
    def cleanup_expired_reservations(self, request, queryset):  # pylint: disable=unused-argument
        """Admin action to clean up expired reservations."""
        deleted_count = EnterpriseSlugReservation.cleanup_expired()
        self.message_user(
            request,
            f"Successfully cleaned up {deleted_count} expired reservations."
        )

    @admin.action(description='Release selected reservations')
    def release_selected_reservations(self, request, queryset):
        """Admin action to manually release reservations."""
        count = queryset.count()
        queryset.delete()
        self.message_user(
            request,
            f"Released {count} reservations."
        )

    def has_add_permission(self, request):
        """
        Generally, reservations should be created through the checkout flow,
        but allow admins to create them if needed.
        """
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        """Allow deletion for cleanup purposes."""
        return True

    def get_readonly_fields(self, request, obj=None):
        """Make certain fields readonly when editing existing objects."""
        readonly = list(self.readonly_fields)

        # If editing an existing object, make user and slug readonly
        # to prevent accidental changes that could break the flow
        if obj:
            readonly.extend(['user', 'slug'])

        return readonly
