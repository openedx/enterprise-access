# Stripe Billing Architecture

## Overview

Enterprise Access acts as the **billing orchestrator** in the edX enterprise ecosystem,
translating Stripe payment events into business actions across multiple services.

## Architecture Flow

```
Stripe Billing → Enterprise Access → License Manager → Other Services
    Events         CheckoutIntents     SubscriptionPlans
                   + Webhooks          + Licenses
```

## Key Components

### Enterprise Access (This Service)
- **CheckoutIntent**: Tracks self-service checkout lifecycle (CREATED → PAID → FULFILLED)
- **StripeEventHandler**: Processes Stripe webhooks and updates CheckoutIntent state
- **StripeEventData**: Persists all Stripe events for audit trail
- **Provisioning Workflows**: Multi-step processes that create business records

### Stripe Integration
- Webhooks send events to Enterprise Access (`/webhooks/stripe/`)
- Events like `invoice.paid`, `customer.subscription.updated` trigger handlers
- Each event is persisted and linked to relevant CheckoutIntent

### License Manager Integration
- Enterprise Access makes REST API calls to License Manager
- `CheckoutIntent.quantity` maps 1:1 to `SubscriptionPlan.num_licenses`
- `CheckoutIntent.enterprise_uuid` links to License Manager's `CustomerAgreement`

## Typical Flow

1. **Customer pays** → Stripe sends `invoice.paid` webhook
2. **Enterprise Access** receives webhook, marks CheckoutIntent as PAID
3. **Provisioning workflow** triggered, creates enterprise customer
4. **API calls** to License Manager create SubscriptionPlan + Licenses
5. **Customer** can now assign licenses to learners

## Key Models

- **CheckoutIntent**: Central billing state machine
- **StripeEventData**: Complete audit trail of Stripe events
- **ProvisioningWorkflow**: Orchestrates cross-service business record creation

## Why This Design?

- **Single source of truth** for billing events
- **Reliable event processing** with persistence and retry capabilities
- **Clean separation** between billing (Enterprise Access) and licensing (License Manager)
- **Audit trail** for compliance and debugging
