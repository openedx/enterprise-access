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

**Historical Context:**

The ``CheckoutIntent`` model was originally designed to track individual checkout sessions during the purchase flow.
However, as the system evolved, it became apparent that enterprises typically have long-lived
subscription relationships (spanning multiple years with annual renewals).
Rather than creating a separate "subscription" model, ``CheckoutIntent`` was extended to serve as the
permanent subscription tracker, maintaining links to the original purchase context while supporting
the ongoing subscription lifecycle.

Architecture Overview
---------------------

The subscription lifecycle spans four main services, each with distinct responsibilities:

**Stripe (Payment Processing)**
  - Manages payment methods, invoicing, and billing cycles
  - Sends webhooks for customer-driven subscription state changes
  - Stores subscription metadata linking back to enterprise record (the ``CheckoutIntent`` identifier)

**Enterprise Access (Orchestration)**
  - Processes Stripe webhooks and maintains customer subscription state
  - Provides provisioning API for Salesforce integration
  - Provides REST and BFF APIs for integration with our frontends
  - Orchestrates License Manager ``SusbscriptionPlan`` and renewal operations

**Salesforce (CRM & Opportunity Management)**
  - Tracks sales opportunities and revenue recognition
  - Creates Opportunity Line Items (OLIs) for accounting
  - Initiates provisioning requests via APIs

**License Manager (Subscription & License Management)**
  - Manages subscription plans and license allocation (i.e. the core edX Enterprise subscription domain records)
  - Processes subscription renewals and transitions

**Data Flow Pattern:**
```
Stripe Events → Enterprise Access → License Manager
    ↕                   ↕
          →        Salesforce
```

Core Models and Relationships
-----------------------------

**CheckoutIntent**
  The central subscription tracker that maintains the complete lifecycle of an enterprise customer's subscription. Originally designed for checkout sessions, it has evolved into the permanent record linking all subscription-related activities.

  *Key Fields:*
  - ``enterprise_uuid``: Links to the enterprise customer in downstream systems
  - ``stripe_customer_id``: Links to the Stripe customer record
  - ``state``: Tracks the overall subscription state (CREATED → PAID → FULFILLED)
  - ``workflow``: Links to the provisioning workflow that created the subscription

**StripeEventData**
  Persists the raw payload from all Stripe webhook events for audit and debugging purposes.

  *Key Fields:*
  - ``event_id``: Stripe's unique event identifier
  - ``event_type``: The type of Stripe event (e.g., 'invoice.paid', 'customer.subscription.updated')
  - ``checkout_intent``: Links the event to the relevant subscription
  - ``data``: Complete JSON payload from Stripe

**StripeEventSummary**
  Extracts and normalizes key data from Stripe events for easier querying and analysis.

  *Key Fields:*
  - ``subscription_plan_uuid``: Links to the License Manager subscription plan
  - ``stripe_subscription_id``: Stripe's subscription identifier
  - ``subscription_status``: Current subscription status (trialing, active, canceled, etc.)
  - ``invoice_amount_paid``: Payment amounts for financial tracking

**CheckoutIntentRenewal**
  Tracks the processing of subscription renewals, particularly the transition from trial to paid and subsequent annual renewals.

  *Key Fields:*
  - ``subscription_plan_renewal_id``: UUID of the renewal record in License Manager
  - ``stripe_event_data``: Links to the specific Stripe event that triggered the renewal
  - ``processed_at``: Timestamp when the renewal was successfully processed

**Relationship Summary:**
```
CheckoutIntent (1) ←→ (*) StripeEventData ←→ (1) StripeEventSummary
       ↓
CheckoutIntent (1) ←→ (*) CheckoutIntentRenewal
```

Subscription Lifecycle States
-----------------------------

**Trial Creation and Provisioning**
  When a customer completes self-service checkout, Stripe creates a subscription in trial status and sends an ``invoice.paid`` event (amount=$0). This triggers:
  
  1. CheckoutIntent state transitions from CREATED → PAID → FULFILLED
  2. Salesforce receives the webhook and creates Account/Contact/Opportunity records
  3. Salesforce calls the ``/provisioning`` API to create the trial subscription plan
  4. License Manager creates licenses for the trial period

**Trial-to-Paid Transition**
  When the trial period ends, Stripe automatically transitions the subscription to active status and sends a ``customer.subscription.updated`` event (trialing → active). This triggers:

  1. Enterprise Access webhook handler detects the status change
  2. Calls License Manager's ``process_subscription_plan_renewal()`` API
  3. License Manager creates a paid subscription plan and assigns licenses from trial to paid
  4. Creates a CheckoutIntentRenewal record to track the processing

**Active Subscription Management**
  During the active subscription period:
  
  - First paid invoice generates ``invoice.paid`` event (amount>$0)
  - Salesforce creates a paid Opportunity Line Item
  - Salesforce calls ``/oli-update`` API to associate the OLI with the paid subscription plan
  - Ongoing subscription management handled automatically by Stripe/License Manager

**Annual Renewals**
  For subsequent years, Stripe sends ``invoice.upcoming`` events 30 days before renewal:

  1. Salesforce receives the webhook and creates renewal Opportunity/OLI records
  2. Salesforce calls the ``/renewal`` API to create a new subscription plan renewal
  3. When payment is processed, the renewal becomes active in License Manager

**Subscription Termination**
  When subscriptions are canceled, ``customer.subscription.updated`` events (status: canceled) trigger:

  - License deactivation in License Manager
  - Cancellation notifications to customers
  - Opportunity updates in Salesforce

Event Processing Flows
----------------------

**Stripe Webhook Processing**

*invoice.paid Event:*
  - Persisted as StripeEventData and summarized in StripeEventSummary
  - For first invoice (trial): Updates CheckoutIntent to PAID status
  - For subsequent invoices: Tracked for financial reporting

*customer.subscription.updated Event:*
  - Detects subscription status changes (trialing→active, active→canceled, etc.)
  - For trial-to-paid transitions: Automatically triggers renewal processing
  - For cancellations: Initiates cleanup procedures

**Salesforce API Integration**

*POST /provisioning:*
  - Creates initial trial subscription plan in License Manager
  - Links Salesforce Opportunity Line Item to subscription plan
  - Initiates enterprise customer provisioning workflow

*POST /oli-update:*
  - Updates existing subscription plans with Salesforce OLI references
  - Used when Salesforce creates paid OLIs after initial provisioning
  - Maintains accounting traceability between systems

*POST /renewal:*
  - Creates new subscription plan renewals for annual billing cycles
  - Processes renewal records in License Manager
  - Creates CheckoutIntentRenewal tracking records

**License Manager Orchestration**

Enterprise Access coordinates License Manager operations through several API endpoints:

- ``create_subscription_plan()``: Creates new subscription plans during provisioning
- ``process_subscription_plan_renewal()``: Processes renewals during trial-to-paid transitions
- ``update_subscription_plan()``: Updates existing plans with Salesforce OLI references

Data Relationships Across Services
----------------------------------

**Key Identifiers**

*stripe_customer_id:*
  - Links CheckoutIntent to Stripe Customer records
  - Used to correlate webhook events with enterprise customers
  - Enables lookup of original CheckoutIntent for Year 2+ renewals

*enterprise_uuid:*
  - Universal identifier for the enterprise customer across all services
  - Links CheckoutIntent to License Manager's EnterpriseCustomer
  - Stored in Stripe subscription metadata for cross-reference

*checkout_intent_id:*
  - Stored in Stripe subscription metadata
  - Enables webhook events to find the correct CheckoutIntent
  - Maintained throughout the entire subscription lifecycle

*salesforce_opportunity_line_item:*
  - Links subscription plans to Salesforce accounting records
  - Ensures revenue recognition and financial reporting alignment
  - Used for idempotent API operations

**Cross-Service Consistency Patterns**

*Event Sourcing:*
  All Stripe events are persisted in StripeEventData, providing a complete audit trail and enabling event replay for debugging.

*Idempotent Operations:*
  API endpoints use Salesforce OLI IDs as idempotency keys, preventing duplicate subscription plan creation during webhook retries.

*Eventual Consistency:*
  Systems may be briefly out of sync during event processing, but eventual consistency is achieved through webhook processing and API calls.

**Lookup Patterns for Long-lived Subscriptions**

For Year 1 events, the CheckoutIntent is directly linked. For Year 2+ renewals:

1. Extract ``stripe_subscription_id`` from the event
2. Query StripeEventSummary for historical events with this subscription ID
3. Find the original CheckoutIntent through the historical event chain
4. Create new renewal records linked to the original CheckoutIntent

Error Scenarios
---------------

**Webhook Processing Failures**
  - Stripe webhook delivery failures due to network issues or service downtime
  - Invalid or malformed event payloads
  - Missing required metadata in Stripe subscriptions

**API Integration Failures**
  - License Manager API timeouts during renewal processing
  - Salesforce API failures during provisioning requests
  - Authentication failures between services

**Data Consistency Issues**
  - Missing CheckoutIntent records for webhook events
  - Orphaned StripeEventData records without corresponding CheckoutIntents
  - Failed renewal processing leaving CheckoutIntentRenewal records in incomplete states

**Error Handling Approach**
  - Webhook endpoints return appropriate HTTP status codes to trigger Stripe retries
  - Failed operations are logged with detailed error messages for debugging
  - CheckoutIntentRenewal records track processing failures for manual intervention
  - Critical failures raise exceptions to prevent silent data corruption

Future Considerations
--------------------

**Database Normalization Improvements**

*Pros of Better Normalization:*
  - Cleaner separation of concerns between checkout sessions and long-lived subscriptions
  - More explicit modeling of subscription lifecycle states
  - Easier querying and reporting on subscription metrics

*Cons of Current Approach:*
  - CheckoutIntent serves dual purposes (checkout + subscription tracking)
  - Some fields may be irrelevant for long-lived subscription management
  - Potential confusion about the model's primary purpose

*Potential Improvements:*
  - Create dedicated ``Subscription`` model linked to CheckoutIntent
  - Normalize subscription state tracking separate from checkout state
  - Consider separating audit/event data from operational data

**Consolidated External System Integration**

Currently, external systems (Salesforce, Stripe) can trigger actions in Enterprise Access through webhooks and API calls. Future improvements might include:

*Centralized Event Processing:*
  - Single event bus for all external system interactions
  - Unified event schema across Stripe webhooks and Salesforce API calls
  - Better coordination of multi-system operations

*Reduced API Surface:*
  - Consolidate multiple API endpoints into fewer, more powerful operations
  - Event-driven integration patterns instead of direct API calls
  - Asynchronous processing with status polling instead of synchronous operations

These improvements would reduce system complexity and improve reliability, but would require significant coordination across teams and services.
