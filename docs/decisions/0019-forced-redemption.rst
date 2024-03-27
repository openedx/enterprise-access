0019 Forced Redemption
**********************

Status
======

Accepted - April 2024

Context
=======
There is frequently a need to force through a redemption (and related enrollment/fulfillment)
of a particular learner, covered by a particular subsidy access policy, into
some specific course run. This need exists for reasons related to upstream
business constraints, notably in cases where a course is included in a policy's catalog,
but the desired course *run* is not discoverable due to the current state of its metadata
(e.g. the ``is_enrollable`` value for that run's record is false).

Some recent work has provided an ``allow_late_enrollment`` directive to be included
in the ``metadata`` of the subsidy redeem payload.  This solution will utilize that directive,
because it causes enrollment and/or external fulfillment to be forced through in
downstream systems.

Decision
========
* We'll expose a model, ``ForcedPolicyRedemption``, via Django Admin that allows
  staff users to force redemption for a given learner, into a particular course,
  under a particular subsidy access policy.
* The model should describe which learner is being enrolled, via which policy, and into
  which specific course **run**.  It should also record which staff user wrote the record
  when it was created/modified, and when the redemption succeeded or failed.
  Furthermore, it should store a reference to the created
  transaction on success, and store a reference to any error information on failure.
* It should be possible to write a record for this model *without* the forced redemption immediately
  taking place, although perhaps the default behavior should cause the redemption to
  occur on save.
* All the standard redeemability logic will still hold, that is, ``can_redeem()`` must return ``True``
  for the redemption to take place.  This entails the automated creation of a ``LearnerContentAssignment``
  record for assignment-based policies.

Consequences
============
This is a somewhat powerful tool, even if only available to staff. It should be made
clear to use it with caution.

This tool will **not** suffice to force redemption for Exec Ed content, or any other
content type for which the enterprise-subsidy service requires additional context
in the ``metadata`` element of the redeem payload.

Alternatives Considered
=======================
There is ongoing work to allow late policy-redemption under some circumstances
in a way that's available to the learner-facing UX.  This proposal builds on that
(particularly the ``allow_late_enrollment`` directive), but is useful in scenarios
where the desired content is not discoverable to the learner (and therefore not enrollable).
