"""
Signal handlers for customer billing models.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from edx_django_utils.monitoring import set_custom_attribute

from enterprise_access.apps.api.serializers import CheckoutIntentReadOnlySerializer
from enterprise_access.apps.customer_billing.constants import (
    CHECKOUT_LIFECYCLE_IS_ERROR_MONITORING_KEY,
    CHECKOUT_LIFECYCLE_STATE_MONITORING_KEY,
    CheckoutIntentSegmentEvents
)
from enterprise_access.apps.customer_billing.models import CheckoutIntent, StripeEventData, StripeEventSummary
from enterprise_access.apps.track.segment import track_event

logger = logging.getLogger(__name__)


@receiver(post_save, sender=CheckoutIntent)
def track_checkout_intent_changes(sender, instance, created, **kwargs):  # pylint: disable=unused-argument
    """Automatically track events after save."""
    # Get the previous record from the history
    latest_history = instance.history.latest()
    prev_record = latest_history.prev_record if latest_history else None

    # Only track if it's a creation or if the state actually changed
    if created or (prev_record is not None and prev_record.state != instance.state):
        previous_state = None if created else (prev_record.state if prev_record else None)

        set_custom_attribute(CHECKOUT_LIFECYCLE_STATE_MONITORING_KEY, instance.state)
        if instance.state in CheckoutIntent.FAILURE_STATES:
            set_custom_attribute(CHECKOUT_LIFECYCLE_IS_ERROR_MONITORING_KEY, 'true')

        properties = dict(CheckoutIntentReadOnlySerializer(instance).data)
        properties["previous_state"] = previous_state
        properties["new_state"] = instance.state

        logger.info(
            (
                f"Tracking CheckoutIntent lifecycle event: "
                f"user={instance.user.id}, "
                f"intent_id={instance.id}, "
                f"previous_state={previous_state}, "
                f"new_state={instance.state}, "
                f"event={CheckoutIntentSegmentEvents.LIFECYCLE_EVENT}"
            )
        )

        track_event(
            lms_user_id=str(instance.user.id),
            event_name=CheckoutIntentSegmentEvents.LIFECYCLE_EVENT,
            properties=properties,
        )


@receiver(post_save, sender=StripeEventData)
def create_stripe_event_summary(sender, instance, created, **kwargs):  # pylint: disable=unused-argument
    """
    Automatically create/update StripeEventSummary when StripeEventData is saved.
    """
    if created or not hasattr(instance, 'summary'):
        try:
            # Create new summary record
            summary = StripeEventSummary(stripe_event_data=instance)
            summary.populate_with_summary_data()
            summary.save()

            logger.info(
                f"Created StripeEventSummary for event {instance.event_id} "
                f"(type: {instance.event_type})"
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                f"Failed to create StripeEventSummary for event {instance.event_id}: {e}"
            )
    else:
        try:
            # Update existing summary record
            summary = instance.summary
            summary.populate_with_summary_data()
            summary.save()

            logger.info(
                f"Updated StripeEventSummary for event {instance.event_id} "
                f"(type: {instance.event_type})"
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                f"Failed to update StripeEventSummary for event {instance.event_id}: {e}"
            )
