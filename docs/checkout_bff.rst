=============================================
Understanding the Checkout BFF Implementation
=============================================

What is a BFF anyway?
---------------------

The Backend-for-Frontend (BFF) pattern creates a dedicated backend service layer
specifically designed to serve a particular frontend. In our case,
we've built a Checkout BFF to support our self-service purchasing flow via
the checkout micro-frontend (MFE).

Rather than having our frontend make multiple calls to different backend services
(enterprise-access, license-manager, edx-platform, etc.), the BFF aggregates
these into a single, optimized API tailored specifically for the checkout flow.

How it all fits together
------------------------

Our implementation follows this general pattern::

    Request → ViewSet → Context → Handler → Response Builder → Response

Let's walk through how a request flows through the system:

1. Request comes in to the ViewSet
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Note below that the word "context" is severely overloaded. There's the notion of a
``Context`` *class*, which is a data container for a BFF flow; while ``get_context(self, request)``
is the action view within our Checkout ViewSet that supports the ``/api/v1/bffs/checkout/context/`` endpoint.
For consider mentally replacing that little 'c' "context" with "overview", in your mind, i.e.
``/api/v1/bffs/checkout/overview/``.

::

    @action(detail=False, methods=['post'], url_path='context')
    def get_context(self, request):
        """Get the checkout context data."""
        response_data, status_code = self.load_route_data_and_build_response(
            request,
            CheckoutContextHandler,
            CheckoutContextResponseBuilder,
            CheckoutContext,
        )

        return Response(response_data, status=status_code)

The ViewSet receives the request and calls ``load_route_data_and_build_response``,
which is a method inherited from ``BaseBFFViewSet``. This method orchestrates the entire flow.

2. Context is created
~~~~~~~~~~~~~~~~~~~~~

::

    context = context_class(request=request)

The ``CheckoutContext`` is instantiated with the request. It parses and validates
request parameters, extracts user information, and prepares a structure to hold
data throughout the request lifecycle. Think of it as a container for all the
data needed to process the request.

Context objects have methods to:

* Add errors: ``context.add_error(user_message="...", developer_message="...")``
* Add warnings: ``context.add_warning(user_message="...", developer_message="...")``
* Store business data: ``context.pricing``, ``context.field_constraints``, etc.

3. Handler is instantiated and processes business logic
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    handler = handler_class(context)
    handler.load_and_process()

The ``CheckoutContextHandler`` receives the context and processes business logic. This includes:

* Fetching pricing data from Stripe
* Loading field constraints
* Checking for existing customers

Handlers do all the heavy lifting. They fetch data from external services,
validate inputs, and populate the context with everything needed for the response.

4. Response Builder formats the data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    response_builder = response_builder_class(context)
    response_builder.build()
    response_data, status_code = response_builder.serialize()

The ``CheckoutContextResponseBuilder`` takes the populated context and
structures it into a standardized response format. It:

1. Calls ``build()`` to prepare the response dictionary
2. Calls ``serialize()`` to validate and convert the data through serializers
3. Returns both the data and appropriate status code

Response builders ensure consistency across all BFF endpoints.

The Stripe Pricing Integration
------------------------------

The BFF integrates with Stripe to fetch real-time pricing data.
We've built a dedicated pricing API module that:

* Fetches and caches price data from Stripe
* Serializes it into a consistent internal format
* Handles error cases gracefully
* Provides helper functions for formatting prices for display

::

    pricing_data = get_stripe_price_data('price_123abc')
    context.pricing = pricing_data

Best Practices Implemented
--------------------------

We've followed several best practices in this implementation:

1. **Separation of concerns**:

   * Context classes handle data storage
   * Handlers contain business logic
   * Response builders format the output
   * ViewSets handle routing

2. **Error handling**:

   * Consistent error format with both user-friendly and developer messages
   * Error collection throughout the request lifecycle
   * Appropriate status codes

3. **Caching**:

   * Stripe pricing data is cached to reduce API calls
   * Tiered caching approach for optimal performance

4. **Validation**:

   * Input validation happens early in the request lifecycle
   * Business validation in handlers
   * Output validation through serializers

How to Extend This Pattern
--------------------------

When you need to add a new endpoint to the BFF:

1. Create a new Context class or extend an existing one
2. Create a Handler that implements your business logic
3. Create a Response Builder that formats your data
4. Add a method to the ViewSet that wires them together

The beauty of this pattern is its consistency and modularity -
each component has a clear, single responsibility, making the code easier to
understand and extend.
