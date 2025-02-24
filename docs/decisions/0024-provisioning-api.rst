0024 A Customer/Subsidy Provisioning API
****************************************

Status
======
**In progress** (February 2025)

Context
=======
We want to be able to automatically provision new customer records and subscription plans
(and eventually learner credit budgets) with a single API call from a client.
 For example, the following business records will need to be created
 (via service-to-service API calls) in the provisioning of a net-new subscription customer:

1. Creation of an EnterpriseCustomer record.
2. Creation of PendingEnterpriseCustomerAdmin record(s).
3. Creation of an EnterpriseCustomerCatalog record.
4. Creation of a CustomerAgreement record.
5. Creation of a SubscriptionPlan record.

Such an endpoint would be helpful not just for external clients (customers), but also
from internal tools, such as the enterprise provisioning page in the support-tools MFE,
and back-office tools and systems that fulfill enterprise subsidy contracts.

Decision
========
1. We'll start by introducing a provisioning API with a single endpoint that supports
   the creation of new subscription-based customers.
2. In the future, we'll add explicit endpoints to modify existing business records or to add
   new subsidy records for existing customers.
3. We'll model the provisioning business logic as a "workflow" or "pipeline", in which
   each step of the pipeline is assumed to be idempotent. For the sake of the ``create`` endpoint,
   this means that each step can be thought of as a get-or-create action.

Alternatives Considered
=======================
We've previously considered requiring clients to call each required business domain API,
in the correct sequence, with a valid set of inputs to each. This was rejected because
it requires that any client with a need to provision enterprise business records acquire
the requisite domain expertise to understand what each business record represents, maintain
the correct sequential order, gracefully handle exceptions, and so on. That (rejected)
solution may also hamper our ability to introduce new business domain models or flows in the future
(because it would risk breaking clients' existing provisioning flows).

Consequences
============
The serializers we create will define the interface and boundary behavior between
the provisioning API and clients thereof. We'll have to balance how flexiblility
of this boundary with other attributes, such as how prescriptive the input side of the
boundary is, and how extensible a provisioning flow can be.
