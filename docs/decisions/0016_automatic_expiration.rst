0016 Automatic Cancellation (Expiration)
****************************************

Status
======
Accepted - January 2024

Context
=======

How expiration currently works
------------------------------
There's currently a management command (run via a cron) named ``automatically_exire_assignments.py``
that toggles the state of ``LearnerContentAssignment`` records ("Assignments")
to ``CANCELLED`` under any of the following conditions, as long as the current state
of the assignment is ``ALLOCATED``:

1. The current date is more than 90 days after the most recent notification action - this action
   time is used as a proxy for understanding *when* the assignment *was last allocated*. This helps
   deal with the edge case where an assignment is allocated, cancelled, and re-allocated later. Note
   that a reminder action on an assignment record *does not* reset this specific expiry time.
2. The current date is greater than the inferred enrollment deadline for the assigned course.
3. The current date is greater than the expiration date of the subsidy associated with the Assignment's
   access policy record.

Decision
========
The above management command will now remove Personally-Identifiable Information ("PII") from assignments
that are automatically moved from ``ALLOCATED`` to ``CANCELLED`` under condition (1) above - that is, only
for such assignments whose last notification time was more than 90 days ago.
This PII includes the learner email address.

Scrubbing the learner email
---------------------------
Note that, to remove learner email PII, we change the value to a "tombstone" - ``retired_user@retired.invalid``.
This is done so that the ``learner_email`` database column can continue to have a non-null constraint.

Consequences
============
* Assignments that were cancelled by the admin or that fell into an ``ERRORED`` state will not
  currently have PII cleared by this cron-based management command.
* The Assignment data schema does not yet fully support the improvements proposed in
  `<0015-expiration-improvements.rst>`_. The implementation of these improvements will
  allow us to also take a more nuanced approach about automatically expiring assignment records
  in non-allocated states, or to retire PII fields in other ways.

Alternatives Considered
=======================

Hook into the edX User Retirement Pipeline
------------------------------------------
We have not yet rejected, but not yet committed to, integrating ``LearnerContentAssignment``
record retirement with the edX User Retirement Pipeline. This would involve exposing
some API view to scrub PII from certain assignment records associated with a
registered edX user who has requested that their account be retired. This is mostly relevant
in the case of ``ACCEPTED`` assignments, or assignments that have fallen into an ``ERRORED``
state prior to being automatically expired.
