"""
Custom authentication classes for the enterprise-access API.
"""
import logging

import stripe
from django.conf import settings
from rest_framework import authentication, exceptions

logger = logging.getLogger(__name__)


class StripeWebhookAuthentication(authentication.BaseAuthentication):
    """
    Authentication class for Stripe webhook requests.

    Validates that incoming webhook requests are authentic by verifying
    the Stripe signature using the webhook endpoint secret. This ensures
    requests genuinely originate from Stripe servers.

    The webhook endpoint secret must be configured in settings.STRIPE_WEBHOOK_ENDPOINT_SECRET.

    Authentication succeeds by returning None (no user authentication required),
    but raises AuthenticationFailed if the signature is invalid.
    """

    def authenticate(self, request):
        """
        Authenticate the Stripe webhook request by verifying its signature.

        Args:
            request: The incoming HTTP request object

        Returns:
            None: Stripe webhook requests don't authenticate to a specific user

        Raises:
            AuthenticationFailed: If signature verification fails or required headers/settings are missing
        """
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

        if not sig_header:
            logger.warning('Stripe webhook request missing signature header')
            raise exceptions.AuthenticationFailed(
                'Missing Stripe signature header'
            )

        webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_ENDPOINT_SECRET', None)
        if not webhook_secret:
            logger.error('STRIPE_WEBHOOK_ENDPOINT_SECRET not configured in settings')
            raise exceptions.AuthenticationFailed(
                'Webhook endpoint secret not configured'
            )

        try:
            # Verify the signature and construct the event
            # This validates that the request genuinely came from Stripe
            event = stripe.Webhook.construct_event(
                payload,
                sig_header,
                webhook_secret
            )
            # Make the constructed event available to the view to avoid
            # reconstructing it again there.
            setattr(request, "_stripe_event", event)
        except ValueError as e:
            logger.exception('Invalid payload in Stripe webhook request: %s', e)
            raise exceptions.AuthenticationFailed('Invalid payload')
        except stripe.SignatureVerificationError as e:
            logger.exception('Invalid signature in Stripe webhook request: %s', e)
            raise exceptions.AuthenticationFailed('Invalid signature')
        except Exception as e:
            logger.exception('Unexpected error during Stripe webhook authentication: %s', e)
            raise exceptions.AuthenticationFailed('Authentication failed')

        # Authentication succeeded - return None since webhooks don't have a user
        # The request is from Stripe's servers, not a logged-in user
        return None
