Subscription and Renewal Lifecycle
==================================

Overview
--------

The customer billing domain manages the complete lifecycle of self-service enterprise subscriptions,
from initial checkout through annual renewals. This system orchestrates interactions between Stripe (payments),
Salesforce (CRM), and License Manager (subscription management) to provide a seamless subscription experience.

**Key Principles:**

* **One CheckoutIntent per Customer**: Each enterprise customer has a single CheckoutIntent that serves as the long-lived subscription tracker
* **Event-Driven Architecture**: Stripe webhooks drive state transitions and trigger downstream processing
* **Service Orchestration**: Enterprise Access acts as the central coordinator between external systems
* **Audit Trail**: All events and state changes are persisted for debugging and compliance

Architecture Overview
---------------------

The subscription lifecycle spans four main services, each with distinct responsibilities:

**Stripe (Payment Processing)**

* Manages payment methods, invoicing, and billing cycles
* Sends webhooks for customer-driven subscription state changes
* Stores subscription metadata linking back to enterprise record (the ``CheckoutIntent`` identifier)

**Salesforce (CRM & Opportunity Management)**

* Tracks sales opportunities and revenue recognition
* Creates Opportunity Line Items (OLIs) for accounting
* Initiates provisioning requests via APIs

**Enterprise Access (Orchestration)**

* Processes Stripe webhooks and maintains customer subscription state
* Provides provisioning API for Salesforce integration
* Provides REST and BFF APIs for integration with our frontends
* Orchestrates License Manager ``SusbscriptionPlan`` and renewal operations

**License Manager (Subscription & License Management)**

* Manages subscription plans and license allocation (i.e. the core edX Enterprise subscription domain records)
* Processes subscription renewals and transitions

Core Models and Relationships
-----------------------------

**CheckoutIntent**

The central subscription tracker that maintains the complete lifecycle of an enterprise customer's subscription.
Originally designed for checkout sessions, it has evolved into the permanent record linking all
self-service-subscription-related activities for a given customer.

Key Fields:

* ``enterprise_uuid``: Links to the enterprise customer in downstream systems
* ``stripe_customer_id``: Links to the Stripe customer record
* ``state``: Tracks the overall subscription state (CREATED → PAID → FULFILLED)
* ``workflow``: Links to the provisioning workflow that created the subscription

**StripeEventData**

Persists the raw payload from all Stripe webhook events that we handle. Ultimately facilitates core business logic,
audit, and debugging purposes.

Key Fields:

* ``event_id``: Stripe's unique event identifier
* ``event_type``: The type of Stripe event (e.g., 'invoice.paid', 'customer.subscription.updated')
* ``checkout_intent``: Links the event to the relevant subscription
* ``data``: Complete JSON payload from Stripe

**StripeEventSummary**

Extracts and normalizes key data from Stripe events for easier querying and analysis, especially for
cross-service data-linkage.

Key Fields:

* ``subscription_plan_uuid``: Links to the License Manager subscription plan
* ``stripe_subscription_id``: Stripe's subscription identifier

**SelfServiceSubscriptionRenewal**

Tracks the processing of subscription renewals,
particularly the transition from trial to paid and subsequent annual renewals.

Key Fields:

* ``subscription_plan_renewal_id``: UUID of the renewal record in License Manager
* ``stripe_event_data``: Links to the specific Stripe event that triggered the renewal
* ``processed_at``: Timestamp when the renewal was successfully processed

Subscription Lifecycle States
-----------------------------

**Trial Creation and Provisioning**

When a customer completes self-service checkout,
Stripe creates a subscription in trial status and sends an ``invoice.paid`` event (amount=$0). This triggers:

1. CheckoutIntent state transitions from CREATED → PAID → FULFILLED
2. Salesforce receives the webhook and creates Account/Contact/Opportunity records
3. Salesforce calls the ``/provisioning`` API to create our internal enterprise customer and subscription records
   (for both the trial and 1st paid plan) via API calls to downstream services.

**Trial-to-Paid Transition**

When the trial period ends, Stripe automatically transitions the subscription to an ``active`` status and
sends a ``customer.subscription.updated`` event (trialing -> active). This triggers:

1. Enterprise Access webhook handler detects the status change
2. Calls License Manager's ``/api/v1/provisioning-admins/subscription-plan-renewals/{id}/process/`` endpoint
3. License Manager processes the renewal from the trial -> paid subscription plan
4. Creates a ``SelfServiceSubscriptionRenewal`` record to track the processing

**Active Subscription Management**

During the active subscription period:
  
* First paid invoice generates ``invoice.paid`` event (amount>$0)
* Salesforce creates a paid Opportunity Line Item
* Salesforce calls ``/api/v1/provisioning/subscription-plan-oli-update`` API to associate the OLI with the paid subscription plan

**Payment Errors**

We rely on Stripe’s Pending Updates feature to help prevent subscriptions from becoming active
before a payment is *successfully* processed. When a payment fails, the Stripe subscription enters a ``past_due``
state. When we observe this state:

* All *future* license-manager subscription plans related to the stripe subscription are updated to
  have ``active=False``.
* We do this regardless of whether the corresponding renewal has been processed (in case there's some other
  state change that temporarily puts the stripe subscription in an ``active`` state.

**Annual Renewals**

i.e. the second and ensuing paid periods. TBD on the actual flow, here.

**Subscription Termination at period end**

When subscriptions are canceled, ``customer.subscription.updated`` events trigger:

* License deactivation in License Manager
* Cancellation notifications to customers
* Opportunity updates in Salesforce (maybe ?)

Event Processing Flows
----------------------

**Salesforce API Integration**

``POST /provisioning``:
* Initiates enterprise customer provisioning workflow
* Creates initial trial subscription plan in License Manager
* Links Salesforce Opportunity Line Item to trial subscription plan

``POST /oli-update``:

* Updates existing paid subscription plan with Salesforce OLI references
* Used when Salesforce creates paid OLIs

Data Relationships Across Services
----------------------------------

**Key Identifiers**

``stripe_customer_id``:

* Links CheckoutIntent to Stripe Customer records
* Used to correlate webhook events with enterprise customers
* Enables lookup of original CheckoutIntent for Year 2+ renewals

``enterprise_uuid:``

* The ``EnterpriseCustomer.uuid`` field

``checkout_intent_{id,uuid}``:

* Stored in Stripe subscription metadata
* Enables webhook events to find the correct CheckoutIntent

``salesforce_opportunity_line_item``:

* Links subscription plans to Salesforce accounting records
* Ensures revenue recognition and financial reporting alignment
* Used for idempotent API operations

Error Scenarios
---------------
**API Integration Failures**

* License Manager API timeouts during renewal processing
* Salesforce API failures during provisioning requests

**Data Consistency Issues**

* Missing CheckoutIntent records for webhook events
* Failed renewal processing leaving ``SelfServiceSubscriptionRenwal`` records in unprocessed state

Future Considerations
--------------------
**Database Normalization Improvements**

Pros of Better Normalization:

* Cleaner separation of concerns between checkout sessions and long-lived subscriptions
* More explicit modeling of subscription lifecycle states
* Easier querying and reporting on subscription metrics

Cons of Current Approach:

* ``CheckoutIntent`` serves dual purposes (checkout + subscription tracking)
* Some fields may be irrelevant for long-lived subscription management
* Potential confusion about the model's primary purpose

Potential Improvements:

* Create dedicated ``Subscription`` model linked to CheckoutIntent
* Normalize subscription state tracking separate from checkout state
* Consider separating audit/event data from operational data

**Consolidated External System Integration**

Currently, two external systems (Salesforce, Stripe)
can trigger actions in enterprise-access through webhooks and API calls.
Furthermore, Stripe can trigger Salesforce record creation and other actions prior
to Salesforce, in turn, trigger actions in enterprise-access.
