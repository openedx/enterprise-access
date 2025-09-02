0031 Replace EnterpriseSlugReservation with CheckoutIntent
**********************************************************

Status
======
**Proposed** (July 2025)

Context
=======

The initial self-service purchasing implementation used frontend-only state
management to keep the MVP simple. However, this approach created several gaps:

1. **Post-submission record keeping**: We had to create
   ``EnterpriseSlugReservation`` just for slug uniqueness validation, but we
   still lack persistence of minimal form fields (including license quantity)
   after payment to render the success page without ephemeral frontend memory.

2. **Lifecycle state tracking**: Now useful for "go to dashboard" stateful
   button implementation that needs to track provisioning progress.

3. **Error tracking**: A convenient place to centrally store the last reason
   something failed during checkout or provisioning.

The current ``EnterpriseSlugReservation`` model addresses only slug conflicts
but doesn't provide comprehensive state tracking for the entire
checkout-to-provisioning lifecycle.

For reference, the success page is documented within this Figma:

https://www.figma.com/design/KAda9wZwM0uiTxVmNqF7ip/Subscription-Self-Service-V2?node-id=270-10323&t=T6TzjEJNGyGdeaDf-1

Decision
========

Replace the ``EnterpriseSlugReservation`` model with a consolidated
``CheckoutIntent`` model that serves multiple purposes: persistence of minimal
purchase state (not all fields), lifecycle tracking, and error tracking.

CheckoutIntent Model Stub
-------------------------

.. code-block:: python

    class CheckoutIntent(TimeStampedModel):
        class State(models.TextChoices):
            CREATED = 'created', 'Created'
            PAID = 'paid', 'Paid'
            FULFILLED = 'fulfilled', 'Fulfilled'
            ERRORED_STRIPE_CHECKOUT = 'errored_stripe_checkout', 'Errored (Stripe Checkout)'
            ERRORED_PROVISIONING = 'errored_provisioning', 'Errored (Provisioning)'
            EXPIRED = 'expired', 'Expired'
        
        user = models.OneToOneField(User)
        state = models.CharField(choices=State.choices, default=State.CREATED)
        enterprise_name = models.CharField()
        enterprise_slug = models.SlugField()
        quantity = models.PositiveIntegerField()
        stripe_checkout_session_id = models.CharField()
        last_checkout_error = models.TextField()
        last_provisioning_error = models.TextField()
        workflow = models.OneToOneField(ProvisionNewCustomerWorkflow)
        expires_at = models.DateTimeField()

        @property
        def admin_portal_url(self):
            if self.state == self.State.FULFILLED:
                return f"https://portal.edx.org/{self.enterprise_slug}"
            return None

API Changes
-----------

CheckoutIntent state needs to be surfaced to the frontend in order to give
real-time feedback to the "go to dashboard" stateful button, conditionally
perform a forced redirect from other pages to the success page, and support a
hard refresh of the Success page. In service of all of the above functions, the
following changes need to be made at the REST API layer:

- **Context BFF Endpoint**: Add a simple serialized CheckoutIntent object, or
  null if there is none for the user.
- **Success BFF Endpoint**: Inherit from the Context BFF endpoint, but replace
  the simple CheckoutIntent serialization with a more complete one with many
  more fields which can be used to power the information displays on the
  Success page.  The extra fields are derived from Stripe API calls (mainly the
  Invoice and PaymentMethod objects related to the Subscription related to the
  CheckoutSession).
- **ModelViewSet**: Expose a vanilla ModelViewSet for CheckoutIntent creation, state mutation and polling.

Modified "context" BFF endpoint:

.. code-block::

    POST /api/v1/bffs/checkout/context

    Request:
    {}

    Response:
    {
    [...]
        "checkout_intent": {
            "uuid": "",
            "state": "created",
            "enterprise_name": "My Enterprise",
            "enterprise_slug": "my-sluggy",
            "stripe_checkout_session_id": "",
            "last_checkout_error": "",
            "last_provisioning_error": "",
            "workflow_id": "",
            "expires_at": "",
            "admin_portal_url": "https://portal.edx.org/my-sluggy"
        }
    [...]
    }


New "success" BFF endpoint:

.. code-block::

    POST /api/v1/bffs/checkout/success

    Authentication: JWT
    Authorization: Authenticated ONLY
    Purpose: Same as context, but a bigger checkout_intent serialization which includes all fields displayed by the Success page.
    Side-Effects: None

    Request:
    {}

    Response:
    {
        [...]
        "checkout_intent": {
            "uuid": "",
            "state": "created",
            "enterprise_name": "My Enterprise",
            "enterprise_slug": "my-sluggy",
            "stripe_checkout_session_id": "",
            "last_checkout_error": "",
            "last_provisioning_error": "",
            "workflow_id": "",
            "expires_at": "",
            "admin_portal_url": "https://portal.edx.org/my-sluggy",

            "first_billable_invoice": {
                "start_time": "2025-07-17T00:15:17.776Z",
                "end_time": "2026-07-17T00:15:17.776Z",
                "last4": 1234,
                "quantity": 35,
                "unit_amount_decimal": 396.00,
                "customer_phone": "",
                "customer_name": "",
                "billing_address": {
                    "city": "",
                    "country": "",
                    "line1": "",
                    "line2": "",
                    "postal_code": "",
                    "state": ""
                }
            }
        }
        [...]
    }

Basic checkout-intent REST endpoints:

.. code-block::

    GET /api/v1/customer_billing/checkout-intent/
    GET /api/v1/customer_billing/checkout-intent/<uuid>
    POST /api/v1/customer_billing/checkout-intent/
    PATCH /api/v1/customer_billing/checkout-intent/<uuid>

State
-----

The CheckoutIntent state can be mutated within existing workflows at these points:

1. **onSubmit callback for Stripe payment element**: ``created -> paid``
2. **New WorkflowStep in ProvisionNewCustomerWorkflow**: ``paid -> fulfilled``
3. **Error handling**: Transitions to ``errored_stripe_checkout`` or ``errored_provisioning``


Alternatives Considered
=======================

Polling Existing Context BFF Endpoint
--------------------------------------

*Alternative:* Continue using the existing context BFF endpoint and poll ``existing_customers_for_authenticated_user`` to determine successful fulfillment.

*Rejected because:*

- Would require sacrificing performance for accuracy by removing backend caching
- No centralized error tracking or lifecycle state management
- Missing post-submission record keeping for success page rendering

Consequences
============

*Positive consequences:*

- Simplified "go to dashboard" stateful button implementation by reading a simple "state" field fetched via dedicated endpoint.
- Centralized error tracking improves user experience by speeding up error feedback via polling.
- Centralized error tracking improves debugging experience.
- More elaborate CheckoutIntent serialization supports fully populating the Success page.
- Centralized reservation and checkout persistence into one model to share common fields.

*Negative consequences:*

- Additional model complexity and migration requirements
- Need to implement proper cleanup of expired records
