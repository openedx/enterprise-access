0029 Self-Service Purchasing BFF Endpoints
******************************************

Status
======
**In progress** (June 2025)

Context
=======

The self-service purchasing feature will be comprised of primarily the Checkout
MFE and a set of backend endpoints (some leveraging BFF framework). This ADR
should help to define those backend endpoints.

The checkout flow is visualized via the following design mockups:

1. `design mockup (old)
<https://www.figma.com/board/DiZO3HwkQNdElBc5av5DG3/DR--Self-Service-Subscription-Flows?node-id=0-1&p=f&t=VkRFRxPmCAXyZ1bc-0>`_

2. `design mockup (new)
<https://www.figma.com/board/xWaO5vDxrxSLVB5lDhhEFm/Self-Service-designs?node-id=27-8568&t=XidhCb0byCfFzn8G-0>`_

The above design mockups broadly depict these main pages:

1. Create your account

   * Authentication:

     * JWT Authentication on initial page load.

     * Otherwise, either a login modal or registration modal is triggered on page submit.

   * Fields captured:

     * Full Name

     * Work Email

     * Company Name

     * Country

   * Registration modal, if displayed, will include `standard user registration fields <https://github.com/openedx/edx-platform/blob/033bcda9/openedx/core/djangoapps/user_authn/views/registration_form.py#L141>`_.

     * Modal Fields captured:

       * Full Name (inactive, copied from form)

       * Email (inactive, copied from form)

       * Username

       * Password

       * Country (inactive, copied from form)

   * Login modal, if displayed, will include basic login fields:

       * Username

       * Password

   * Note: Registration and Login modals will interact directly with edx-platform user_authn API endpoints to perform login or registration.

2. Build your trial

   * Only JWT authenticated users are authorized.

   * Fields captured:

     * Number of Licenses

     * Company Name (inactive)

     * Enterprise Slug

3. Start Trial

   * Only JWT authenticated users are authorized.

   * Fields captured:

     * Combination of `Address Element <https://docs.stripe.com/elements/address-element>`_ and `Payment Element <https://docs.stripe.com/payments/payment-element>`_.

Decision
========

We will implement a RESTful API contract with four primary endpoints following the proposed BFF (Backend-for-Frontend) pattern. The design emphasizes real-time validation, clear authentication boundaries, and stateless server operations with ephemeral form state handled entirely on the frontend.

BFF Endpoints for Checkout Flow
--------------------------------

**1. Cross-Page Context Endpoint**

.. code-block::

    POST /api/v1/bffs/checkout/context

    Authentication: JWT
    Authorization: Authenticated OR Unauthenticated.
    Purpose: Supply any relevant admin context, and pricing options. Incorporate into Loaders.
    Side-Effects: None

    Request:
    {}

    Response (200 OK):
    {
        "existing_customers_for_authenticated_user": [
            {
                "customer_uuid": "",
                "customer_name": "",
                "customer_slug": "",
                "stripe_customer_id": "",
                "is_self_service": False,
                "admin_portal_url": ""
            }
        ],
        "pricing": {
            "default_by_lookup_key": "b2b_enterprise_self_service_yearly",
            "prices": [
                {
                    "id": "price_1MoBy5LkdIwHu7ixZhnattbh",
                    "product": "prod_NZKdYqrwEYx6iK",
                    "lookup_key": "b2b_enterprise_self_service_yearly",
                    "recurring": {
                      "interval": "month",
                      "interval_count": 12,
                      "trial_period_days": 14,
                    },
                    "currency": "usd",
                    "unit_amount": 1000,
                    "unit_amount_decimal": "1000"
                }
            ]
        },
        "field_constraints": {
          "quantity": { "min": 5, "max": 30 },
          "enterprise_slug": { "min_length": 3, "max_length": 30, "pattern": "^[a-z0-9-]+$" }
        }
    }

As an implementation note for the ``prices`` list, pre-filter the
Stripe API response to just the Price objects which are supported by the
checkout flow. I.e. filter prices on the following fields:

- ``active`` = ``true`` (exclude outdated prices)

- ``billing_scheme`` = "per_unit" (exclude Tiered schemes)

- ``livemode`` = ``true`` (exclude test-only prices)

- ``type`` = "recurring" (exclude non-recurring prices)

- ``recurring.usage_type`` =  "licensed" (exclude metered pricing)

The ``field_constraints`` section is likely to contain very slow-moving data,
but the benefit over static config is that the constraints can be derived
directly from the backend field definitions which serve as a source of truth.
This avoids needing to sync constraints across multiple repositories.

**2. Cross-Page Validation Endpoint**

.. code-block::

    POST /api/v1/bffs/checkout/validation

    Authentication: JWT
    Authorization: Authenticated OR Unauthenticated.
    Purpose: Validate form fields across all form pages, leveraging LMS API calls to check user existence and slug conflicts
    Optional Fields: full_name, work_email, company_name, enterprise_slug, stripe_price_id, quantity
    Side-Effects: None
    Frontend consumers: Build Trial page, Create Account page

    Request:
    {
        "full_name": "John Doe",
        "work_email": "admin@example.com",
        "company_name": "Example Corporation",
        "enterprise_slug": "example-corp",
        "quantity": 10,
        "stripe_price_id": "price_1MoBy5LkdIwHu7ixZhnattbh"
    }

    Response (200 OK - Valid):
    {
        "validation_decisions": {
            "full_name": null,
            "work_email": null,
            "company_name": null,
            "enterprise_slug": null,
            "quantity": null,
            "stripe_price_id": null
        },
        "user_authn": {
            "user_exists_for_email": true
        },
    }

    Response (400 Bad Request - Validation Errors):
    {
        "validation_decisions": {
            "work_email": {
                "error_code": "invalid_format",
                "developer_message": "Email format validation failed"
            },
            "enterprise_slug": {
                "error_code": "existing_enterprise_customer",
                "developer_message": "The slug conflicts with an existing customer."
            },
            "quantity": {
                "error_code": "range_exceeded",
                "developer_message": "Quantity 50 exceeds allowed range [5, 30] for stripe_price_id"
            },
            "company_name": {
                "error_code": "required_field",
                "developer_message": "Company name cannot be empty"
            }
        },
        "user_authn": {
            "user_exists_for_email": null
        },
    }

If possible, make ``enterprise_slug`` validation require the call to be
authenticated. This should help mitigate unauthenticated bots from inducing
many indirect API calls from enterprise-access to the LMS.

The ``user_authn`` section represents data resulting from API calls to the
`user_authn API <https://github.com/openedx/edx-platform/blob/4d4f8f457d321faf665ed859a40e7df9e4978617/openedx/core/djangoapps/user_authn/urls.py>`_.

Customer Billing Endpoints
---------------------------

**3. Create Checkout Session**

.. code-block::

    POST /api/v1/customer-billing/create-checkout-session

    Authentication: JWT (required)
    Authorization: Any authenticated user
    Purpose: Called on submit of the "Build Trial" page to prepare a new stripe checkout session for the subsequent "checkout" page
    Side-Effects: "Reserve" the slug for as long as the checkout session lasts, Create the Stripe Checkout Session.
    Frontend consumer: Build Trial page

    Request:
    {
        "admin_email": "admin@example.com",
        "enterprise_slug": "example-corp",
        "quantity": 10,
        "stripe_price_id": "price_1MoBy5LkdIwHu7ixZhnattbh"
    }

    Response (201 Created):
    {
        "checkout_session": {
            "client_secret": "cs_test_1234567890abcdef",
            "expires_at": "1751323210"
        }
    }

    Response (422 Unprocessable Entity - Validation Failed):
    {
        "admin_email": {
            "error_code": "not_registered",
            "developer_message": "The provided email has not yet been registered."
        },
        "enterprise_slug": {
            "error_code": "existing_enterprise_customer",
            "developer_message": "Slug conflicts with existing customer."
        }
    }

**4. Create Portal Session**

.. code-block::

    GET /api/v1/customer-billing/<customer_uuid>/portal-session

    Authentication: JWT (required)
    Authorization: Only allow admins for the given customer_uuid
    Purpose: URL for any "Billing Portal" button (placed anywhere in admin portal or emails). 302 Redirect to Billing Portal URL.
    Side-Effects: Create a new Stripe Billing Portal Session (consider rate limiting and caching).

    Response (302 Redirect):
    Location: https://billing.stripe.com/session/bps_1234567890abcdef

**5. Stripe Webhook Handler**

.. code-block::

    POST /api/v1/customer-billing/stripe-webhook

    Authentication: Payload signature validation
    Authorization: Only allow Stripe system user
    Purpose: Receive specific Stripe events to trigger email communications with the admin as needed
    Side-Effects: Permanent storage of event payload in DB, Possible triggering of Celery task

    Supported webhook events:
    - checkout.session.completed
    - invoice.paid
    - customer.subscription.trial_will_end
    - customer.subscription.deleted
    - payment_method.attached

    Request (from Stripe):
    {
        "id": "evt_1MoBy5LkdIwHu7ixZhnattbh",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1234567890abcdef",
                "customer": "cus_test_customer123",
                "subscription": "sub_test_subscription456"
            }
        }
    }

    Response (200 OK):
    {
        "received": true,
        "event_id": "evt_1MoBy5LkdIwHu7ixZhnattbh"
    }


Form State Management
---------------------

Form state persistence is handled entirely on the frontend using in-memory
state management.  Here is an example ``zustand`` state hook:

.. code-block:: javascript

    // Frontend state management strategy
    import { create } from 'zustand';

    const useCheckoutFormStore = create<FormStore>(
      (set) => ({
        formData: {
          account: {},  // page 1 state.
          trial: {},    // page 2 state.
        },
        setFormData: (step, data) => set(
          (store) => ({
            formData: {
              ...store.formData,
              [step]: data,
            },
          }),
        ),
      }),
    );

    export default useCheckoutFormStore;

**In-Memory State Management Benefits:**

- Simplified backend implementation.

- Privacy-focused (no server-side storage of form data).

- Automatic state clearing on tab close.

Error Codes
-----------

**Common Error Codes:**

- ``invalid_format``: Basic format validation failures.

- ``incomplete_data``: Validation requires another field which was not included.

**Quantity Error Codes:**

- ``range_exceeded``: Numeric values outside allowed ranges.

**Enterprise Slug Error Codes:**

- ``existing_enterprise_customer``: Slug conflicts with existing customers.

**Email Error Codes:**

- ``not_registered``: Email not registered.

**Stripe Price ID Error Codes:**

- ``does_not_exist``: This stripe_price_id has not been configured.

Rate Limiting
-------------

Rate limiting applied following existing enterprise-access patterns:

.. code-block:: python

    # BFF endpoints
    @ratelimit(key='ip', rate='20/m', method='POST', block=False) # Context
    @ratelimit(key='ip', rate='60/m', method='POST', block=False)  # Validation

    # Customer billing endpoints (existing patterns)
    @ratelimit(key='user', rate='5/m', method='POST', block=False)  # Checkout session
    @ratelimit(key='user', rate='10/m', method='POST', block=False)   # Portal session

Integration Points
------------------

**edx-platform Integration:**

- Frontend: Login and Registration modals in frontend directly call existing user_authn LMS endpoints:

  - Login:

    - ``POST <LMS>/api/user/v2/account/login_session/``

    - Reference `loginRequest() logic <https://github.com/openedx/frontend-app-authn/blob/e9aaf70/src/login/LoginPage.jsx#L155-L161>`_ from frontend-app-authn.

  - Registration (validation and account creation as separate endpoints):

    - ``POST <LMS>/api/user/v1/validation/registration``

    - ``POST <LMS>/api/user/v2/account/registration/``

    - Reference `registerRequest() logic <https://github.com/openedx/frontend-app-authn/blob/1b5aa10/src/register/data/service.js#L5-L26>`_ from frontend-app-authn.

- Backend validation endpoint:

    - Leverages same registration validation endpoint to confirm email existence:

      - ``POST <LMS>/api/user/v1/validation/registration``

        Request::

          { "email": "foobar@example.com" }

        Response (email exists)::

          { "validation_decisions": { "email": "This email is already associated with an existing account" } }

        Response (email available)::

          { "validation_decisions": { "email": "" } }


**Stripe Integration:**

- Submit button on Build Your Trial page calls create-checkout-session endpoint which calls  Stripe APIs to create a Stripe "Checkout Session".

- "Customer Billing" buttons hooked up to get-customer-billing endpoint which calls Stripe APIs to create a Stripe "Billing Portal".

**Salesforce Integration:**

- No direct integration from backend endpoints (future consideration for lead generation).

- Indirect integration: final provisioning handled via existing Stripe events → Salesforce → Provisioning API flow.

Alternatives Considered
=======================

**1. Server-Side State Management vs. Frontend-Only State Persistence**

*Alternative:* Store checkout progress and form data in server-side database or sessions.

*Rejected because:*
- Adds unnecessary complexity to backend state management
- Requires database PII cleanup and expiration handling

*Chosen approach:* Frontend-only state management using in-memory state for simplicity and security.

**2. Separate Validation Endpoint Per Page vs. Bulk Validation Endpoint**

*Alternative:* Individual validation BFF endpoints for each page (e.g., ``/api/v1/bffs/checkout/page1/validation``, ``/api/v1/bffs/checkout/page2/validation``, etc.).

*Rejected because:*
- Increases API surface area and maintenance burden
- Harder to implement cross-field validation logic when fields are on different pages.

*Chosen approach:* Single validation endpoint that accepts optional fields for flexible validation.

Consequences
============

*Positive consequences:*

- Stripe handles all payment data securely.

- We would not need to implement Specially Designated Nationals (SDN) checks as it is automatically handled by Stripe.

- We (Enterprise) would not need to implement handle "embargo" checks as it is automatically handled by LMS registration API endpoints.

*Negative consequences:*

- No server-side state means no abandoned-cart recovery.

- API error codes must be kept in sync between frontend and backend.
