2. Automatically Enrolling Learner After a Subsidy Request Approval
===================================================================


Status
======

Rejected April 2022
-------------------

We decided to abandon this feature for the following reasons:

* We don't want to overwhelm users with enrollments when they get approved for multiple requests at the same time.
* We don't know which course run to enroll learners into and don't want this feature to cause more support cases to be opened.
* This feature does not provide our customers enough value for us to deal with the issues above.


Context
=======

Learners are currently able to request a subsidy (license or coupon code) through the enterprise-access service.
After a request is approved, the learner has to redeem the subsidy and enroll in a course manually.
Since the goal of a learner is to access a course, we want to automatically enroll them after a request approval.

For coupon code requests, requiring manual redemption is not ideal because we cannot enforce that a code is only used
for the course requested.
Admins have some visibility into the usage of a coupon code currently. The admin portal shows if a coupon code has been redeemed,
but not the course it's was redeemed for. We have to query ecommerce data to check the order created using the coupon code
for that information. Automatically enrolling a learner after a request approval would guarantee that a code is used to enroll in
the intended course.

For license requests, it's less of a problem because a license can be used for all courses in an enterprise catalog.
Auto-enrollment would just save learners clicks for this case.

Decision
============

We will be adopting event-based communication between the enterprise-access service, the LMS (possibly), and the services
responsible for granting/redeeming a subsidy.

The following events will be sent to an event bus when the state of a subsidy request changes:

* `org.openedx.enterprise.access.coupon-code-request.approved.v1`
* `org.openedx.enterprise.access.license-request.approved.v1`

These events will be produced via Django signals that live in the `openedx-events <https://github.com/openedx/openedx-events>`__ repo.

Auto-enrollment will be a feature that could be toggled off since it does not always make sense to auto-enroll a learner,
e.g. when a coupon code offers 50% discount, a license request without course id, etc.. We will add a field `attempt_auto_enrollment`
to the `SubsidyRequestCustomerConfiguration` model to allow for the auto-enrollment to be turned on/off.
We could also explore having the auto-enrollment be a request specific rather than enterprise specific toggle.

The request approval process will involve these steps:

1. An admin approves a subsidy request.
2. The state on the subsidy request changes to `approved`.
3. An subsidy request approved event is sent to the event bus.
4. license-manager or ecommerce consumes this event, assigns a subsidy, and sends a subsidy assigned event.
   It then enrolls the learner (either by calling the LMS or sending an event to LMS)
5. enterprise-access consumes the subsidy assigned event and changes the state on the subsidy request to `fulfilled`.
   It then sends an request approved email to the learner of the request. The work of enterprise-access is done at this point.

Changes required
----------------
* We will introduce a new subsidy request state `fulfilled` that signifies a subsidy has been granted.
* enterprise-access will no longer call the services directly to grant a subsidy.
* We will modify the approval email to be a generic email saying that the learner has been approved for the course.
  The generic email will contain a link to the course run that the learner requested, without mentioning enrollment.
  When the learner clicks the link, they will be lead to the course about page where they can view their course.
  It's important for this email to be generic because it allows enterprise-access to be decoupled from the enrollment process
  and continue to focus on subsidy requests. It doesn't care if a learner was enrolled or not, just that a subsidy was granted successfuly.
* We will no longer send an email to the learner at the time of approval.
* enterprise-access will consume subsidy-assigned events from license-manager and ecommerce.
* These events might look like::

    {
        'event_type': 'org.openedx.ecommerce.coupon-code.assigned.v1',
        'code': 'ABC123',
        'coupon': 3,
        'course_id': 'edX+DemoX',
    },
    {
        'event_type': 'org.openedx.license-manager.license.assigned.v1',
        'subscription_uuid': 'subscription-uuid',
        'license': 'license-uuid',
        'course_id': 'edX+DemoX',
    }

* enterprise-access will send the now generic approval email to learners upon consumption of these events and update subsidy request states

Consequences
------------
* A learner could get to the learner portal before being auto enrolled, but it's an edge case and they can still enroll manually.
* Enrollment could fail for a learner, but they wouldn't know and can still enroll manually.
* We don't have to consume enrollment events from the LMS, removing the need to consume events from the LMS and reducing complexity.
* enterprise-access stays within its bounded context


Alternatives considered
=======================

Alternative Solution 1
----------------------
Slightly tweaking the solution above, ecommerce and license-manager would send the subsidy assigned event `after` enrolling a learner.
That would allow enterprise-access to send variants of the approval email and also guarantee that a learner is enrolled by the time the email is sent.

The subsidy assigned event might look like::

  {
      'event_type': 'org.openedx.ecommerce.coupon-code.assigned.v1',
      'code': 'ABC123',
      'coupon': 3,
      'is_redeemed': True,
      'idenfier_to_tie_to_request': 'uuid'
  }

Consequences
------------
* We would want to send a subsidy assigned event even if enrollment fails.

Alternative Solution 2
----------------------

enterprise-access still calls ecommerce and license-manager to assign a subsidy, then sends a request approved event
to the event bus. The subsidy granting services will consume the events, redeem the granted subsidy,
and enroll a learner in the course specified in the request.

Changes required
----------------
* We will no longer send an email to the learner at the time of approval. Instead, we will send an email to
  the learner when they have been enrolled.
* enterprise-access will consume enrollment events from the LMS, check if the enrollment corresponds to an approved
  request by matching on `lms_user_id`` and `course_id`, and send an approval email to the learner if a matching
  request is found.
* We might not need the above changes if we make the messaging in the approval email generic enough,
  i.e. "You've been approved, check out the course here!" Learners won't know if we tried to enroll them.
* We could add a new state `fulfilled` on the subsidy request models, but unless we need to store that information,
  I'd propose not changing any of the current models.

Consequences
------------
* The learner could see a granted subsidy and enroll manually in course before auto-enrollment occurs.
* Auto-enrollment could fail and enterprise-access won't receive an enrollment event. The learner will still have their
  subsidy and can enroll manually but they might not receive an request approved email. This won't be a problem if we take the
  generic email approach.
* Matching an enrollment to a subsidy request based on lms_user_id and course_id is not guaranteed to be reliable.
  We might want an identifier on the events produced/consumed to tie them to a single interaction.
* Subsidy request and approval process stays the same as before. As far as enterprise-access is concerned,
  a request is approved when a subsidy is granted. It does not care about enrollments.

Considerations
==============
* Does enrolling a learner after a request approval provide a good user experience?
  The current flow for request approval gives learners the flexibility to choose when to enroll in a course.
  A user might request access to multiple courses with the intention of taking them one at a time or down the line.
  Automatically enrolling them in multiple courses might lead to learners to bookmarking courses or maybe even unenrolling.
* How valuable is the guarantee that a code is used to enroll a learner in the intended course?
  A user can use a code intended for one course to enroll in another, but that is an edge case since the email we send to a learner
  after a request approval links them to the course they requested access to.
* Which course run do we enroll users in if there are multiple? Do we allow learners to choose or do we enroll them in the first available course run?
* How does this impact analytics/downstream reporting?