0034 SelfServiceSubscriptionRenewal Tracking Model
==================================================

Status
------

Accepted

Context
-------

The enterprise subscription system handles complex renewal workflows across multiple services
(Stripe, Salesforce, License Manager). Two critical renewal scenarios require explicit tracking:

1. **Trial-to-Paid Transition**: When Stripe automatically transitions a subscription from
   ``trialing`` to ``active`` status, Enterprise Access must call License Manager's
   ``process_subscription_plan_renewal()`` API to process the renewal that transitions the trial
   period ``SubscriptionPlan`` into the paid period ``SubscriptionPlan``.
2. **Annual Renewals**: For subsequent billing cycles, Salesforce creates new
   Opportunity Line Items and triggers renewal processing through Enterprise Access.

**Current State Issues:**

- Difficult to audit which Stripe events triggered which License Manager operations
- No way to track the relationship between renewal events and the License Manager renewal records they create

**Data Model Context:**

- ``CheckoutIntent`` serves as the long-lived subscription tracker (one per customer)
- ``StripeEventData`` and ``StripeEventSummary`` capture webhook events but don't track business process outcomes
- The ``provisioning`` workflow and step model records *do* capture business process outcomes to some degree,
  but not in a way that's easy to query.
- License Manager's ``SubscriptionPlanRenewal`` records are created by our API calls but not explicitly
  linked back to the triggering events.

Decision
--------

Create a ``SelfServiceSubscriptionRenewal`` model to track renewal processing with the following design.

**Core Fields:**

- ``checkout_intent`` (FK): Links to the long-lived subscription tracker
- ``subscription_plan_renewal_id`` (UUID): References the License Manager renewal record
- ``stripe_event_data`` (1:1 FK): Links to the specific Stripe event that triggered processing
- ``stripe_subscription_id``: Enables correlation across services
- ``processed_at``: Tracks successful completion

**Other Fields to Consider:**

It may be helpful to more explicitly represent the data relationship between backoffice system (e.g. Salesforce)
and the renewals that system is concerned with, i.e. set

- The backoffice line item id corresponding to the current plan
- The backoffice line item id corresponding to the future plan

**Key Design Principles:**

- **Audit Trail**: Every renewal operation is explicitly recorded with timestamps
- **Event Correlation**: Direct link between Stripe events and business process outcomes  
- **Cross-Service Linkage**: Maintains references to records in both Stripe and License Manager

**Initial Provisioning of Trial Flow:**

- Set ``subscription_plan_renewal_id`` to the provisioned renewal record between the trial and first paid plan

**First and Subsequent Payment Received Flow:**

- Set ``processed_at`` to true.
- Set ``stripe_event_data`` to the ``customer.subscription.updated`` event associated with moving
  the subscription into an ``active`` state.

**Future Renewal Flow:**

- Set ``subscription_plan_renewal_id`` to the provisioned renewal record between
  the Nth paid plan and the N+1th paid plan

Alternatives Considered
-----------------------

**Option 1: Add fields to StripeEventSummary**

- *Rejected*: Mixes event data extraction with business process tracking

**Option 2: Add fields to CheckoutIntent**

- *Rejected*: Cannot track multiple renewals over the subscription lifecycle

Consequences
------------

**Positive:**

- Explicit tracking of all renewal processing with full audit trail
- Clear relationship between Stripe events and License Manager operations
- Supports both trial-to-paid and annual renewal workflows

**Negative:**

- Additional database model to maintain
- Requires careful cleanup if renewal processing fails partway through

**Implementation Notes:**

- Model instances will be created by Stripe webhook handlers during renewal processing
- ``processed_at`` timestamp set only after successful License Manager API call
- Failed processing leaves record without ``processed_at`` for debugging/retry
