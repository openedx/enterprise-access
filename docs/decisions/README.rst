Architectural Decision Records
##############################
Below, you can find links to all of the Architectural Decision Records (ADRs) that pertain
to functionality of the enterprise-access service, along with a brief description of each.

.. contents:: :local:


`<0001-purpose-of-this-repo.rst>`_
**********************************
*Feature: Browse and Request*

Accepted in January 2022, this ADR declares:

  This service will be the source-of-truth about requests of enterprise subsidies and approvals or denials thereof.

*Note* this ADR was written before the concepts of ``SubsidyAccessPolicies`` or "new" Learner Credit existed.

`<0002-auto-enrollment-post-request-approval.rst>`_
***************************************************
*Feature: Browse and Request*

Rejected in April 2022, this ADR declares:

  We decided to abandon this feature [auto-enrollment after request approval] for the following reasons:

  * We don't want to overwhelm users with enrollments when they get approved for multiple requests at the same time.
  * We don't know which course run to enroll learners into and don't want this feature to cause more support cases to be opened.
  * This feature does not provide our customers enough value for us to deal with the issues above.

`<0003-initial-api-specification.rst>`_
***************************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted in March 2023, this ADR describes the initial API specification for the ``subsidy_access_policy`` REST API.

`<0004-add-access-policy-functionality.rst>`_
*********************************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted in February 2023, this ADR declares:

  We've introduced a new Django service, ``enterprise-subsidy``, to provide a new implementation of Learner Credit,
  in which learners in an enterprise can redeem the balance of their enterprise's Learner Credit ledger to pay
  for verified enrollments in any kind of content supported via Enterprise Catalogs.
  
  [...]

  In writing an access policy application, we'll be able to command and query who is allowed to redeem
  subsidy value, from which allowed set of content, via what access method.  It should also support our
  general compliance requirements, protecting our business reputation and general business account veractiy.

  [...]

  The base access policy model should be composed of references to an enterprise subsidy, an enterprise catalog,
  an access method (e.g. direct learner enrollment, or Browse & Request), and optionally, the total value allowed
  to be redeemed via the policy (that is, the maximum number of dollars or seats allowed to be consumed).

`<0005-access-policy-locks.rst>`_
*********************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted in March 2023, this ADR declares:

  We need to treat redemptions of learner credit value via an access policy as a `shared resource`,
  since they support limits on how much value can be spent, either "per-learner" (i.e. no learner covered by
  a policy may spend more than some dollar or enrollment limit), or "per-policy" (i.e. no more than
  some dollar or enrollment limit may be consumed in aggregate, across all learners, via the policy).

`<0006-api-specification-for-enterprise-mfes.rst>`_
***************************************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted in March 2023, this ADR describes the first major revision to the Subsidy Access Policy ``can_redeem`` view:

  This API endpoint will be called by the enterprise learner portal to understand whether
  the learner is already enrolled in any of the available course runs (i.e., a prior redemption has been successfully
  fulfilled) and/or which subsidy access policy should be used to redeem each course run when a learner
  clicks the "Enroll" button. 

`<0007-access-policy-locks-revised.rst>`_
*****************************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted in April 2023, this ADR revises the locking implementation described in `<0005-access-policy-locks.rst>`_:

  [The prior ADR] was implemented, but leveraged ``TieredCache``, which uses
  ``get()`` and ``set()`` functions from Memcached to set locks, but ``add()`` is a better choice according to Memcached
  authors.

`<0008-additional-redemption-metadata.rst>`_
********************************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted in May 2023, this ADR describes:

  [...] modifications to existing API endpoint(s) such that MFEs can pass
  additional metadata into the redemption flow. This extension was devised to support required
  GetSmarter Enterprise Api Gateway (GEAG) meatadata such as DOB and terms acceptance dates.

`<0009-subsidy-access-policy-crud-api-refactoring.rst>`_
********************************************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted in May 2023, this ADR supercedes `<0003 Initial API Specification>`_:

  Our subsidy access policy API needs some degree of refactoring.  Specifically, we want to:

  - Limit the the breadth of the API so that there's "one obviously right way" to complete an action.
  - Make the required permissions required for each action very obvious.
  - Make sure to decorate our views and viewsets consistently and adequately such that suitable API docs are maintained.
  - Isolate concerns of query parameter filtering to a ``FilterSet`` implementation.
  - Isolate concerns of request and response serialization into purpose-built ``Serializers``.

`<0010-subsidy-access-policy-redemption-api-refactoring.rst>`_
**************************************************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted in May 2023, this ADR partially supercedes `<0003 Initial API Specification>`_:

  There are some unneeded and misnamed routes related to policy redemption which we want to fix.

`<0011-subsidy-access-policy-crud-api-definition.rst>`_
**************************************************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted in August 2023, this ADR defines and describes the refactored access policy CRUD API.

`<0012-assignment-based-policies.rst>`_
*******************************************
*Feature: Assigned Learner Credit*

Accepted September 2023, this ADR defines:

- The addition of the ``content_assignments`` djangoapp, which persists
  data representing the assignment of content to specific learners within an enterprise.
- The introduction of an assignment-based ``SubsidyAccessPolicy``, which
  depends on the models and business-logic of the ``content_assignments`` app.
- The general structure and strategy of the REST API views that interface with
  the first two points.

`<0013-assignment-actions-model.rst>`_
*******************************************
*Feature: Assigned Learner Credit*

Accepted October 2023, this ADR describes an approach to persisting data about
certain actions related to a ``LearnerContentAssignment`` record in a distinct model.

`<0014-assignment-price-validation.rst>`_
*******************************************
*Feature: Assigned Learner Credit*

Accepted November 2023, this ADR describes an approach to validating
client-provided allocation prices.

`<0015-expiration-improvements.rst>`_
*******************************************
*Feature: Assigned Learner Credit*

Accepted December 2023, this ADR proposes an improved approach
around assignment lifecycle and business logic related to expiration (and
to a lesser degree, cancellation).

`<0016-automatic-expiration>`_
********************************
*Feature: Assigned Learner Credit*

Accepted January 2024, this ADR describes an approach to automatically
expire assignments based on the earliest of three possible dates (i.e.,
course enrollment deadline, subsidy expiration date, 90 days after
allocation).

`<0017-policy-retirement.rst>`_
********************************
*Feature: Subsidy Access Policy (Learner Credit)*

Accepted January 2024, this ADR describes an approach to retiring
policies. Retired policies are no longer usable by learners for redemption,
but are still visible to enterprise administrators for historical reporting
purposes.

`<0018-access-policy-grouping.rst>`_
********************************
*Feature: Subsidy Access Policy and Groups (Learner Credit)*

Accepted February 2024, this ADR describes an approach to associating
learner-group membership within an Enterprise Customer with
Subsidy Access Policy records.

`<0019-forced-redemption.rst>`_
********************************
*Feature: Subsidy Access Policy Redemption*

Accepted April 2024, this ADR describes a new ``ForcedPolicyRedemption``
model and Django admin view for forcing redemption via a learner
credit Subsidy Access Policy.

`<0020-redemption-attempt-record.rst>`_
********************************
*Feature: Subsidy Access Policy Redemption*

Proposed April 2024, this ADR describes a new ``RedemptionAttempt``
model for audit-log type records regarding redemption for learner credit 
Subsidy Access Policy.

`<0021-transaction-aggregates.rst>`_
********************************
*Feature: Subsidy Access Policy Aggregate*

Proposed April 2024, this ADR describes a new ``aggregate`` redemption
model that contains count/spend metrics at the subsidy level, policy level,
and policy+learner level for learner credit Subsidy Access Policy.

`<0022-deposit-creation-ux.rst>`_
********************************
*Feature: Subsidy Access Policy Deposits*

Accepted July 2024, this ADR describes a new Django admin action for the
``SubsidyAccessPolicy`` edit page called "Deposit Funds". It automates much
of the workflow around adding additional funds to a subsidy directly from the 
related policy.

`<0023-spend-limits-constraint.rst>`_
********************************
*Feature: Subsidy Access Policy Spend-limit*

Accepted June 2024, this ADR describes a new constraint on the
``SubsidyAccessPolicy`` model's ``spend_limit`` field on the model's
``clean()`` function. It prevents admins from increasing the policy's
``spend_limit`` above the subsidy's ``total_deposits``.

`<0024-provisioning-api.rst>`_
******************************
*Feature: Self-service Provisioning*

Proposed February 2025, this ADR describes a singular endpoint that will
make downstream calls across multiple services to provision net-new core
enterprise business records.

`<0025-abstract-workflow-pattern.rst>`_
***************************************
*Feature: Self-service Provisioning*

Proposed March 2025, this ADR describes an abstract workflow pattern
that will be wrapped around our provisioning implementation.
