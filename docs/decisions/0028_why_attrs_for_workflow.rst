`attrs`/`cattrs` vs. DRF Serializers for Workflows
**************************************************

:Author: iloveagent57
:Date: 2025-05-19
:Version: 1.0

.. contents:: Table of Contents

Status
======
**Accepted** (April 2025)

Context
=======

In the ``openedx/enterprise-access`` repository, and in many modern Python applications, different tools are
employed for data serialization and structuring at various architectural layers. This document clarifies
the distinct roles of Django Rest Framework (DRF) Serializers and the ``attrs``/``cattrs`` libraries,
explaining why both are used.

Django Rest Framework (DRF) Serializers
----------------------------------------

Primary Purpose
^^^^^^^^^^^^^^^
Designed for **API request and response handling**. Their core function is to convert complex
data types (like Django model instances or querysets) into Python native datatypes
that can then be easily rendered into formats like JSON for API responses. Conversely,
they deserialize and validate incoming API request data (e.g., JSON payloads) into Python objects or model instances.

Key Strengths
^^^^^^^^^^^^^
*   **API Contract Definition**: Excellent for defining the precise structure, data types,
    and validation rules for external API endpoints.
*   **Model Integration**: ``ModelSerializer`` offers tight integration with Django models,
    automatically generating fields and validation from model definitions.
*   **Validation**: Provides a robust validation framework, supporting both field-level
    and object-level validation rules.
*   **Representation Control**: Manages how data is presented in API responses,
    including handling nested relationships and hyperlinking.
*   **Web Context Awareness**: Deeply integrated with DRF's views, request/response cycle,
    and features like the browsable API.

Typical Use Case in ``openedx/enterprise-access``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
As seen in ``enterprise_access/apps/api/serializers/provisioning.py``,
DRF serializers define the expected input and output structures for the ``/api/v1/provisioning/`` endpoint.
They act as the "gatekeepers" for external communication, ensuring data conforms to the API's contract.

`attrs` and `cattrs`
--------------------

`attrs` - Primary Purpose
^^^^^^^^^^^^^^^^^^^^^^^^^
Facilitates writing Python classes with significantly less boilerplate code.
It automatically generates common methods like ``__init__()``, ``__repr__()``, ``__eq__()``, etc.,
based on declared attributes. Its focus is on the quick and clean creation
of **structured data classes** for general use within Python code.

`cattrs` - Primary Purpose
^^^^^^^^^^^^^^^^^^^^^^^^^^
Complements ``attrs`` by providing powerful and flexible **structuring** (converting unstructured data,
like dictionaries, into ``attrs`` instances) and **unstructuring** (converting ``attrs`` instances
back into dictionaries or other simple Python types). It is highly effective for serialization and
deserialization tasks *between internal application components*.

Key Strengths
^^^^^^^^^^^^^
*   **Internal Data Structures**: Ideal for defining clear, validated data structures for
    internal application logic, Data Transfer Objects (DTOs) between different layers, or
    representing domain-specific concepts internally.
*   **Lightweight and Flexible**: More streamlined than DRF serializers when the primary need is for
    structured data classes and their conversion, without the additional overhead of API-specific features. `
    `cattrs`` offers extensive configuration options.
*   **Decoupling Internal Components**: Enables internal systems (like the workflow engine)
    to operate with well-defined data objects without being tightly coupled to the REST API layer's
    specific serialization mechanisms or assumptions.
*   **Internal Type Safety**: Validators within ``attrs`` help maintain data integrity as objects
    are passed between different parts of the application.

Typical Use Case in ``openedx/enterprise-access``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
*   **Workflow Data Contracts**: In ``enterprise_access/apps/workflow/serialization.py``,
    the ``BaseInputOutput`` class (which leverages ``attrs``/``cattrs`` patterns) serves as the
    foundation for defining the precise data structure that each workflow step
    expects as input and produces as output.
*   **Step-Specific Data**: In ``enterprise_access/apps/provisioning/models.py``, classes such as
    ``GetCreateCustomerStepInput`` and ``GetCreateCustomerStepOutput`` are ``attrs`` classes.
    They define these internal data contracts for the step that creates a new ``EnterpriseCustomer`` record
    within the ``ProvisionNewCustomerWorkflow``.
*   **Database Serialization**: The ``input_data`` and ``output_data`` fields on ``AbstractUnitOfWork``
    (and thus on ``AbstractWorkflow`` and ``AbstractWorkflowStep``) are stored as JSON in the database.
    ``attrs`` classes define the schema for this JSON, and ``cattrs``
    (via methods like ``from_dict()`` and ``to_dict()``, inherited from ``BaseInputOutput``)
    handles the conversion between the ``attrs`` objects and these JSON-friendly dictionary representations.

Decision
========

We'll use both DRF Serializers and ``attrs``/``cattrs``. This decision stems from a desire for
clear architectural layering and choosing the right tool for the job:

Separation of Concerns / Layering
---------------------------------
*   **DRF Serializers** are best suited for the **REST API boundary**. They manage the
    "translation" between the external world (HTTP requests/responses) and the application's internal domain logic.
*   ``attrs``/``cattrs`` excel at defining and managing structured data
    **within the application's internal layers**. This includes data passed between workflow steps,
    service functions, or other internal components. This approach keeps internal
    logic cleaner and decoupled from REST API-specific concerns.

Context and Overhead
--------------------
*   Employing DRF Serializers for purely internal data objects
    (like the input/output definitions for workflow steps) would introduce unnecessary context and
    overhead related to HTTP requests, responses, HTML forms, etc.,
    which are not relevant in those internal scenarios.
*   ``attrs``/``cattrs`` provide a more lightweight and focused solution for these internal data structuring needs.

Flexibility for Internal Logic
------------------------------
The workflow pattern, for instance, needs to dynamically compose input and output structures
for entire workflows based on their constituent steps (as seen with the dynamic generation
of ``input_class`` and ``output_class`` in ``AbstractWorkflow``). ``attrs`` and ``cattrs``
offer the necessary flexibility to define these internal data classes and manage their
serialization/deserialization to/from JSON (for database storage) without requiring the full DRF machinery.


Potential Disadvantages of Using Both Systems
---------------------------------------------

Increased Cognitive Load / Learning Curve
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
*   Developers must become proficient in two distinct systems for data structuring, validation,
    and serialization. This includes understanding the APIs, conventions, and best practices
    for both DRF Serializers and ``attrs``/``cattrs``.
*   It can be challenging for new team members to discern when to use which tool or how they interact,
    particularly at the boundary where API data is transformed into internal workflow data structures.

Potential for Boilerplate/Duplication
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
*   Although both toolsets aim to reduce boilerplate in their respective domains,
    there's a risk of defining similar data structures or validation rules in two places:
    once in a DRF serializer for the API contract and again in an ``attrs`` class
    for the internal representation (e.g., a workflow step's input).
*   For instance, if an API endpoint accepts a "name" (string, max 100 characters)
    and an "email" (valid email format), and an internal workflow step also requires these exact fields
    with identical validation, these constraints would need to be defined in both
    the DRF serializer and the corresponding ``attrs`` class.
*   While bridging mechanisms (like the ``ProvisionNewCustomerWorkflow.generate_input_dict()`` method) help,
    the underlying definitions might still feel somewhat duplicated.
*   If the "duplication" of definitions becomes a significant impediment or a frequent source of bugs,
    exploring alternatives such as rigorous conventions and helper functions to bridge
    DRF serializer definitions and ``attrs`` class definitions could be valuable avenues for improvement.

Maintenance Overhead
^^^^^^^^^^^^^^^^^^^^
*   If a data structure evolves (e.g., a field is added, removed, or its validation rules are modified),
    updates may be required in two locations—the DRF serializer and the associated ``attrs`` class(es).
    This increases the likelihood of inconsistencies if one is updated and the other is overlooked.

Complexity at the "Seam"
^^^^^^^^^^^^^^^^^^^^^^^^
*   The interface point where DRF serializers hand off data to be consumed by ``attrs`` classes
    (e.g., within an API view that processes request data to populate workflow input) can introduce complexity
    or subtle bugs if not managed with care. The mapping logic must be clear, correct, and consistently maintained.

Slightly More Dependencies
^^^^^^^^^^^^^^^^^^^^^^^^^^
*   The approach introduces additional libraries (``attrs``, ``cattrs``) into the project.
    However, these are well-regarded and commonly used within the Python ecosystem.

Alternative Approaches (and their Trade-offs)
=============================================

Using Only DRF Serializers (for both API and Internal Logic)
------------------------------------------------------------
Pros
^^^^
*   **Single System**: Reduces cognitive load as developers only need to master DRF serializers.
*   **Less Duplication**: Data structures and validation rules are typically defined once.
Cons
^^^^
*   **Tight Coupling**: Internal logic (such as workflows) can become more tightly coupled to the API
    layer's data representation. Changes to internal data structures might inadvertently impact API contracts.
*   **Heavier for Internal Use**: DRF serializers carry more "web context" and associated overhead
    than might be necessary for purely internal data objects.
*   **Less Ideal for Non-API Data**: If internal processes exist that do not originate from an API call,
    but still require structured data, DRF serializers might feel like an awkward or overly complex fit.
*   **Dynamic Composition Challenges**: Dynamically constructing serializers with fields based on a
    list of other serializers (analogous to how ``AbstractWorkflow`` dynamically
    creates its ``input_class`` from step inputs) is feasible but can be more
    cumbersome with DRF serializers compared to ``attrs``.

Using Pydantic (for both API and Internal Logic)
------------------------------------------------
*   **Pydantic** is a widely adopted library for data validation and settings management
    using Python type hints. It is often considered a strong alternative or complement in this space.
Pros
^^^^
*   Pydantic leverages Python type hints for defining data structures, 
*   Offers powerful and flexible validation capabilities.
*   DRF Integration: Libraries such as ``drf-pydantic`` enable the direct use of Pydantic models
    as DRF serializers, potentially offering the benefits of both systems by reducing definition duplication.
Cons
^^^^
*   Without an integration library, a translation layer between Pydantic models (for internal use)
    and DRF serializers (for API use) would still be necessary, similar to the existing ``attrs``/DRF separation.
*   **Consideration for ``openedx/enterprise-access``**: Given the established use of ``attrs``/``cattrs``,
    migrating to Pydantic would represent a significant refactoring effort.

Standard Library ``dataclasses`` (with custom validation/serialization)
-----------------------------------------------------------------------
Pros
^^^^
*   **No External Dependencies (beyond Python itself)**: ``dataclasses`` have been part of the standard library
    since Python 3.7.
*   **Simple Data Structures**: Effective for defining straightforward data-holding classes.
Cons
^^^^
*   **No Built-in Validation**: Validation logic would need to be implemented manually or by
    integrating a separate validation library.
*   **No Built-in Advanced Serialization**: While basic conversion to/from dictionaries is possible,
    more complex scenarios (like consistent handling of ``UUID``s or ``datetime`` objects to JSON)
    would require custom code or functionality similar to that provided by ``cattrs``.

Consequences
============
The chosen approach — DRF Serializers for the API layer and ``attrs``/``cattrs`` for internal workflow data —
provides tangible benefits and disadvantages. The disadvantages below are common trade-offs in software design
when striving for modularity and utilizing specialized tools. We believe, for our uses, that these disadvantages
are outweighed by the benefits outlined above.
