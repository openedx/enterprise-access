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

- ``group_id`` (Query Param, required): The uuid of the customer. For now it will be an enterprise customer uuid.
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

- ``group_id`` (POST data, required): The uuid of the customer. For now it will be an enterprise customer uuid.
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


CRUD operations on SubsidyAccessPolicy
======================================

GET **/api/v1/admin/policy/**

List all policies linked to a group.

Inputs
------

- ``group_uuid`` (Query Param, required): The uuid of the customer. For now it will be an enterprise customer uuid.

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
        "group_uuid": "214cf999-5964-4a2e-afa8-c62461558211",
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
