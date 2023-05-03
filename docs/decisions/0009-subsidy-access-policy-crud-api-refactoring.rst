0009 Refactoring of the SubsidyAccessPolicy CRUD API
****************************************************

Status
======

Accepted - May 2023

Supercedes `_0003 Initial API Specification`

Context
=======
Our subsidy access policy API needs some degree of refactoring.  Specifically, we want to:

- Limit the the breadth of the API so that there's "one obviously right way" to complete an action.
- Make the required permissions required for each action very obvious.
- Make sure to decorate our views and viewsets consistently and adequately such that suitable API docs are maintained.
- Isolate concerns of query parameter filtering to a ``FilterSet`` implementation.
- Isolate concerns of request and response serialization into purpose-built ``Serializers``.

Decision
========
We'll update routes and permissions as for policy CRUD operations as described below.

Read Actions
------------
Retrieve and list via **/api/v1/subsidy-access-policies/**

- Any user with a role that's applicable for some valid enterprise customer uuid
  will be allowed to retrieve or list policies belonging to that customer.  So admin,
  learners, and operators alike each have read permission for these records (as long
  as the admins and learners are members of the related customer).
- For the list action, the request is required to be filtered by an
  ``enterprise_customer_uuid`` query param, and optionally by ``policy_type``.

Write Actions
-------------
Create, update, and delete via **/api/v1/subsidy-access-policies/**

- Only operators can create, update, or delete policies.
- We'll add a new permission like ``PERMISSION_WRITE_POLICY`` that's granted
  to users with the operator role.
- The delete action should be a soft-delete: it should toggle the ``active`` field
  on the policy to ``False``.

Consequences
============
- Since there's no complex permissioning or list-based filtering involved,
  these actions can be defined in the same ViewSet that defines the retrieve and list actions.
- We can use the ``permission_required`` decorator on the method that implements each
  action to control permission checks.
- The existing ``SubsidyAccessPolicyCRUDViewset`` is now deprecated and due for removal.
  The corresponding ``/api/v1/admin/policy`` route will also be removed.


.. _0003 Initial API Specification: 0003-initial-api-specification.rst
