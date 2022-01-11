1. Purpose of this Repo
***********************

Status
======

Accepted - January 2022

Context
=======

Learners who can browse the Enterprise Learner Portal without a subsidy should be able to
request a subsidy (either a license or coupon code) from their enterprise administrator.
In the Enterprise Admin Portal, an enterprise admin should be able to see which learners
have requested a license or coupon code, and then either assign those learners a license,
or deny the request.

Note that the source of truth about subsidy state is stored in other services - license-manager
is the source of truth for licenses, and ecommerce is the source of truth about coupon codes.
The enterprise-access service is intended to be the source of truth about requests and
approvals/denials of those requests for one of the above subsidy types (and perhaps
other sub-domains of accessing enterprise properties in the future).  The subsidy source-of-truth
services are still responsible for subsidy state and performing actions around
assigning or revoking enterprise subsidies.

Decision
========

This service will be the source-of-truth about requests of enterprise subsidies and approvals
or denials thereof.  

Subsidy Request learner experience
----------------------------------
An enterprise learner must have access to the learner portal
with a customer that has at least one current, active Subscription Plan or Enterprise Coupon.
Somewhere in the user interface, it is made clear to the learner
that they may request enrollment in a specific course from their learning administrator.
Upon approval, the enterprise-access system will call one of the subsidy assignment endpoints.
The enterprise-access system is *not* responsible for creating an enterprise enrollment.  Rather,
upon successful subsidy assignment, the subsidy system notifies the learner of the assigned subsidy,
with which the user may enroll in a course contained in their enterprise's catalog.

The learner must be linked to the enterprise customer,
that is, an ``EnterpriseCustomerUser`` record must exist for this (user, customer)
association.

We'll display a request button in the UI even if there are no unassigned
subsidies available for this customer. In the future, displaying such unfulfillable requests
to the customer admin could act as a "nudge" to the admin to procure more subsidies.

Subsidy request data
--------------------
We'll store the following data related to the subsidy request:

* A UUID to uniquely identify the request record.
* The learner making the request, in the form of their LMS user id (though perhaps their email address is also needed).
* The associated enterprise customer UUID.
* The course run identifier for which the learner is requesting enrollment.
* When the request was made.
* Whether the request was approved or denied.
* When the request was approved or denied.
* The assigned subsidy UUID which fulfilled the request.
* The identifier of the admin who approved or denied the request.
* A history table

A suggested data model design is presented in https://github.com/openedx/license-manager/pull/374/
Notable aspects of this suggested design:

* Requests for different types of subsidies live in different models, e.g.
  ``LicenseRequest`` and ``CouponCodeRequest``.
* Request approval and denial data lives in these same models, e.g.
  ``LicenseRequest.objects.filter(status='approved')`` would return
  a Queryset of all approved requests.

Furthermore, we suggest creating a configuration model for each customer that uses the subsidy request feature.
It should store at least the following:

* An indication of whether the feature is enabled for the customer
  (customer admins could perhaps disable on their own).
* The subsidy type that will be applied when requests are approved.
* The cadence at which enterprise admins are notified of pending requests.

Subsidy Request admin experience
--------------------------------
There will be a new user interface in the admin portal displaying a data table of subsidy requests.
From this data table, an admin is able to either approve (assign subsidies) requests, or deny them.
Either action may be made in bulk.

Subsidy approval flow and data
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
When an admin approves a given subsidy request (say it's for a license), we should store a record
of approval in the enterprise-access system, with the following data:

* Who approved it.
* When it was approved.
* The subsidy "container" identifier from which a subsidy should be assigned.
  That is, a ``SubscriptionPlan`` uuid in the case of licenses and a ``Coupon`` id in the case of codes.

After that data is stored, an asynchronous celery task should be invoked to perform the subsidy assignment.
This task will make API calls to one of the subsidy source-of-truth systems (license-manager or ecommerce).
When that API call returns, we'll add additional data indicating:

* Whether/when the subsidy assignment succeeded.
* The license or coupon code identifier that was successfully assigned.


If the customer has only one Subscription Plan or Enterprise Coupon
"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
This case is almost functionally equivalent to the current experience of assigning licenses or codes
to one or more learners by email address. We'll allow for bulk assignment as long as the
Subscription Plan or Coupon has enough unassigned licenses/codes to assign to the number of
selected requesting users.

If the customer has multiple Plans or Coupons
"""""""""""""""""""""""""""""""""""""""""""""
For this case, the administrator must indicate from which plan or coupon they'd like
to allocate unassigned licenses to the requesting users.
We will only allow licenses from a single plan to be assigned to a set of selected requesting users;
an admin may not assign licenses from multiple plans amongst multiple requesting learners
in a single bulk action request.

Denying a subsidy request
^^^^^^^^^^^^^^^^^^^^^^^^^
In this case, no license or code will be assigned to the requesting learner.
The Request record should be updated to reflect the denial; the suggested data modeling above
would have us create a discrete ``Denial`` record.

New subsidy request API endpoints
---------------------------------
We'll create a new Viewset to deal with License and Coupon Code Request CRUD operations.

* GET (retrieve) A learner should be able to see their own Request records.
* GET (list) An admin should be able to list all Request records associated with their enterprise.
* POST (create) A learner should be able to create a new request record.
* POST (approval) An admin should be able to update a Request record as approved.
* POST (denial) An admin should be able to update a Request record as denied.
* DELETE A learner should be able to delete their own request record.

Consequences
============

* We may need to allow for the license assignment endpoint to receive an optional course id
  in it's payload, so that a license approval email also contains a link to the course page,
  from which the learner selects the course run in which to enroll.

Rejected Alternatives
=====================
We considered several alternatives for where to store the source-of-truth on subsidy requests/approvals
before deciding to create this service.  Why choose to create a new Django-based service?

* The feature is not huge, but perhaps "just big enough" to justify it's own service.
* There are other potential, future features that fall under the domain of enterprise-access.
* It helps us "pave the road" for a more fully microservices-based (and event-driven architecture) ecosystem.

The alternatives we rejected are listed below.

edx-enterprise/LMS
------------------
Benefits:

* Puts data around requests in the same DB as other enterprise data.
* Doesn’t mix contexts between services (i.e. codes requests living in license-manager)

Drawbacks:

* Would require calls from LMS into license-manager to fulfill license requests (communication is currently one-way from license-manager to LMS)
* Now we need API calls to do anything with each type of subsidy.
* Deploys are slow.

ecommerce
---------
Data models and API pertaining only to coupon code requests would live here.

Benefits:

* This service is already the source of truth for coupons; we wouldn’t have to mix context into a different service.

Drawbacks:

* Future of service is somewhat unknown.
* Less dev familiarity.
* ecommerce is officially owned by a non-enterprise team.
* There’s code that might be “obviously reusable” from license-manager about request/approval of licenses that we’d have to duplicate into a different service.

license-manager
---------------
Benefits:

* It’s very natural to store requests for licenses in the service that is the source of truth for licenses.
* Approval of a request is very similar to assigning 1 or more licenses from a given subscription.
* It’s easy to work in, easy to deploy, and there’s high familiarity amongst the dev teams.

Drawbacks:

* It’s less natural to store requests for coupon codes here; the source of truth about coupons is the ecommerce service.
* A sense of “we like working in license-manager as the backend for learner/admin portal MFEs, so let’s just start putting every new thing here.”

Potential mitigations to the drawbacks we considered:

* Make a new Django app in license-manager like subsidy_requests to logically encapsulate this feature.  Make smart use of Django model inheritance to support possible future UX needs in a flexible way.
* Duplicate coupon data from ecommerce into license-manager (instead of relying on REST API sub-calls during request fulfillment).  This becomes more attractive if the ecommerce coupons API becomes unstable or less performant.

A new library like enterprise-subsidy
-------------------------------------
Benefits:

* Can plug into any service that needs a feature like request/approve subsidy.
* Don’t have to mix contexts between services.

Drawbacks:

* Still ties us to ecommerce deployments and maintenance.
* Overhead of maintaining a library along with services.
