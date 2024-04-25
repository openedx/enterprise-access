0020 Redemption Attempt Record
******************************

Status
======

Proposed - April 2024

Context
=======
The primary responsibilities of the ``subsidy_access_policy`` module are as follows:

1. Act as a "read-orchestrator", determining which learner-credit-based redemptions
   are allowed under various conditions.
2. Act as the entry-point for the redemption workflow, via the ``redeem`` view
   and related ``SubsidyAccessPolicy.redeem()`` method. This responsibility depends on
   the read-orchestrator responsibility above - the redemption flow must determine if redemption
   is allowed before proceeding to request that the enterprise-subsidy service write
   any ``transaction`` records.

There is a general observability problem with the redemption workflow: outcomes that
result in errors or non-redemption can only be inspected in a meaningful way from
logs, or sometimes from the error details of the redemption HTTP response. This makes
it difficult to debug the redemption workflow and gather metrics for engineering and
non-engineering stakeholders alike.

Decision
========
We'll introduce a new Django model, called ``RedemptionAttempt`` (or similar), to persist
business logic outcomes and workflow results in the course of the redemption workflow. We should
think of it as an audit-log type of persisted data - the presence or absence of a ``RedemptionAttempt``
record should have no bearing on subsequent attempts of the redemption workflow.
It should record the following types of data:

1. The core inputs of the redeem workflow, including identifiers for the policy, learner, content,
   and optionally any related ``LearnerContentAssignment`` or ``SubsidyRequest`` records.
2. The state of the policy at the time of the redemption attempt - pointing at a historical
   ``SubsidyAccessPolicy`` record would be useful here. This historical record lets us
   understand the subsidy identifier, catalog identifier, groups, and policy limits at
   the time of the redemption attempt.
3. The value that ``can_redeem()`` returned at the start of the flow, and if that value was ``False``,
   the associated reason for disallowed redemption.
4. The success state of the response from the enterprise-subsidy ``redeem`` request, including
   identifiers for the transaction, fulfillment(s), and the price.
5. The optional error state of the enterprise-subsidy response, including any error messages and codes.

Consequences
============
This should be thought of only as an audit-log type of record, lest we become tempted
to update state based on data mutations that occur *outside* of the redemption workflow,
e.g. when unenrollments and reversals occur. This record type indicates only the state and
outcome of the redemption at the time the workflow was executed. Current state of redemptions
can still be ascertained by examining the state of related transaction, fulfillment, and/or enrollment records.


Alternatives Considered
=======================
We could continue to rely only on log messages and HTTP responses, and build more tooling
around those artifacts to help make the redemption workflow more observable.
However, that approach, in isolation, precludes us from surfacing redemption workflow state/outcome
into downstream consumers like our data warehouse, the Support Tools MFE,
or even the enterprise Learner Portal MFE - note that surfacing these records
via MFEs would require that the records are exposed via a REST API (at some point in the future).
