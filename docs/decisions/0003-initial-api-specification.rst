0003 Initial API Specification
##############################

Status
******

**Draft**

Context
*******

This doc describes the API specification for the ``subsidy-access-policy`` Django app in this repo.



Open Questions and TODOs
========================



Primary Access Policy Route
***************************
**/api/v1/policy/**
The root URL for all actions allowed on access policy apis.


Enterprise Access Policy Transactions
**************************************

GET all redeemable policies
======================================
**/api/v1/policy/**

List all redeemable access policies

Inputs
------

- ``enterprise_customer_uuid`` (Query Param, required): The uuid of the enterprise customer.
- ``learner_id`` (Query Param, required): The user for whom the transaction is written and for which a enrollment should occur.
- ``content_key`` (Query Param, required): The content for which a enrollment is created.

Outputs
-------
A sample response can be seen below.

::

   [
    {
        "uuid": "9e63269a-1e80-4371-b7fe-a8878973b4d6",
        "policy_redemption_url": "/api/v1/policy/9e63269a-1e80-4371-b7fe-a8878973b4d6/redeem/",
        "policy_type": "SubscriptionAccessPolicy",
        "description": "Subscription access policy",
        "active": true,
        "group_uuid": "12aacfee-8ffa-4cb3-bed1-059565a57f06",
        "enterprise_customer_uuid": "13aacfee-8ffa-4cb3-bed1-059565a57f06",
        "catalog_uuid": "4ccd4516-0c4d-45b5-b653-b28e158322c2",
        "subsidy_uuid": "103cdc29-d633-4384-87b0-bc699f2307c5",
        "access_method": "direct",
        "per_learner_enrollment_limit": 0,
        "per_learner_spend_limit": 0,
        "spend_limit": 0
    }
]

Permissions
-----------

enterprise_learner or enterprise_admin
  Should only create the transaction if the requesting user has implicit (JWT) or explicit (DB-defined)
  ``enterprise_learner`` or ``enterprise_admin`` role assigned.

POST access policy redeem transaction
======================================
**/api/v1/policy/<policy_uuid>/redeem/**

Redeem subsidy value by making a request to enterprise-subsidy service.

Inputs
------

- ``enterprise_customer_uuid`` (POST data, required): The uuid of the enterprise customer.
- ``learner_id`` (POST data, required): The user for whom the transaction is written and for which a enrollment should occur.
- ``content_key`` (POST data, required): The content for which a enrollment is created.

Outputs
-------
Not Finalized

Permissions
-----------

enterprise_learner
  Should only create the transaction if the requesting user has implicit (JWT) or explicit (DB-defined)
  ``enterprise_learner`` role assigned.

GET access policy redemption
===============================
**/api/v1/policy/redemption/**

Return a list of all redemptions belong to a learner for a given `enterprise_customer_uuid`, `learner_id` and `content_key`.

Inputs
------

- ``enterprise_customer_uuid`` (Query Param, required): The uuid of the enterprise customer.
- ``learner_id`` (Query Param, required): The lms user id for whom we need to check redemptions for.
- ``content_key`` (Query Param, required): The content for which a enrollment is created.

Outputs
-------
Not Finalized

Permissions
-----------

enterprise_learner
  Requesting user has implicit (JWT) or explicit (DB-defined) ``enterprise_learner`` role assigned.


GET access policies with redeemable credits
===============================
**/api/v1/policy/credits_available/**

Return a list of all policies for the given `enterprise_customer_uuid`, that the given learner can redeem, irrespective of the content_key

Inputs
------

- ``enterprise_customer_uuid`` (Query Param, required): The enterprise customer uuid that the learner is linked to.
- ``lms_user_id`` (Query Param, required): The lms user id for whom we need to check redemptions for.

Outputs
-------
A sample response can be seen below.

::

   [
       {
          "uuid":"348257e0-14bd-4775-91da-226271787c33",
          "policy_redemption_url":"/api/v1/policy/348257e0-14bd-4775-91da-226271787c33/redeem/",
          "remaining_balance_per_user":200,
          "remaining_balance":100,
          "policy_type":"PerLearnerSpendCreditAccessPolicy",
          "enterprise_customer_uuid":"12aacfee-8ffa-4cb3-bed1-059565a57f06",
          "description":"",
          "active":false,
          "catalog_uuid":"0092421c-cee2-4982-9a10-a9bce9ade9be",
          "subsidy_uuid":"9ede40cd-fe12-4249-ba3d-cbe30187ee03",
          "group_uuid":"b9b8b2c5-b8ba-42c8-b9cf-b6d46c01684e",
          "enterprise_customer_uuid": "12aacfee-8ffa-4cb3-bed1-059565a57f06",
          "access_method":"direct",
          "per_learner_enrollment_limit":0,
          "per_learner_spend_limit":5,
          "spend_limit":0
       },
       {
          "uuid":"76bb547f-3196-4b92-a0b6-fd01b3a25cc4",
          "policy_redemption_url":"/api/v1/policy/76bb547f-3196-4b92-a0b6-fd01b3a25cc4/redeem/",
          "remaining_balance_per_user":300,
          "remaining_balance":100,
          "policy_type":"PerLearnerEnrollmentCreditAccessPolicy",
          "enterprise_customer_uuid":"12aacfee-8ffa-4cb3-bed1-059565a57f06",
          "description":"",
          "active":false,
          "catalog_uuid":"fa4c60b8-ec06-42c9-b3fb-2d46d61cbbb2",
          "subsidy_uuid":"b2aeb940-7944-49b5-ab0b-e8c171eaf8f6",
          "group_uuid":"39f3c016-9673-47bc-9f31-6f9a1c1f8698",
          "enterprise_customer_uuid": "12aacfee-8ffa-4cb3-bed1-059565a57f06",
          "access_method":"direct",
          "per_learner_enrollment_limit":5,
          "per_learner_spend_limit":0,
          "spend_limit":0
       }
   ]


Permissions
-----------

enterprise_learner
  Requesting user has implicit (JWT) or explicit (DB-defined) ``enterprise_learner`` role assigned.



CRUD operations on SubsidyAccessPolicy
======================================

GET **/api/v1/admin/policy/**

List all policies linked to an enterprise.

Inputs
------

- ``enterprise_customer_uuid`` (Query Param, required): The uuid of the enterprise customer.

Outputs
-------
A sample response can be seen below.

::

   [
        {
            "uuid": "d0cd25f9-0b73-49c3-8299-dc9751a12ef5",
            "policy_type": "PerLearnerEnrollmentCreditAccessPolicy",
            "description": "sdad",
            "active": true,
            "group_uuid": "214cf999-5964-4a2e-afa8-c62461558211",
            "enterprise_customer_uuid": "12aacfee-8ffa-4cb3-bed1-059565a57f06",
            "catalog_uuid": "214cf999-5964-4a2e-afa8-c62461558315",
            "subsidy_uuid": "214cf999-5964-4a2e-afa8-c62461342198",
            "access_method": "direct",
            "per_learner_enrollment_limit": 0,
            "per_learner_spend_limit": 0,
            "spend_limit": 5
        },
        {
            "uuid": "a16b960d-0ddd-4af2-a596-9991cd5508da",
            "policy_type": "CappedEnrollmentLearnerCreditAccessPolicy",
            "description": "sdad",
            "active": true,
            "group_uuid": "214cf999-5964-4a2e-afa8-c62461558211",
            "enterprise_customer_uuid": "12aacfee-8ffa-4cb3-bed1-059565a57f06",
            "catalog_uuid": "214cf999-5964-4a2e-afa8-c62461558315",
            "subsidy_uuid": "214cf999-5964-4a2e-afa8-c62461342198",
            "access_method": "direct",
            "per_learner_enrollment_limit": 3,
            "per_learner_spend_limit": 0,
            "spend_limit": 0
        },
]

Permissions
-----------

enterprise_admin
  Should only create the transaction if the requesting user has implicit (JWT) or explicit (DB-defined) ``enterprise_admin`` role assigned.


**GET /api/v1/admin/policy/<policy_uuid>/**

Retrieve a subsidy access policy instance.

Inputs
------

- ``policy_uuid`` (URL, required): The uuid of the customer. For now it will be an enterprise customer uuid.

Outputs
-------
A sample response can be seen below.

::

    {
        "uuid": "d0cd25f9-0b73-49c3-8299-dc9751a12ef5",
        "policy_type": "PerLearnerEnrollmentCreditAccessPolicy",
        "description": "sdad",
        "active": true,
        "enterprise_customer_uuid": "214cf999-5964-4a2e-afa8-c62461558211",
        "group_uuid": "204cf999-5964-4a2e-afa8-c62461558211",
        "catalog_uuid": "214cf999-5964-4a2e-afa8-c62461558315",
        "subsidy_uuid": "214cf999-5964-4a2e-afa8-c62461342198",
        "access_method": "direct",
        "per_learner_enrollment_limit": 3,
        "per_learner_spend_limit": 0,
        "spend_limit": 0
    }

Permissions
-----------

enterprise_admin
  Should only create the transaction if the requesting user has implicit (JWT) or explicit (DB-defined) ``enterprise_admin`` role assigned.

**POST /api/v1/admin/policy/**

Create a subsidy access policy instance after validating the request data.

Inputs
------

- ``payload`` (request data, required): Payload data for POST request.

A sample **request** can be seen below.

::

    {
        "policy_type": "PerLearnerEnrollmentCreditAccessPolicy",
        "description": "updated description",
        "active": true,
        "group_uuid": "214cf999-5964-4a2e-afa8-c62461558211",
        "enterprise_customer_uuid": "12aacfee-8ffa-4cb3-bed1-059565a57f06",
        "catalog_uuid": "214cf999-5964-4a2e-afa8-c62461558315",
        "subsidy_uuid": "214cf999-5964-4a2e-afa8-c62461342198",
        "access_method": "direct",
        "per_learner_enrollment_limit": 3,
        "per_learner_spend_limit": 0,
        "spend_limit": 0
    }

Outputs
-------
A sample response can be seen below.

::

    {
        "policy_type": "PerLearnerEnrollmentCreditAccessPolicy",
        "description": "updated description",
        "active": true,
        "group_uuid": "214cf999-5964-4a2e-afa8-c62461558211",
        "enterprise_customer_uuid": "12aacfee-8ffa-4cb3-bed1-059565a57f06",
        "catalog_uuid": "214cf999-5964-4a2e-afa8-c62461558315",
        "subsidy_uuid": "214cf999-5964-4a2e-afa8-c62461342198",
        "access_method": "direct",
        "per_learner_enrollment_limit": 3,
        "per_learner_spend_limit": 0,
        "spend_limit": 0
    }


Permissions
-----------

enterprise_admin
  Should only create the transaction if the requesting user has implicit (JWT) or explicit (DB-defined) ``enterprise_admin`` role assigned.
