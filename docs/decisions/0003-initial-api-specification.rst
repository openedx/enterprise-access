0003 Initial API Specification
##############################

Status
******

**Provisional**

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

POST access policy redeem transaction
======================================
**/api/v1/policy/redeem/**

Creats a new transaction to redeem an entitlement by making a request to ``POST /api/v1/subsidies/.../transactions/``
A side-effect of a successful POST request here is the creation of a course enrollment or entitlement
that "fulfills" the ledger transaction.

Inputs
------

- ``customer-id`` (POST data, required): The uuid of the customer. For now it will be an enterprise customer uuid.
- ``learner_id`` (POST data, required): The user for whom the transaction is written and for which a enrollment should occur.
- ``content_key`` (POST data, required): The content for which a enrollment is created.

Outputs
-------
Returns data about the transaction.

::

   {
       'uuid': 'the-transaction-uuid',
       'status': 'completed',
       'idempotency_key': 'the-idempotency-key',
       'learner_id': 54321,
       'content_key': 'demox_1234+2T2023',
       'quantity': 19900,
       'unit': 'USD_CENTS',
       'reference_id': 1234,
       'reference_table': 'enrollments',
       'subsidy_access_policy_uuid': 'a-policy-uuid',
       'metadata': {...},
       'created': 'created-datetime',
       'modified': 'modified-datetime',
       'reversals': []
   }

Permissions
-----------

enterprise_learner
  Should only create the transaction if the requesting user has implicit (JWT) or explicit (DB-defined)
  ``enterprise_learner`` role assigned.


Consequences
************

- Django Admin site will be used to create and configure the access policy records.
