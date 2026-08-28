0023 Replicated Content Metadata
********************************

Status
======
**Proposed** (July 2024)

Context
=======
We rely on content metadata from an upstream service, enterprise-catalog,
for many of our flows. In particular, all of our flows related to content assignments
depend on content metadata, beyond just the ``content_key`` related
to a given ``LearnerContentAssignment``. Specifically, these flows
all depend on content start dates for determining the expiration time of an assignment:
* Our assignment read API
* The assignment allocation write API
* The ``credits-available`` endpoint
* The assignment expiration management command
* Email-sending tasks to create, remind, and nudge learners about assignments

Additionally, future feature enhancements will rely on knowing the ``parent_content_key``
related to a particular assignment. Specifically, assigning course *runs*, as opposed
to top-level courses, will depend on this field of content metadata.

Decision
========
Instead of fetching and caching content metadata from enterprise-catalog
(the upstream system of record), we should fetch and persist this data in
the database via a Django Model. Persisting content metadata in a dedicated model
would allow us to create FKs from other models that refer to such a model. This gives
us the following advantages:
* We could allow for filtering/sorting/searching on content metadata fields in the
  typical Django/DRF manner.
* We could include content metadata fields in serialized API response payloads
  via typical Django/DRF methods.
* We could write/modify business logic in management commands, tasks, etc. based
  on such a model, allowing us to bypass calls to the upstream service.

High-level implementation
-------------------------
General outline of a phased approach here:
1. Introduce model(s) to persist replicated content metadata.
2. Define a serializer (or similar) for turning the response payload
   into an instance of the model above.
3. Define nullable foreign key from ``LearnerContentAssignment`` to the new model.
4. Begin asynchronously populating model instances on reads or writes during a subset
   of the assignment flows enumerated above.
5. Define and implement a strategy for refreshing replicated metadata
   (e.g. via event bus, or cron, or on read, etc.).
6. Begin reading from replicated model instead of relying on ``get_and_cache_catalog_content_metadata()``
   to fetch/cache without persistence.

There will naturally be iteration between steps. For example, we'll likely
need to modify or augment our serialization logic and/or fields in (2)
as we attempt to execute step (6).

Consequences
============
* We make more explicit the dependency of the assignments domain on content metadata.
* ...

Alternatives Considered
=======================

Continue to fetch and cache
---------------------------
This is our current state. While it does support the flows stated above, it doesn't
easily support filtering/sorting/searching.  Furthermore, much of the serialization logic
is complex (particularly with the context of ``credits-available``), which is a tradeoff
we've made to enhance the performance of that serialization and its dependent views.

Fetch and persist on models of other domains
--------------------------------------------
This is partially our current state as well - for instance, we store the content title
directly on the ``LearnerContentAssignment`` model. This alternative becomes less
attractive as we start to replicate fields onto models from *multiple* dependent domains.
For example, there are flows in the domain of ``subsidy_access_policy`` that depend
on content metadata fields.
