0025 Stripe Event Consumption And Delivery
******************************************

Status
======
**In progress** (March 2025)

Context
=======

We intend to utilize Stripe to manage subscription and payment lifecycles for the Enterprise Self-Service Purchasing
feature. We will need a robust way to ingest events from Stripe, handle those events, and forward them to SalesForce.
For example, when Stripe emits an ``invoice.paid`` event, that needs to be communicated to SalesForce in order to
trigger the creation/update of Customers, Opportunities, etc. Furthermore, that same event needs to be communicated to
our edX Enterprise backend so that we can immediately followup with the customer to confirm that we have begun to
provision their product.

At minimum, "robust" means:
- Spoofed events are discarded.
- Network failure does not cause dropped events.
- Network failure does not cause events to be handled multiple times, or duplicate products provisioned.

Decision
========

The selected approach is to configure Stripe to POST two separate WebHook endpoints for every event:

#. POST to a new django view in the edX backend (tentatively enterprise-access).
#. POST to a custom SalesForce endpoint.

.. image:: ../images/0025-stripe-event-consumption-delivery.png

Architecture of the edX backend WebHook endpoint
------------------------------------------------

#. **Ingest:** Implement a new webhook endpoint in the edX Enterprise backend.

   * IDA and URL: TBD.

   * The view logic should robustly ingest Stripe events, storing them in the DB.

     * Use `signature validation <https://docs.stripe.com/webhooks#verify-official-libraries>`_.

     * Use `de-duplication <https://docs.stripe.com/webhooks#handle-duplicate-events>`_.

   * After successful ingestion, respond with HTTP 200 to tell Stripe to stop retrying sending the event.

#. **Pick:** Implement an asynchronous event picker to continuously try to pick events to handle/forward.

   * Architecturally, this is an infinite loop via management command running in a dedicated container/pod.

   * Ingested events which have not been handled will cause ``handle_stripe_event`` tasks to be queued.

   * In order to give celery time to handle the event without sacrificing loop frequency, throttle queue requests for a
     given event ID by checking the TaskResult table just like `how we throttle tasks in enterprise-catalog
     <https://github.com/openedx/enterprise-catalog/blob/01f5367309ee25093e414b0fd3498a48ec575073/enterprise_catalog/apps/api/tasks.py#L134>`_.
     A recent TaskResult record for a given event is like a lock that prevents the same event from re-queuing.

   * Scheduling order: In case multiple events for the same customer are pending, we should make a best effort to chain
     the task requests for a given customer (Celery tasks can be natively chained). Handling events out of order for a
     given customer could expose weird corner cases.

#. **Handle:** Implement a celery task to "handle" an event.

   * The handler task should mark the event has handled by setting the ``handled`` boolean on the event record to ``true``.

   * Behavior will differ based on the event type received.

Architecture of the SalesForce WebHook endpoint
-----------------------------------------------

This endpoint will be provided by the `Stripe Connector App
<https://docs.stripe.com/plugins/stripe-connector-for-salesforce/installation-guide>`_ to directly ingest Stripe events
within SalesForce.

SalesForce Engineers will then need to trigger custom APEX code (or other automation) to handle `invoice.paid` and other
events. As a SOQL query, recent `invoice.paid` events can be fetched like this::

  select CreatedDate,
         stripeGC__Request_Body__c
  from stripeGC__Stripe_Event__c
  where stripeGC__Event_Name__c = 'invoice.paid'
    and stripeGC__Is_Live_Mode__c = true
  order by CreatedDate desc
  limit 10

Alternatives Considered
=======================

Rejected
========

* Queue event handler immediately as part of the ingestion, instead of via an asynchronous picker.

  * This is too sensitive to errors during ingestion, which could result in dropped events.

* Use a k8s ``cronjob`` to perform the "Pick" role (infinite loop).

  * This was okay, but cronjobs have a minimum frequency of minutely, which could add an uncomfortable amount of delay.

* Use a Celery "beat" to perform the "Pick" role (infinite loop) using a 1-second period.

  * The beats scheduler is configured by default to wake up every 5 seconds, resulting in a minimum scheduling period of
    5 seconds.  We do really need 1 second. Theoretically we could reconfigure it to wake up every second.

  * It's unclear from documentation what happens if beats start taking longer than the configured period.

  * I thought maybe this could save us time setting up infrastructure when compared with a dedicated picker, but it
    turns out celery beats don't just magically run inside an existing worker. They actually need to run inside their
    own dedicated container.

* We considered making the edX backend an intermediary for Stripe events bound for SalesForce.

  * This was ultimately rejected on the basis of the Stripe Connector App giving us just enough features to tip the
    cost/benefit balance.

  * By acting as intermediary, we would need to implement all of the following additional features in-house:

    * Provision SalesForce endpoints to act as WebHook listeners.

    * Write APEX handlers to validate stripe event signatures, de-duplicate events sent multiple times on accident, and
      write events to a table.

    * Write a "forwarding" handler task within the edX Enterprise backend to reliably POST and retry POSTing events to
      SalesForce.

Consequences
============

I haven't quite figured out how to easily monitor event deliveries via the SalesForce Connector App in a way that can be
alerted via DataDog. **Open Question**: If we integrated DataDog into SalesForce, could we monitor any of these `event types
<https://developer.salesforce.com/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_eventlogfile_supportedeventtypes.htm>`_
to count APEX executions?
