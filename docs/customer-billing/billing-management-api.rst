Billing Management API
======================

Overview
--------

The Billing Management API provides enterprise administrators and operators with REST endpoints to manage
customer billing data in Stripe. This API enables self-service management of billing addresses, payment methods,
transactions, and subscriptions without requiring direct access to the Stripe dashboard.

**Base URL:** ``/api/v1/billing-management/``

**Authentication:** JWT authentication required (``JwtAuthentication``)

**Authorization:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION`` - granted to Enterprise Admins and Enterprise Operators

Key Principles
--------------

* **Enterprise-Scoped Access**: All endpoints require ``enterprise_customer_uuid`` query parameter for RBAC checks
* **Stripe Integration**: Direct integration with Stripe APIs for real-time data
* **Idempotent Operations**: Safe to retry failed requests
* **Error Handling**: Returns standard HTTP status codes with descriptive error messages

Architecture
------------

The Billing Management API acts as a secure proxy layer between frontend applications and Stripe:

**Request Flow:**

1. Frontend (Admin Portal MFE) → Billing Management API
2. API validates JWT authentication and RBAC permissions
3. API looks up the enterprise's ``stripe_customer_id`` (see `CheckoutIntent Lookup`_)
4. API makes authenticated Stripe API call
5. API returns normalized response to frontend

**Benefits:**

* **Security**: Stripe API keys never exposed to frontend
* **Access Control**: Enterprise-scoped permissions via RBAC
* **Normalization**: Consistent response format across endpoints
* **Caching**: ``@stripe_cache()`` decorator reduces redundant Stripe API calls

Endpoints
---------

Address Management
^^^^^^^^^^^^^^^^^^

**GET /address/**

Retrieve the billing address for a Stripe customer.

* **URL Name:** ``billing-management-get-address``
* **Permission:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION``
* **Query Params:** ``enterprise_customer_uuid`` (required)
* **Response:** ``BillingAddressResponseSerializer``
* **Status Codes:** 200 (success), 400 (missing UUID), 403 (permission denied), 404 (not found), 422 (Stripe error)

**POST /address/**

Update the billing address for a Stripe customer.

* **URL Name:** ``billing-management-update-address``
* **Permission:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION``
* **Query Params:** ``enterprise_customer_uuid`` (required)
* **Request Body:** ``BillingAddressUpdateRequestSerializer``

  * Required: ``name``, ``email``, ``country``, ``address_line_1``, ``city``, ``state``, ``postal_code``
  * Optional: ``address_line_2``, ``phone``

* **Response:** ``BillingAddressResponseSerializer``
* **Status Codes:** 200 (success), 400 (validation error), 403 (permission denied), 404 (not found), 422 (Stripe error)

Payment Methods
^^^^^^^^^^^^^^^

**GET /payment-methods/**

List all payment methods for a Stripe customer.

* **URL Name:** ``billing-management-list-payment-methods``
* **Permission:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION``
* **Query Params:** ``enterprise_customer_uuid`` (required)
* **Response:** ``PaymentMethodsListResponseSerializer``

  * Returns array of payment methods with ``is_default`` flag
  * Supports cards (``type: 'card'``) and bank accounts (``type: 'us_bank_account'``)
  * Empty array if no payment methods exist

* **Status Codes:** 200 (success), 403 (permission denied), 404 (not found), 422 (Stripe error)

**POST /payment-methods/{payment_method_id}/set-default/**

Set a payment method as the default for invoicing.

* **URL Name:** ``billing-management-set-default-payment-method``
* **Permission:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION``
* **URL Params:** ``payment_method_id`` (required)
* **Query Params:** ``enterprise_customer_uuid`` (required)
* **Response:** ``{'message': 'Payment method set as default successfully'}``
* **Status Codes:** 200 (success), 400 (missing params), 403 (permission denied), 404 (not found), 422 (Stripe error)
* **Validation:** Verifies payment method belongs to customer before setting as default

**DELETE /payment-methods/{payment_method_id}/**

Remove a payment method from a Stripe customer.

* **URL Name:** ``billing-management-delete-payment-method``
* **Permission:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION``
* **URL Params:** ``payment_method_id`` (required)
* **Query Params:** ``enterprise_customer_uuid`` (required)
* **Response:** ``{'message': 'Payment method deleted successfully'}``
* **Status Codes:** 200 (success), 403 (permission denied), 404 (not found), 409 (conflict), 422 (Stripe error)
* **Business Rules:**

  * Cannot delete if only payment method on account (returns 409)
  * Cannot delete if default payment method when others exist (returns 409)

Transactions
^^^^^^^^^^^^

**GET /transactions/**

List invoices/transactions for a Stripe customer with pagination.

* **URL Name:** ``billing-management-list-transactions``
* **Permission:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION``
* **Query Params:**

  * ``enterprise_customer_uuid`` (required)
  * ``limit`` (optional, default: 10, max: 25)
  * ``page_token`` (optional, for pagination)

* **Response:** ``TransactionsListResponseSerializer``

  * Returns array of transactions with metadata
  * ``next_page_token`` for pagination (if ``has_more: true``)
  * Each transaction includes: ``id``, ``created``, ``amount``, ``status``, ``pdf_url``, ``receipt_url``

* **Status Codes:** 200 (success), 400 (invalid params), 403 (permission denied), 404 (not found), 422 (Stripe error)
* **Status Normalization:** Maps Stripe ``draft`` status to ``open``

Subscriptions
^^^^^^^^^^^^^

**GET /subscription/**

Get subscription details including plan type, license count, and billing information.

* **URL Name:** ``billing-management-get-subscription``
* **Permission:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION``
* **Query Params:** ``enterprise_customer_uuid`` (required)
* **Response:** ``SubscriptionResponseSerializer``

  * Returns ``{'subscription': null}`` if no active subscription
  * Plan types: ``'Teams'``, ``'Essentials'``, ``'LearnerCredit'``, ``'Other'``
  * Includes: ``plan_type``, ``yearly_amount``, ``license_count``, ``current_period_end``, ``cancel_at_period_end``

* **Status Codes:** 200 (success), 403 (permission denied), 422 (Stripe error)
* **Plan Type Resolution:** ``price.metadata.plan_type`` → ``product.metadata.plan_type`` → ``'Other'``

**POST /subscription/cancel/**

Request cancellation of subscription at period end (not immediate).

* **URL Name:** ``billing-management-cancel-subscription``
* **Permission:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION``
* **Query Params:** ``enterprise_customer_uuid`` (required)
* **Response:** ``CancelSubscriptionResponseSerializer``

  * Returns subscription with ``cancel_at_period_end: true``

* **Status Codes:** 200 (success), 403 (permission denied/invalid plan), 404 (no subscription), 409 (already cancelling), 422 (Stripe error)
* **Business Rules:**

  * Only allowed for ``'Teams'`` and ``'Essentials'`` plans (403 for others)
  * Returns 409 if already scheduled for cancellation

**POST /subscription/reinstate/**

Reinstate a subscription that was scheduled for cancellation.

* **URL Name:** ``billing-management-reinstate-subscription``
* **Permission:** ``BILLING_MANAGEMENT_ACCESS_PERMISSION``
* **Query Params:** ``enterprise_customer_uuid`` (required)
* **Response:** ``ReinstateSubscriptionResponseSerializer``

  * Returns subscription with ``cancel_at_period_end: false``

* **Status Codes:** 200 (success), 403 (permission denied/invalid plan), 404 (no subscription), 409 (not pending cancellation/period ended), 422 (Stripe error)
* **Business Rules:**

  * Only allowed for ``'Teams'`` and ``'Essentials'`` plans (403 for others)
  * Returns 409 if not currently scheduled for cancellation
  * Returns 409 if subscription period has already ended

Common Patterns
---------------

RBAC Permission Checks
^^^^^^^^^^^^^^^^^^^^^^

All endpoints use the ``@permission_required`` decorator with ``BILLING_MANAGEMENT_ACCESS_PERMISSION``:

.. code-block:: python

    @permission_required(
        BILLING_MANAGEMENT_ACCESS_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )

* Permission check runs **before** view code executes
* Missing ``enterprise_customer_uuid`` → 403 FORBIDDEN
* Non-existent enterprise → 403 FORBIDDEN
* User lacks permission → 403 FORBIDDEN

CheckoutIntent Lookup
^^^^^^^^^^^^^^^^^^^^^

All endpoints follow the same pattern for Stripe customer ID resolution:

1. Extract ``enterprise_customer_uuid`` from query parameters
2. Use the enterprise's ``EnterpriseStripeCustomer`` row; without one, the newest ``CheckoutIntent`` with a
   non-empty ``stripe_customer_id``
3. Return 404 if neither is found. The subscription endpoints need the ``CheckoutIntent`` itself and return 404
   without one.

Error Handling
^^^^^^^^^^^^^^

Standard error response format:

.. code-block:: json

    {
        "error": "Descriptive error message"
    }

**Status Code Mapping:**

* ``400 BAD_REQUEST``: Missing required parameters, validation errors
* ``403 FORBIDDEN``: Permission denied, RBAC checks fail
* ``404 NOT_FOUND``: Resource not found (enterprise, customer, payment method, subscription)
* ``409 CONFLICT``: Business rule violation (e.g., cannot delete only payment method)
* ``422 UNPROCESSABLE_ENTITY``: Stripe API errors

Testing
-------

All endpoints have comprehensive test coverage in ``enterprise_access/apps/api/v1/tests/test_customer_billing.py``:

* Success scenarios (admin and operator roles)
* Permission checks (learner role denied)
* Missing/invalid parameters
* Non-existent resources
* Stripe API error handling
* Business rule validation

**Test Execution:**

.. code-block:: bash

    docker compose exec app bash -c \
      "DJANGO_SETTINGS_MODULE=enterprise_access.settings.test \
       pytest -c pytest.local.ini \
       enterprise_access/apps/api/v1/tests/test_customer_billing.py::BillingManagement*Tests -v"

Related Documentation
---------------------

* :doc:`subscription-and-renewal-lifecycle` - Full subscription lifecycle documentation
* ``docs/stripe-billing-architecture.md`` - Overall Stripe billing architecture
* ``docs/checkout_bff.rst`` - Checkout BFF API documentation

Implementation Notes
--------------------

**Caching Strategy:**

The ``@stripe_cache()`` decorator is used on Stripe API wrapper functions to reduce redundant calls:

* Cache key: ``stripe_{object_type}_{stripe_id}``
* Default TTL: 5 minutes
* Cache backend: ``TieredCache`` (Memcache → Django cache)

**When testing**, mock the underlying Stripe API directly (e.g., ``stripe.Customer.retrieve``) rather than the cached wrapper function.

**Stripe API Rate Limits:**

Stripe enforces rate limits on API calls. The billing management API does not implement additional rate limiting,
relying on Stripe's built-in limits and the caching layer to reduce load.

**Frontend Integration:**

The Admin Portal MFE consumes these endpoints to provide a self-service billing management interface.
All endpoints return JSON responses suitable for direct consumption by React components.
