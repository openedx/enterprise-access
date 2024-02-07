0018 Access Policy Grouping
###########################

Status
******
**Accepted** Feb 2024

Context
*******
The enterprise-access service needs to allow for the flexibility to control and distribute subsidy access policies to
custom subsets of learners. The subdivision of access policies will improve user management and budgeting from an admin
perspective, as well as add support for personalization to the learner experience down the line. While it is not
the responsibility of the service to house the grouping of learners, an access policy must take into account the 
existence of related subsets when determining if a subsidy is redeemable by an individual learner.

Decision
********
The ``SubsidyAccessPolicy`` model will adopt a new, nullable UUID field: `enterprise_group_uuid`. This field will be
read and factored in at time of calculating redeemability of an individual policy for a learner. The intention is for
every new access policy to have an assigned group.

How the systems will interact with one another
++++++++++++++++++++++++++++++++++++++++++++++
Upon provisioning a new access policy budget for a customer, the service will make a POST request to edx-platform to
create a new ``EnterpriseGroup`` record. On successful response, the enterprise-access service will write the returned
UUID of the newly created group to the access policy `enterprise_group_uuid` field.

``SubsidyAccessPolicy``'s `can_redeem()` method already makes a request to edx-platform for
`enterprise_contains_learner()` in which `lms_user_id` and `enterprise_customer_uuid` are provided to confirm
a learner's membership with the associated organization. Now, `enterprise_group_uuid` will be optionally supplied by
`can_redeem()` if it exists on the individual policy record. This value will further filter down the list of learners
that have access to the subsidy linked to a policy. 

Consequences
************
`can_redeem()` will introduce new behavior that will result in situations where learners of an organization may be
denied from redeeming an access policy. However, this will be backwards compatible behavior where the lack of group
architecture will expand, and maintain previous availability. It's important to consider the downstream effects of and
the consumers of the `can_redeem()` method, namely the `/credits-available/` API endpoint. This view will now only
return available policies tied to groups which the learner is a member of, instead of all available policies under the
organization.
