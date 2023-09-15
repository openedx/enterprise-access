0012 Assignment-based policies
*******************************

Status
======

Accepted - September 2023

Context
=======
Some enterprise customers want tighter control over the consumption of subsidy value
provided to their learners, particularly for higher-priced content.
To support this business and customer need, we’ll introduce the ability for
customer admins to assign content to specific learners via their Policies (or "Budgets").

These learners may or may not be already registered -
the creation of assignments will deal up-front in email addresses;
once a learner is registered, they’ll record the associated LMS user id.

Assignments will *not* directly result in a new redemption/enrollment taking place;
a learner must accept the assignment via the Learner Portal for an associated redemption to occur.


Decision
========
We'll introduce a new Django app to persist facts about
assignment of content to learners in the context of ``SubsidyAccessPolicies``.
This will require that the ``subsidy_access_policy`` is cohesive with the new app.
It also requires additions to the enterprise-access REST API, so that clients
may *allocate* new assignments, read or modify their state, and determine
correct aggregate facts about the amount of value currently *allocated* or *spent*
against an assignment-based ``SubsidyAccessPolicy``.


New ``content_assignments`` Django app
--------------------------------------
We built a new ``content_assignments`` Django application.  It persists data about
allocated assignments - these assignments, stored as ``LearnerContentAssignment`` model instances,
represent the fact that an admin assigned some course to a learner at a given time and price.
The ``subsidy_access_policy`` app’s business logic will integrate with this new app.
A model called ``content_assignments.AssignmentConfiguration`` is depended upon by the ``LearnerContentAssignment``
model and the base ``SubsidyAccessPolicy`` model via a foriegn key relationship.

The default, happy-state of a ``LearnerContentAssignment`` is ``allocated``.  We talk about
"allocation" as the act of creating a new assignment within a given policy (or "budget").
Allocations should be taken into account when determining current spend for an
assignment-based policy/budget (see section below).

This new Django app will introduce asynchronous celery tasks to:

- Link pending, allocated learners to an enterprise
- Notify learners of new allocated assignments, or to remind them of existing allocations.

New assignment-based policy type
--------------------------------
The ``SubsidyAccessPolicy`` model and business logic will now depend on the
state of assignment records. This aligns with the policy domain's role as an
"octopus enforcer" - it depends on all of the other domains of our software
that must be considered in computing queries and commands about access to content
covered by a policy.

We'll introduce a new type of assignment-based policy model, with two new, top-level methods
that allow for querying and commanding about allocation of new assignment records:

- ``can_allocate`` takes allocated ``LearnerContentAssignment`` records related to this policy
  into account to determine if some number of learners can have assignment records
  allocated in this policy for a given content key  and its current price.
- ``allocate`` is the command through which new assignment records should
  be allocated against a given policy.  It queries ``can_allocate`` before
  allocation occurs.

Before determining if new assignments can be allocated for a set of learners,
the assignment-based policy logic must now also take into account, via the sum of:

- The total cost of new assignments to be allocated.
- The total cost of all existing allocated (but not accepted, failed, or cancelled) assignments related to the policy.
- Must not exceed the remaining balance of the policy’s subsidy.
- Must not exceed the remaining spend limit configured for the policy.

Furthermore, we'll have to modify *redemption* logic for assignment-based policies as follows:

- ``redeem`` The act of *redemption* against an assignment-based ``SubsidyAccessPolicy`` transitions
  the state of the related *assignment* record.  ``LearnerContentAssignment``
  records persist the associated transaction identifier upon successful redemption.
- ``can_redeem`` should take allocated assignments associated with the policy into account.
  This shouldn't strictly-speaking be *necessary*, but is an important guard against
  circumstances where a redemption attempt *does* fit under the policy and subsidy balance/spend-limits,
  but *would cause* the sum of existing allocated assignment for the policy, along with
  redeemed spend, to *exceed* the policy or subsidy balance/spend-limit.
- ``credits_available`` This endpoint returns a list of current, active policies
  with credit availble to be redeemed **immediately**.  Therefore, we'll have to conditionally
  include assignment-based policies in this endpoint's response payload - if the requesting
  learner has an allocated assignment record associated with a current, active policy, that
  policy record should be included in the response payload.

For the purposes of displaying budget balance in the admin portal,
the total cost of currently allocated assignments must now be taken into account,
alongside the initial and current balance of the policy/budget.
Furthermore, if an assignment is canceled, the pending amount of credit on the
assignment should no longer be included in the aggregated allocated balance for the policy/budget.

REST API additions
------------------
New ``can_allocate`` and ``allocate`` actions will be added to the ``SubsidyAccessPolicy`` REST API.
These map 1-1 to the query and command described directly above.

Reads of ``AssignmentConfiguration`` and ``LearnerContentAssignment`` records happen via
a new assignments REST API.  This gives us the ability to be very flexible
in our user-experience design for learner and admin use cases that do *not* directly
require knowledge of aggregate budget and spend for a given policy.
Similarly, ``AssignmentConfiguration`` creation and modification
will occur via this new assignments REST API, and provide that same flexibility (for example,
in the realm of provisioning new records to which policies eventually depend on and integrate with).

Content metadata replication
----------------------------
We need to support query-ability of assignment records by course *title*.  We'll start
replicating content metadata into Django models within the ``enterprise-access`` service.
This replication will initially happen ad-hoc/on read, and there will be some
index on *recency* to determine if replicated data should be updated from our upstream
systems of records.  This design will provide us with the flexibility to eventually move
from a "pull" model (replicate-on-read) to an event-based "pull" model (update-on-upstream-event).

Rejected alternative: requiring that clients of our APIs provide this title,
which we'd persist on the ``LearnerContentAssignment`` model.  This becomes
distasteful from a separation-of-concerns perspective - the domain of *assignments*
should not be concerned with anything except the primary identifier of records
from the *content* domain.

Rejected Alternatives
=====================
Assignments modeled as *pending* transactions.  This introduces too much complexity
into the *ledger/transaction* domain.

Consequences
============
Assignments are *not* a type of ledgered-transaction.  This implies that some
learner-initiated event or command must occur before a transaction record, related
to some assignment, can be created.
