Cache design and use
####################

Cache layers
************

For some purposes, we use the Django core cache directly.  In enterprise-access, memcached
is the default caching backend in both local and production-like environments.

For other purposes, we utilize both a ``RequestCache`` and ``TieredCache`` provided by
`<https://github.com/openedx/edx-django-utils/tree/master/edx_django_utils/cache>`_.
The RequestCache is a request-scoped dictionary that stores keys and their values
and exists only for the lifetime of a given request.
TieredCache combines a RequestCache with the default Django cache.

Versioned cache keys
********************

For both ``RequestCache`` and ``TieredCache`` invocations, we use versioned cache keys
to support key-based cache invalidation.
See `<https://signalvnoise.com/posts/3113-how-key-based-cache-expiration-works>`_ for background on this design (note
that the design presented in this blog post is somewhat more complex that the design used in enterprise-access).

Our cache key version currently consists of two components: the ``enterprise_access`` package version
defined in ``enterprise_access.__init__.py``, and an optional Django settings called ``CACHE_KEY_VERSION_STAMP``.

This optional settings-based component can be changed to effectively invalidate **every** cache
key in the Django-memcached server in whatever environment the setting is defined in, as long as that key
was set via our cache utility functions that build versioned-keys.  The value of this setting
isn't particularly important, something like a datetime string will work fine.

.. code-block::
   
   # In your environment's Django settings file.
   CACHE_KEY_VERSION_STAMP = '20230607123455'

In the future, we hope to incorporate upstream changes to data in the enterprise-catalog service
into our key-based invalidation scheme, so that the timeouts described below become unnecessary to maintain.

Where we cache
**************

``SubsidyAccessPolicy`` resource locking
========================================
We utilize the core Django cache to lock resources in the ``SubsidyAccessPolicy`` redeem flow - the
lock is set in the ``redeem`` endpoint.

More details about our locking strategy can be found in `<0005-access-policy-locks.rst>`_ and
`<0007-access-policy-locks-revised.rst>`_.


``The Content Metadata API``
============================
The ``content_metadata_api`` module uses a ``TieredCache`` to store metadata associated
with content keys, and to store boolean results indicating if a given content key is
contained in a given customer catalog. We use a tiered cache strategy for this data because,
the set of all content metadata is well-bounded and changes
somewhat slowly (perhaps daily, at most).

The timeout for both types of these cache entries can be configured via your environment's settings
using the variable ``CONTENT_METADATA_CACHE_TIMEOUT``, which should be an integer representing
the Django-memcached timeout in seconds.  If not set, both types of cache entries use a default
timeout of 5 minutes (300 seconds).  In general, the cache timeout value for this data
type should be no more than several hours, which is long enough to be useful for performance purposes,
and short enough to limit the downside risk of operating on stale data.

.. code-block::
   
   # In your environment's Django settings file.
   CONTENT_METADATA_VIEW_CACHE_TIMEOUT_SECONDS = 60 * 13  # Make the cache timeout 13 minutes
