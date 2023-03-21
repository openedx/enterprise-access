0006 API Specification for Enterprise Micro-frontends (MFEs)
************************************************************

Status
======

Accepted (March 2023)

Context
=======

This document intends to outline which API endpoint(s) exist or will exist in support of the
Enterprise MFEs (e.g., frontend-app-learner-portal-enterprise) as well as other consumers,
or service-to-service communication.

API Endpoints
=============

GET Retrieve single, redeemable access policy for a set of content keys
-----------------------------------------------------------------------

**/api/v1/policy/enterprise-customer/<enterprise_customer_uuid>/can_redeem/?content_key=...&content_key=...**

This API endpoint will be called by the enterprise learner portal to understand whether
the learner is already enrolled in any of the available course runs (i.e., a prior redemption has been successfully
fulfilled) and/or which subsidy access policy should be used to redeem each course run when a learner
clicks the "Enroll" button. 

The redemption fulfillment status is retrieved from the ``/api/v1/transactions/<transaction_uuid>/`` API endpoint in ``enterprise-subsidy``,
which returns an individual transaction and its current state (i.e., ``created``, ``pending``, ``committed``, ``failed``).

The course page in the enterprise learner portal displays an "Enroll" or "View course" CTA for each course run of the
course being viewed. Given that we're now using the redemption fulfillment status as a proxy for whether the learner
is already enrolled, we will need to know the fulfillment status for each course run. To avoid the frontend needing to
make N API requests to ``can_redeem`` (i.e., one per course run), this API endpoint will return the redemption's fulfillment
status and a redeemable policy for each course run.

This API endpoint works by iterating over all subsidy access policies associated with
the enterprise customer for the specified learner to understand which subsidy access policies are indeed
redeemable for each course run. It does this by calling the ``can_redeem`` API endpoint in ``enterprise-subsidy`` for each active
subsidy access policy. In the event multiple subsidy access policies are found, this API endpoint chooses
the preferred subsidy based on defined business rules.

This API endpoint differs from the one spec'd in the `0003 Initial API Specification`_ in that
it makes a decision of which single subsidy access policy should be redeemed for a given course in the event
a learner has multiple redeemable subsidy access policies. The API endpoint that's already spec'd returns a
list of redeemable policies, which would mean the client  (e.g., enterprise learner portal) still requires business
logic to make a choice of which redeemable policy to attempt a redemption. This new API endpoint would thus remove the
need for such business logic in the client given the subsidy access policy choice is abstracted into the API layer instead.

*Inputs (query parameters):*

* ``lms_user_id``
* ``content_key`` (i.e., one or more course run keys)
* ``enterprise_customer_uuid`` might actually make more sense as a query parameter, too. TBD at implementation time.

*Outputs:*

For each specified ``content_key`` (i.e., course run key), returns the following:

* A single, redeemable subsidy access policy (if any).
* Redemption status of the single, redeemable subsidy access policy (if any). This is largely a serialized ``Transaction`` from ``enterprise-subsidy``, where the redemption UUID is a ``Transaction`` UUID.
* List of reasons for why there is no redeemable subsidy access policy.

Sample API responses
^^^^^^^^^^^^^^^^^^^^

::

  [
    {
      "course_run_key": "course-v1:ImperialX+dacc003+3T2019",
      "redemption": {
        "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
        "state": "committed",
        "policy_redemption_status_url": "https://enterprise-subsidy.edx.org/api/v1/transactions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
        "courseware_url": "https://courses.edx.org/courses/course-v1:ImperialX+dacc003+3T2019/courseware/",
        "errors": []
      },
      "subsidy_access_policy": {
        "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
        "policy_redemption_url": "https://enterprise-access.edx.org/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redeem/",
        "policy_type": "LearnerCreditAccessPolicy",
        "description": "Learner credit access policy",
        "active": true,
        "catalog_uuid": "14f701ea-7e0b-4a4e-bbda-f295e40c7bf1",
        "subsidy_uuid": "7801b0ef-b1c2-4f3a-97fa-121f0bce48be",
        "access_method": "direct",
        "spent_limit": 10000,
        "per_learner_spend_limit": 200,
        "remaining_balance": 9500,
        "remaining_balance_for_learner": 200
      },
      "reasons": []
    }
  ]

*No redeemable subsidy access policies available to the learner:*

::

  [
    {
      "course_run_key": "course-v1:ImperialX+dacc003+3T2019",
      "redemption": null,
      "subsidy_access_policy": null,
      "reasons": [
        {
          "reason": "Insufficient balance remaining",
          "detail": "Not enough funds available for the course."
        }
      ]
    }
  ]

*Redeemable subsidy access policy that has not yet been redeemed and/or fulfilled:*

::

  [
    {
      "course_run_key": "course-v1:ImperialX+dacc003+3T2019",
      "redemption": null,
      "subsidy_access_policy": {
        "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
        "policy_redemption_url": "https://enterprise-access.edx.org/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redeem/",
        "policy_type": "LearnerCreditAccessPolicy",
        "description": "Learner credit access policy",
        "active": true,
        "catalog_uuid": "14f701ea-7e0b-4a4e-bbda-f295e40c7bf1",
        "subsidy_uuid": "7801b0ef-b1c2-4f3a-97fa-121f0bce48be",
        "access_method": "direct",
        "spent_limit": 10000,
        "per_learner_spend_limit": 200,
        "remaining_balance": 9500,
        "remaining_balance_for_learner": 200
      },
      "reasons": []
    }
  ]

*Redeemable subsidy access policy that has been redeemed but is pending fulfillment:*

::

  [
    {
      "course_run_key": "course-v1:ImperialX+dacc003+3T2019",
      "redemption": {
        "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
        "state": "pending",
        "policy_redemption_status_url": "https://enterprise-subsidy.edx.org/api/v1/transactions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
        "courseware_url": null,
        "errors": []
      },
      "subsidy_access_policy": {
        "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
        "policy_redemption_url": "https://enterprise-access.edx.org/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redeem/",
        "policy_type": "LearnerCreditAccessPolicy",
        "description": "Learner credit access policy",
        "active": true,
        "catalog_uuid": "14f701ea-7e0b-4a4e-bbda-f295e40c7bf1",
        "subsidy_uuid": "7801b0ef-b1c2-4f3a-97fa-121f0bce48be",
        "access_method": "direct",
        "spent_limit": 10000,
        "per_learner_spend_limit": 200,
        "remaining_balance": 9500,
        "remaining_balance_for_learner": 200
      },
      "reasons": []
    }
  ]

*Redeemable subsidy access policy that has been successfully redeemed and fulfilled:*

::

  [
    {
      "course_run_key": "course-v1:ImperialX+dacc003+3T2019",
      "redemption": {
        "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
        "state": "committed",
        "policy_redemption_status_url": "https://enterprise-subsidy.edx.org/api/v1/transactions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
        "courseware_url": "https://courses.edx.org/courses/course-v1:ImperialX+dacc003+3T2019/courseware/",
        "errors": []
      },
      "subsidy_access_policy": {
        "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
        "policy_redemption_url": "https://enterprise-access.edx.org/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redeem/",
        "policy_type": "LearnerCreditAccessPolicy",
        "description": "Learner credit access policy",
        "active": true,
        "catalog_uuid": "14f701ea-7e0b-4a4e-bbda-f295e40c7bf1",
        "subsidy_uuid": "7801b0ef-b1c2-4f3a-97fa-121f0bce48be",
        "access_method": "direct",
        "spent_limit": 10000,
        "per_learner_spend_limit": 200,
        "remaining_balance": 9500,
        "remaining_balance_for_learner": 200
      },
      "reasons": []
    }
  ]

*Redeemable subsidy access policy that has been redeemed, but failed during fulfillment:*

::

  [
    {
      "course_run_key": "course-v1:ImperialX+dacc003+3T2019",
      "redemption": {
        "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
        "state": "failed",
        "policy_redemption_status_url": "https://enterprise-subsidy.edx.org/api/v1/transactions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
        "courseware_url": null,
        "errors": [
          {
            "code": 500,
            "message": "Something went wrong. Please try again.",
          }
        ]
      },
      "subsidy_access_policy": {
        "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
        "policy_redemption_url": "https://enterprise-access.edx.org/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redeem/",
        "policy_type": "LearnerCreditAccessPolicy",
        "description": "Learner credit access policy",
        "active": true,
        "catalog_uuid": "14f701ea-7e0b-4a4e-bbda-f295e40c7bf1",
        "subsidy_uuid": "7801b0ef-b1c2-4f3a-97fa-121f0bce48be",
        "access_method": "direct",
        "spent_limit": 10000,
        "per_learner_spend_limit": 200,
        "remaining_balance": 9500,
        "remaining_balance_for_learner": 200
      },
      "reasons": []
    }
  ]

.. _0003 Initial API Specification: 0003-initial-api-specification.rst