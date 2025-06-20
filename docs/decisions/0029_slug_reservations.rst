ADR-001: Enterprise Slug Reservation System Architecture
********************************************************

:Date: June 2025
:Authors: iloveagent57, Titans Engineering Team
:Feature: Self-Service Purchasing - Slug Reservation Race Condition

Status
======
**Accepted** (June 2025)

Context
=======

The self-service Teams subscription checkout flow has an inherent race condition during the delay
between Stripe checkout session creation and actual EnterpriseCustomer provisioning. This delay window consists of:

1. User submits checkout form
2. Stripe payment processing and webhook delivery
3. Salesforce automation triggers
4. Provisioning API call creates EnterpriseCustomer

This creates opportunities for multiple users to reserve the same enterprise slug,
leading to downstream provisioning failures in several scenarios:

* Multiple admins attempting to submit the form using the same slug
* Eager administrators double-clicking submit buttons
* Frontend JavaScript errors causing duplicate API calls  
* Malicious actors intentionally spamming checkout forms with identical slugs

Without slug reservation, we cannot validate slug uniqueness at checkout time since the actual
EnterpriseCustomer record doesn't exist yet during the asynchronous provisioning pipeline.

Decision
========

We will implement a **database-backed slug reservation system** using a Django model, ``EnterpriseSlugReservation``.

Key Design Decisions
--------------------
Our solution implements the following requirements:

* Database Storage - Store reservations in a Django model with proper indexing and constraints.
* One Reservation Per User - Each user can only have one active reservation at a time (``OneToOneField`` relationship)
* Unique Slug Constraint - Database-level unique constraint prevents duplicate slug reservations
* 24-hour Expiration - Reservations automatically expire after 24 hours to match Stripe's checkout session expiry.
  (configurable/overridable via Django settings)
* Automatic Cleanup - Expired reservations are cleaned up before new reservation attempts
* Stripe Checkout Session ID correlation - Store Stripe checkout session IDs for debugging and potential cleanup hooks
* User Replacement - Users can replace their own existing reservations (prevents user lock-out)
* Backwards Compatibility - API works with or without user parameter to help support flows *aside* from direct HTTP
  requests by the user for whom the slug should be reserved.

Considered Alternatives
=======================

Cache-Based Reservation System
------------------------------

We considered using Django's cache framework (Redis/Memcached) to store slug reservations
with the following potential structure::

    # Cache key pattern: "slug_reservation:{slug}"
    # Cache value: {"user_id": 123, "expires_at": "2025-06-24T15:54:35Z", "session_id": "cs_..."}
    
Advantages:

* Faster read/write operations
* Automatic expiration via TTL
* No database schema changes required
* Potentially better performance at scale

Disadvantages:

* Data volatility - Cache evictions could release active reservations unexpectedly
* No audit trail - Lost debugging information when cache entries expire
* Deployment complexity - Cache clearing during deployments could cause reservation loss
* Testing difficulty - Cache behavior harder to test reliably
* Admin interface - No native Django admin support for cache data

Rejection Reasoning:

The cache-based approach introduces too many reliability concerns for a critical business flow.
The risk of losing reservation data due to cache evictions or deployments outweighs the performance benefits,
especially given that slug reservations are low-volume operations (dozens per hour, not thousands).

Consequences
============
This decision prioritizes data reliability and debugging capability over marginal performance gains,
which aligns with the business-critical nature of the checkout flow.

Positive
--------

* Eliminates race conditions in slug reservation during checkout flow
* Provides audit trail for debugging reservation conflicts  
* Scales appropriately with database indexing and query optimization
* Integrates cleanly with existing Django ORM and admin interfaces
* Supports authentication flows for both authenticated and anonymous users
* Maintains data consistency across application restarts and deployments
* Enables admin management via Django admin with custom actions and filters

Neutral
-------

* Additional database table requires migration and ongoing maintenance
* Cleanup process needs scheduled task or manual admin action
* Database queries add minimal latency to checkout validation

Negative
--------

* Slightly increased complexity in checkout validation logic
* Potential for reservation data cruft if the cleanup fails (although this should be, generally, very small/slow data)
* Database dependency for reservation validation (though acceptable given existing patterns)
