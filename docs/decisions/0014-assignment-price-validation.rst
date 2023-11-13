0014 Assignment Price Validation
********************************

Status
======

Accepted - November 2023

Context
=======
In the realm of Assigned Learner Credit, we want clients to be able to allocate
at the price that is **currently advertised** to them.  There are several factors at play, here:

1. The advertised price is displayed in the Admin Portal, and it is fetched from our Algolia search index.
2. The actual price for a course may change as often as daily, due to currency conversion fluctuations (or
   other outside forces).
3. On the allocation/redemption backend (enterprise-access and enterprise-subsidy), the current price of a course
   is fetched from the enterprise-catalog service.
4. Due to the nature of our scheduled crons that sync new metadata to enterprise-catalog, and which
   update our Algolia search index, there may be periods in any given day where the price of some
   content record in Algolia does **not** match the current price for that record in the enterprise-catalog service.


Decision
========
We'll continue to expect that clients of the ``allocate`` view provide their current understanding of the course
price as input.  The ``allocate`` view will now validate the client-provided price to prevent abuse. It
will ensure that the client-provided price falls within some error bounds around the current price
according to the enterprise-catalog service.  These size of this error bound can be controlled
via new Django settings ``ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO`` and
``ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO``.

Consequences
============
Client-provided price is a bit of an anti-pattern, and it should be eschewed when possible in the future.

Alternatives Considered
=======================

Backends and frontends read price from the same source
------------------------------------------------------
If the Admin Portal frontend and Assignments backend (enterprise-access) reliably
read course price from the same source, we wouldn't need to do this (in fact,
the ``allocate`` view wouldn't require the client to provide price as an input).
This is actually the preferred state of affairs, but requires more
effort than we currently have bandwidth to achieve.
