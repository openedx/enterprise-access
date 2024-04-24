\0018 Access Policy Grouping
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
The enterprise access service will adopt a new table ``PolicyGroupAssociation``. Apart from boilerplate, this table
will define two fields: a non nullable FK `subsidy_access_policy` with related name `groups` and a non nullable UUID
char field: `enterprise_group_uuid`. A ``SubsidyAccessPolicy``'s `groups` will be read and factored in at time of
calculating redeemability of an individual policy for a learner. The intention is for every new access policy to have
at least one assigned group.

PolicyGroupAssociation
*********************
**Model properties**
------
- created, modified (boilerplate)
- subsidy_access_policy (NOT NULL, FK to SubsidyAccessPolicy, related_name=”groups”)
- enterprise_group_uuid (NOT NULL, char UUID)

How the systems will interact with one another
++++++++++++++++++++++++++++++++++++++++++++++
Upon provisioning a new access policy budget for a customer, the service will make a POST request to edx-platform to
create a new ``EnterpriseGroup`` record. On successful response, the enterprise-access service will write the returned
UUID of the newly created group to the new table ``PolicyGroupAssociation`` with the associated policy's UUID.

``SubsidyAccessPolicy``'s `can_redeem()` method already makes a request to edx-platform for
`get_enterprise_user()` in which `lms_user_id` and `enterprise_customer_uuid` are provided to confirm
a learner's membership with the associated organization. Now, instead returning `True` or `False` as a signature, the
`get_enterprise_user()` method will return the learner's serialized EnterpriseCustomerUsers record from the
`/enterprise-learner/` API or `None` if the user is not a part of the enterprise. This will retain any truthy based
logic dependent on the old functionality of `get_enterprise_user()` but will surface more information usable by
new consumers, namely `can_redeem()`.

Expected behavior of Access Policies by default
+++++++++++++++++++++++++++++++++++++++++++++++
As stated in the decision, while backwards compatible, it is assumed that each policy is assigned a group.
Groups, by definition, work to limit access to content or features within the greater set of an organization's
members. As such this implementation changes the expected behavior of a policy/(now also group) after provisioning
from the past assumption of universal learner access to policies connected to the enterprise. This presents an issue
with policy implementations like Browse and Request as well as organizations with SSO integrations, as we want to
streamline the onboarding process for enterprise customers, not requiring an additional step to add learners to groups/
policies that we know will apply to the entire org. While the new, default policy/group associations does present
a new level of filtering on availability of content, it will be left up to the group record contained in edx-platform
to determine who it applies to and if it contains the entire org. For situations where we want to skip having customer
admins manually associate all learners, the group will provide an ``all_context`` flag that can be set at the time of
provisioning, based on subsidy type.

Consequences
************
`can_redeem()` will introduce new behavior that will result in situations where learners of an organization may be
denied from redeeming an access policy. However, this will be backwards compatible behavior where the lack of group
architecture will expand, and maintain previous availability. It's important to consider the downstream effects of and
the consumers of the `can_redeem()` method, namely the `/credits-available/` API endpoint. This view will now only
return available policies tied to groups which the learner is a member of, instead of all available policies under the
organization.
