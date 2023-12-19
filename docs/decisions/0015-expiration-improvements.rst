0015 Expiration and Cancellation Data Improvements
**************************************************

Status
======
Proposed - December 2023

Context
=======
There are subtleties around the Assignment cancellation and expiration user
experience in the enterprise ``frontend-app-learner-portal-enterprise`` frontend (a.k.a. the "learner-portal")
that require a more nuanced and descriptive approach to the data we persist around these states, and
how that data is represented in API response payloads for consumption
by the learner-portal frontend.

How expiration currently works
------------------------------
There's currently a management command (run via a cron) that toggles the state
of Assignments to ``CANCELLED`` under the following conditions:

1. The current date is more than 90 days after the *creation* date of the assignment. We use the creation
   date as a proxy for the date at which an assignment was allocated, which is problematic under
   the edge case where an assignment is allocated, cancelled, and re-allocated later.
2. The current date is greater than the inferred enrollment deadline for the assigned course.
3. The current date is greater than the expiration date of the subsidy associated with the Assignment's
   access policy record.

Learners are notified of expiration and cancellation via the learner-portal frontend
------------------------------------------------------------------------------------
The learner-portal notifies learners of assignment cancellation and expiration.  These notifications
are required to be *acknowledgeable* (a.k.a. "dismissable") - that is, the learner was chosen to acknowledge
and permanently dismiss the notification, whereby after ack'ing/dismissing the notification, the learner
will never again be notified of the cancellation/expiration. This requirements of the user experience
requires us to optionally persist state about acknowledgement/dismissal for any given assignment in the backend.

Decision
========

``EXPIRED`` should be a distinct Assignment State
-------------------------------------------------
This will help disambiguate behavior and simplify/remove business logic
from the learner-portal frontend.  We may wish to also store the expiration reason
for an assignment (i.e. which of the 3 cases above caused the assignment to become expired).

Persist explicit timestamps/datetimes about state transition
------------------------------------------------------------
There should be fields on the ``LearnerContentAssignment`` model that express when
an assignment record last transitioned to a certain state.  That is, for the valid
states of an assignment: ``allocated``, ``accepted``, ``errored``, ``cancelled``, and ``expired``,
there should be correspond datetime fields ``allocated_at``, ``expired_at``, and so on.
These fields should be populated and nullified on state transition as follows:

* ``allocated`` - When an assignment becomes allocated, we should set ``allocated_at`` to the current
  timestamp and nullify the ``errored_at``, ``cancelled_at``, and ``expired_at`` fields.
  Note this should occur *any time* an
  assignment becomes allocated (for example, if an assignment is allocated, cancelled, then re-allocated, we
  should update the ``allocated_at`` field to the latest allocation time).
* ``accepted`` - When an assignment is accepted, we should set ``accepted_at`` to the
  current timestamp. Additionally, we should nullify the ``errored_at``, ``cancelled_at``, and ``expired_at`` fields.
* ``errored`` - When an assignment becomes errored, we should set ``errored_at`` to the current timestamp.
  No other datetime fields should be affected in this state.
* ``cancelled`` - When an assignment becomes cancelled, we should set ``cancelled_at`` to the current timestamp.
  No other datetime fields should be affected in this state.
* ``expired`` - When an assignment becomes expired, we should set ``expired_at`` to the current timestamp.
  No other datetime fields should be affected in this state.

The presence of these field will help clarify the true state of an assignment for
consumption by the learner-portal frontend.

Cancellation/Expiration acknowledgement should be an action(s)
--------------------------------------------------------------
There should be new action types to acknowledge expiration and cancellation of assignments, respectively.
We'll provide endpoint(s) scoped to the assignment configuration
of ``LearnerContentAssignments`` to create such action records.  This will greatly simplify logic in the learner-portal frontend
about the state of expiration/cancellation notifications and whether they have been acknowledged/dismissed
by the learner.  It also makes the behavior of that frontend more in-line with the intended user experience.

The views to create these acknowledgement actions should be scoped to the assignment configuration level (i.e.
the parent ``AssignmentConfiguration`` model, which ties out to ``SubsidyAccessPolicies``, and of which each
``LearnerContentAssignment`` must have a reference to) and should accept one or more valid
``LearnerContentAssignment`` uuids in the request payload.  This will allow the learner-portal MFE to make
one request per distinct assignment configuration, acknowledging cancellation/expiration.

Serialize the expiration deadlines with assignments
---------------------------------------------------
Serialize the earliest possible expiration date of an assignment in the serialized
response of assignments inside the ``credits-available`` payload (perhaps also in the
assignments list view response payload in the future).
Imagine it as a key like ``earliest_possible_expiration`` that is the minimum of subsidy expiration date, enrollment deadline, and 90 day deadline.

Modify existing expiration command
----------------------------------
* Compare against the allocation date of assignments to determine 90 day expiration condition.
* Set the state to ``EXPIRED`` and set the expiration date on the expired record.
* All points of business logic that transition the state of an assignment record should
  make appropriate updates to the state timestamps, as described a few sections above.

Consequences
============
* The difference between admin-directed cancellation vs. automatic expiration becomes obvious
  from the state of the record.
* We store dates around the lifecycle transitions of assignment records.  This introduces a few more
  fields to manage, but is ultimately reasonable and helpful.
* We yank a bunch of business logic out of the frontend (which is good).
* Serialization becomes more complex and depends on cached content metadata and subsidy metadata records.
  We're making this tradeoff to help isolate business logic inside the backend API and model layers, which
  is reasonable.
* The expiration management command becomes more specific, which is good.


Alternatives Considered
=======================

Expiration acknowledgement as an Assignment field
-------------------------------------------------
Rejected because it doesn't align with the pattern we've already
introduced around assignment action records.

Use `localStorage` to keep track of acknowledgments
---------------------------------------------------
We're currently adopting a short-term fix to rely on `localStorage` in the learner-portal MFE
to indicate if cancellations or expirations have been acknowledged by the learner. We reject
this as a long-term approach because it relies on keeping some complex business logic in the frontend,
and because it allows learners (intentionally or unintentionally) to clear their acknowledgement/dismissal
history and see cancelled/expired assignments again.
