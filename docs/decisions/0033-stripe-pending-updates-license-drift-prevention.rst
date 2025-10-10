0033. Stripe Subscription Pending Updates
=========================================

Status
------

Proposed - October 2025

Context
-------

We're using the Stripe customer billing portal to support self-service purchasing.
https://docs.stripe.com/customer-management/configure-portal
By default, stripe customers are *not* allowed to update the quantity of subscriptions
through the customer portal. We don't plan on changing this default.

However, customers will be allowed (by default) to change their payment methods via the Stripe portal.
By default, Stripe immediately applies subscription changes regardless of whether payment succeeds or fails.
This creates a potential issue on Stripe subscription updates as follows:

1. Customer completes the SSP flow and a Stripe subscription with a trial period is created.
2. Before trial end, the customer updates their payment method.
3. The trial ends and Stripe generates an invoice, changing the Subscription status to ``active``.
4. Payment fails on the new payment method.
5. Our internal system sees the ``active`` status and processes the trial -> paid transition.
5. Customer gets access to a paid Subscription Plan without actually paying.

This potential scenario poses both revenue risk and compliance issues.

Decision
--------

We will implement Stripe's "pending updates" feature to ensure subscription
changes only apply *after successful payment*:

1. **Enable pending updates on subscription creation**: When a new subscription is created and we receive a
   ``customer.subscription.created`` webhook, immediately call ``stripe.Subscription.modify()`` with
   ``payment_behavior='pending_if_incomplete'``

2. **Handle pending update lifecycle**: Add webhook handlers for:
   - ``customer.subscription.updated`` - to track when subscriptions have pending updates
   - The handler for this event must take both the ``state`` and ``pending_updates`` fields
     in the event payload into account when determining how edX Subscription Plan
     state should be altered.

The implementation ensures that subscription payment method changes through the billing portal
require successful payment before the paid subscription is activated within edX Enterprise systems.

See: https://docs.stripe.com/billing/subscriptions/pending-updates

Rationale
---------

**Why pending updates work for our use case:**
- Stripe's billing portal is configured to allow payment method changes
- Pending updates prevent changes from applying on payment failure
- The ``payment_behavior='pending_if_incomplete'`` parameter persists as subscription behavior for future updates

**Why we enable it post-creation:**
- Checkout sessions don't support the ``payment_behavior`` subscription parameter

Alternatives Considered
-----------------------

1. **Periodic reconciliation**
   - *Considered*: Regular batch job to sync Stripe quantities with payment status
   - *Rejected*: Reactive approach; temporary license drift still occurs

2. **Webhook-only approach without pending updates**
   - *Considered*: React to subscription changes and manually revert failed payments
   - *Rejected*: Complex state management; race conditions; still allows temporary drift

Consequences
------------

**Positive:**
- Eliminates potential for paid access without the customer actually paying
- Leverages Stripe's native pending updates feature (well-tested, reliable)
- Maintains customer self-service experience through billing portal
- Provides audit trail of all subscription events via ``StripeEventData``

**Negative:**
- Adds complexity to webhook handling
- Requires monitoring of pending update expiration events

**Future Considerations:**
- We need to have a well-defined ``CheckoutIntent`` lifecycle flow to handle pending updates
- We need corresponding actions on the ``SubscriptionPlan`` to manage state related to failed payments
- May need customer notification system for failed payments
