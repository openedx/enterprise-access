0006 API Specification for Enterprise Micro-frontends (MFEs)
************************************************************

Status
======

Accepted (March 2023)

Context
=======

This document intends to outline which API endpoints exist or will exist in support of the
Enterprise MFEs (e.g., frontend-app-learner-portal-enterprise) as well as other consumers,
or service-to-service communication.

API Endpoints
=============

Retrieve single, redeemable access policy for a course
------------------------------------------------------

**/api/v1/enterprise-customer/<enterprise_customer_uuid>/policy/can_redeem/**

This API endpoint will be called by the enterprise learner portal to understand whether
the learner is already enrolled in the course (i.e., a prior redemption has been successfully
fulfilled) and/or which subsidy access policy should be used to redeem the course when a learner
clicks the "Enroll" button.

*Inputs:*

* `lms_user_id`
* `content_id` (i.e., course id)

*Outputs:*

* A single, redeemable subsidy access policy.
* Redemption status of the single, redeemable subsidy access policy.

Sample API responses
^^^^^^^^^^^^^^^^^^^^

*Redeemable subsidy access policy that has not yet been redeemed and/or fulfilled:*

::

  {
    "redemption": null,
    "subsidy_access_policy": {
      "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
      "policy_redemption_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redeem/",
      "policy_type": "LearnerCreditAccessPolicy",
      "description": "Learner credit access policy",
      "active": true,
      "catalog_uuid": "14f701ea-7e0b-4a4e-bbda-f295e40c7bf1",
      "subsidy_uuid": "7801b0ef-b1c2-4f3a-97fa-121f0bce48be",
      "access_method": "direct",
      "spent_limit": 10000,
      "per_learner_spend_limit": 200,
    }
  }

*Redeemable subsidy access policy that has been redeemed but is pending fulfillment:*

::

  {
    "redemption": {
      "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
      "status": "pending",
      "policy_redemption_status_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redemptions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
      "courseware_redirect_url": null,
      "error_status_code": null,
      "error_message": null,
    },
    "subsidy_access_policy": {
      "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
      "policy_redemption_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redeem/",
      "policy_type": "LearnerCreditAccessPolicy",
      "description": "Learner credit access policy",
      "active": true,
      "catalog_uuid": "14f701ea-7e0b-4a4e-bbda-f295e40c7bf1",
      "subsidy_uuid": "7801b0ef-b1c2-4f3a-97fa-121f0bce48be",
      "access_method": "direct",
      "spent_limit": 10000,
      "per_learner_spend_limit": 200,
    },
  }

*Redeemable subsidy access policy that has been successfully redeemed and fulfilled:*

::

  {
    "redemption": {
      "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
      "status": "fulfilled",
      "policy_redemption_status_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redemptions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
      "courseware_redirect_url": "https://learning.edx.org",
      "error_status_code": null,
      "error_message": null,
    },
    "subsidy_access_policy": {
      "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
      "policy_redemption_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redeem/",
      "policy_type": "LearnerCreditAccessPolicy",
      "description": "Learner credit access policy",
      "active": true,
      "catalog_uuid": "14f701ea-7e0b-4a4e-bbda-f295e40c7bf1",
      "subsidy_uuid": "7801b0ef-b1c2-4f3a-97fa-121f0bce48be",
      "access_method": "direct",
      "spent_limit": 10000,
      "per_learner_spend_limit": 200,
    },
  }

*Redeemable subsidy access policy that has been redeemed, but failed during fulfillment:*

::

  {
    "redemption": {
      "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
      "status": "error",
      "policy_redemption_status_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redemptions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
      "courseware_redirect_url": null,
      "error_status_code": 400,
      "error_message": "Something went wrong. Please try again.",
    },
    "subsidy_access_policy": {
      "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
      "policy_redemption_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redeem/",
      "policy_type": "LearnerCreditAccessPolicy",
      "description": "Learner credit access policy",
      "active": true,
      "catalog_uuid": "14f701ea-7e0b-4a4e-bbda-f295e40c7bf1",
      "subsidy_uuid": "7801b0ef-b1c2-4f3a-97fa-121f0bce48be",
      "access_method": "direct",
      "spent_limit": 10000,
      "per_learner_spend_limit": 200,
    },
  }

Retrieve the fulfillment status for a policy redemption
--------------------------------------------------------

**/api/v1/enterprise-customer/<enterprise_customer_uuid>/policy/<policy_uuid>/redemptions/<redemption_uuid>/**

When the policy-specific `redeem` endpoint is called (e.g., when learner clicks "Enroll" button on course page), it returns
with a redemption UUID that may be used to query against to understand the status of the redemption's fulfillment which, by
design, may be asynchronous.

As such, this API endpoint intends to be used to check the fulfillment status of a redemption to communicate to consumers that
any side effects from the redemption have been successfully completed.

*Inputs:*

None other than the arguments in the URL path for the endpoint.

*Outputs:*

* A single, redeemable subsidy access policy.
* Redemption status of the single, redeemable subsidy access policy.

Sample API responses
^^^^^^^^^^^^^^^^^^^^

*Redemption with successful fulfillment*

::

  {
    "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
    "status": "fulfilled",
    "policy_redemption_status_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redemptions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
    "courseware_redirect_url": "https://learning.edx.org",
    "error_status_code": null,
    "error_message": null,
  }

*Redemption with pending fulfillment*

::

  {
    "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
    "status": "pending",
    "policy_redemption_status_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redemptions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
    "courseware_redirect_url": null,
    "error_status_code": null,
    "error_message": null,
  }

*Redemption with error during fulfillment*

::

  {
    "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
    "status": "error",
    "policy_redemption_status_url": "/api/v1/policy/56744a36-93ac-4e6c-b998-a2a1899f2ae4/redemptions/26cdce7f-b13d-46fe-a395-06d8a50932e9/",
    "courseware_redirect_url": null,
    "error_status_code": 400,
    "error_message": "Something went wrong. Please try again.",
  }