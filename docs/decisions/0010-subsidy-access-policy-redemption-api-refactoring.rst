0010 Refactoring of Subsidy Access Policy redemption API
********************************************************

Status
======

Accepted - May 2023

Supercedes `_0003 Initial API Specification`

Context
=======
There are some unneeded and misnamed routes related to policy redemption
which we want to fix.


Decision
========

- We'll rename the top-level path resource name from ``policy`` to ``policy-redemption``
  for any views that explicitly deal with the redemption of policies.  Any other actions
  belong under the CRUD API exposed at ``/api/v1/subsidy-access-policies``.
- ``/api/v1/policy/redemption`` is unneeded.  The ``can_redeem`` action provides a similar-enough
  interface for our use-cases.
- ``/api/v1/policy/`` (not to be confused with the CRUD list (and other)
  operations provided today via ``/api/v1/admin/policy/``) -
  this purports to return a list of all redeemable policies for a given
  ``enterprise_customer_uuid``, ``lms_user_id`` and ``content_key``
  The ``can-redeem`` endpoint supersedes this.  A general list action for policies
  will be provided in the CRUD API, see `_0009 Refactoring of the SubsidyAccessPolicy CRUD API`
- ``/api/v1/policy/credits_available/`` is unused by the learner portal today,
  but the use case it serves is likely relevant once we need to
  support non-LMS EMET use cases.

Consequences
============

.. _0009 Refactoring of the SubsidyAccessPolicy CRUD API: 0009-subsidy-access-policy-crud-api-refactoring.rst
