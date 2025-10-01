"""
Signal handlers for customer billing models.
"""
import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from enterprise_access.apps.api.serializers import CheckoutIntentReadOnlySerializer
from enterprise_access.apps.customer_billing.constants import CheckoutIntentSegmentEvents
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.track.segment import track_event

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=CheckoutIntent)
def capture_previous_state(instance, **kwargs):
    """Capture the previous state before saving."""
    if instance.pk:
        try:
            instance._previous_state = CheckoutIntent.objects.get(pk=instance.pk).state
        except CheckoutIntent.DoesNotExist:
            instance._previous_state = None  # pylint: disable=protected-access
    else:
        instance._previous_state = None  # pylint: disable=protected-access


@receiver(post_save, sender=CheckoutIntent)
def track_checkout_intent_changes(instance, created, **kwargs):
    """Automatically track events after save."""
    previous_state = None if created else getattr(instance, '_previous_state', None)

    # Only track if it's a creation or if the state actually changed
    if created or (previous_state is not None and previous_state != instance.state):
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
