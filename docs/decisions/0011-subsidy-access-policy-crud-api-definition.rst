0011 Subsidy Access Policy CRUD API Definition
**********************************************

Status
======

Accepted - August 2023

Depends on `_0009 Subsidy Access Policy CRUD API Refactoring`

Context
=======
Our Subsidy Access Policy CRUD API has been refactored.  The API docs for this
service define the contract for it: http://localhost:18270/api/schema/redoc/#tag/Subsidy-Access-Policies-CRUD

This ADR describes, very generally, the resulting contract of this CRUD API, located at the
route ``/api/v1/subsidy-access-policies/``.

Decision
========
The Subsidy Access Policy CRUD API supports actions and use cases as described below.

Policy Creation
---------------
http://localhost:18270/api/schema/redoc/#tag/Subsidy-Access-Policies-CRUD/operation/api_v1_subsidy_access_policies_create

A ``POST`` request to ``/api/v1/subsidy-access-policies/`` will create, idempotently, a policy record - if an equivalent
record already exists, that record will be returned and no new policy is created.  Use this
endpoint to create a new policy record, preferrably after the related subsidy and catalog
records already exist.

Policy Retrieval
----------------
http://localhost:18270/api/schema/redoc/#tag/Subsidy-Access-Policies-CRUD/operation/api_v1_subsidy_access_policies_retrieve

A ``GET`` request to ``/api/v1/subsidy-access-policies/{policy_uuid}``
will fetch a single policy record by the ``uuid`` field.
Useful for examining the detailed fields and values for a single policy record.


Policy Listing/Filtering
------------------------
http://localhost:18270/api/schema/redoc/#tag/Subsidy-Access-Policies-CRUD/operation/api_v1_subsidy_access_policies_list

A ``GET`` request to ``/api/v1/subsidy-access-policies/`` will return a filtered list of policy records.
Useful if the caller wants to find all policies related to a given enterprise customer UUID.


Policy Modification
-------------------
http://localhost:18270/api/schema/redoc/#tag/Subsidy-Access-Policies-CRUD/operation/api_v1_subsidy_access_policies_update
http://localhost:18270/api/schema/redoc/#tag/Subsidy-Access-Policies-CRUD/operation/api_v1_subsidy_access_policies_partial_update

Both a ``PUT`` and a ``PATCH`` to ``/api/v1/subsidy-access-policies/{policy_uuid}`` will do a partial update
of a single policy record.  Useful for any client that needs to update an existing policy record.

Policy De-activation
--------------------
http://localhost:18270/api/schema/redoc/#tag/Subsidy-Access-Policies-CRUD/operation/api_v1_subsidy_access_policies_destroy

A ``DELETE`` request to ``/api/v1/subsidy-access-policies/{subsidy_uuid}`` will soft-delete
the record identified by ``{policy_uuid}`` by toggling its ``active`` field to ``false``.
Use this endpoint when a policy needs to be de-activated.

Consequences
============
None, but please remember that the API docs pages served by the enterprise-access service
are the authoritative documentation of the API contract for Subsidy Access Policies; the
document you are reading is not authoritative.

.. _0009 Subsidy Access Policy CRUD API Refactoring: 0009-subsidy-access-policy-crud-api-refactoring.rst
