##################################
Provisioning Workflow Architecture
##################################

:Author: iloveagent57 (human), Copilot/Gemini 2.5 (LLM)
:Date: 2025-05-19
:Version: 1.0

.. contents:: Table of Contents

Introduction
***************

This document provides an architectural overview of the provisioning system and the underlying generic workflow
pattern within the ``openedx/enterprise-access`` repository. These components are designed to automate the setup
and management of enterprise customers, their administrators, catalogs, and subscriptions. The architecture
emphasizes a layered approach, clearly separating REST API handling, workflow orchestration (the workflow
layer), core business logic, and data serialization.

Components
**********

The provisioning app is primarily composed of modules within ``enterprise_access/apps/api`` (the REST API layer),
``enterprise_access/apps/provisioning`` (specific provisioning logic and workflow definition),
and ``enterprise_access/apps/workflow`` (the generic workflow pattern).

Generic Workflow Pattern
========================
This application provides the abstract, reusable building blocks for creating various automated, multi-step
processes. It forms the core of the workflow layer. There's a good overview in ``enterprise_access/apps/workflow/docs``,
and a brief recap here. This pattern is domain-agnostic; there's nothing inherently relevant to provisioning
within the ``apps/workflow`` directory.

Workflow Data Serialization
---------------------------
Responsible for defining and managing the structure of data as it moves through various steps
within the workflow layer. It defines schemas for workflow inputs and outputs,
ensuring data consistency and facilitating serialization/deserialization between Python objects
used in the workflow layer, and JSON suitable for database storage or HTTP responses.

``attrs`` is used to define Python classes representing structured data
(like step inputs/outputs) with minimal boilerplate. ``cattrs`` handles the conversion between these
``attrs`` instances and Python dictionaries (which are then typically serialized to/from JSON).
See the decision document in ``docs/decisions`` about our choice of these libraries.

``BaseInputOutput`` serves as a base for specific input/output data classes
used by workflow steps and the overall workflow. These ``attrs``-based classes define
fields, validators, and often include a static ``KEY`` attribute used
by ``AbstractWorkflow`` for data mapping.

``AbstractUnitOfWork``
------------------------
This is the most fundamental building block in the workflow layer. It represents a single, atomic,
executable piece of work that is persisted as a Django model. Both
individual workflow steps and entire workflows are considered "units of work."

These units of work store ``input_data`` and ``output_data`` as JSON, alongside execution
status timestamps (``succeeded_at``, ``failed_at``) and any exception messages.
Subclasses define ``input_class`` and ``output_class`` (``attrs`` classes) to structure this data.

The ``execute()`` method wraps a call to an abstract ``process_input()`` method (which concrete subclasses
must implement). ``execute()`` handles serialization of outputs, status updates, and exception logging.

``AbstractWorkflowStep``
-------------------------
Represents a single, persistent step *within* a larger workflow in the workflow layer.

It encapsulates one part of a multi-stage process. It inherits all capabilities of ``AbstractUnitOfWork``
for data handling and execution.

It also contains a ``workflow_record_uuid`` (linking to the parent workflow instance) and a
``preceding_step_uuid`` (linking to the previous step in the sequence, if any).

``AbstractWorkflow``
-------------------------
Represents the entire workflow, orchestrating a sequence of ``AbstractWorkflowStep`` instances within the
workflow layer.

It defines and manages a sequence of operations (steps) to achieve a larger business goal. Concrete
workflow classes inherit from this and define a ``steps = []`` list, which is a sequence of
``AbstractWorkflowStep`` *classes*.

Its ``input_class`` and ``output_class`` are dynamically generated ``attrs`` classes. The fields of these
classes correspond to the ``KEY`` attributes of the input/output classes of its constituent steps,
creating a composite input/output structure for the entire workflow.

Orchestration Logic (``process_input()``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
This method contains the core logic for executing the workflow. It iterates through its defined ``steps``:

1.  For each step class, it gets or creates a persistent model instance for that step, linking it to the
    current workflow and the preceding step.
2.  It passes the relevant part of the workflow's overall input to this step instance.
3.  It calls ``execute()`` on the step instance (if not already succeeded).
4.  The output of the current step is added to an ``accumulated_output`` object (an instance of the
    workflow's ``output_class``). This ``accumulated_output`` is available to subsequent steps.

Finally, it returns the fully populated ``accumulated_output`` object.

Provisioning API Core Logic (``apps/provisioning/api.py``)
==========================================================
This module sits between the workflow layer and external service integrations, containing atomic,
idempotent functions for specific provisioning tasks.

It provides the low-level business logic for interacting with external systems (e.g., LMS, License
Manager) via their respective API clients (``LmsApiClient``, ``LicenseManagerApiClient``). These
functions are designed to be callable and produce the same result if called multiple times with the
same inputs (idempotency).

It includes functions like ``get_or_create_enterprise_customer()``,
``get_or_create_enterprise_admin_users()``, etc. These are invoked by the ``process_input()``
methods of the concrete workflow *steps* defined for provisioning. These functions **are designed to be idempotent**.
This means calling them multiple times with the same input parameters will produce
the same outcome without unintended side effects (e.g., creating duplicate entities). This is crucial for
reliability and allows for safe retries of workflow steps if needed.

Provisioning Workflow Layer (``apps/provisioning/models.py``)
=============================================================
This module defines the concrete implementation for the "Provision New Customer" workflow, applying the
generic workflow pattern to the specific domain of provisioning. This is a key part of the workflow
layer for provisioning.

Step-Specific Input/Output Classes
----------------------------------
For example, ``GetCreateCustomerStepInput`` and ``GetCreateCustomerStepOutput``. These inherit from
``BaseInputOutput`` (from ``apps/workflow/serialization.py``), defining the precise ``attrs`` structure
and validation for a step's input and output, including a ``KEY`` attribute for data mapping.

Concrete Provisioning Step Classes
----------------------------------
For example, ``GetCreateCustomerStep(AbstractWorkflowStep)``. These classes inherit from ``AbstractWorkflowStep``,
define their specific ``input_class``, ``output_class``, and an ``exception_class``. 
Primarily, they implement the ``process_input()`` method, which contains the step's specific logic. This typically
involves:

*   Accessing its specific input via ``self.input_object``.
*   Accessing outputs of *previous* steps via the ``accumulated_output`` parameter (e.g.,
    ``accumulated_output.create_customer_output.uuid``).
*   Calling the relevant idempotent function from ``apps/provisioning/api.py``.
*   Returning an instance of its ``output_class`` populated with the results.

``ProvisionNewCustomerWorkflow(AbstractWorkflow)``
--------------------------------------------------
The concrete workflow definition for provisioning a new customer:

* ``steps`` List: Defines the sequence of operations executed through the workflow.
* ``generate_input_dict()``: A classmethod used by the REST API layer to translate flat REST API
  request data into the nested dictionary structure expected by this workflow's
  dynamically generated ``input_class``. It maps API data to the respective step input ``KEY``.
* Output Accessors (e.g., ``customer_output_dict()``): Helper methods to retrieve specific parts
  of the workflow's total ``output_data`` using the ``KEY`` of the relevant step's output class.

REST API Layer (``apps/api/``)
==============================

This layer exposes provisioning functionality via HTTP, handling external contracts.

Serializers (``apps/api/serializers/provisioning.py``)
----------------------------------------------------------
These define data contracts for HTTP API (validation, deserialization of requests; serialization of responses).
``ProvisioningRequestSerializer`` (for requests) and ``ProvisioningResponseSerializer`` (for responses) nest
other specific entity serializers. These are all DRF Serializers.

Views (``apps/api/v1/views/provisioning.py``)
-------------------------------------------------

This view file defines HTTP endpoints, handles authentication/permissions,
and integrates the REST API-serialized data with workflow execution.

``ProvisioningCreateView`` handles ``POST`` requests for new customer provisioning. It uses
JWT authentication and permission checks.

The view works generally in this order:

1. HTTP Request: Client sends ``POST`` to ``/api/v1/provisioning/`` with JSON body.
2. ``ProvisioningCreateView``:

   a. Authenticates, checks permissions.
   b. Validates/deserializes request with ``ProvisioningRequestSerializer``.
3. Workflow Initiation:

   a. Calls ``ProvisionNewCustomerWorkflow.generate_input_dict()`` to prepare input.
   b. Creates ``ProvisionNewCustomerWorkflow`` DB record with this input.
4. Workflow Execution - ``ProvisionNewCustomerWorkflow.execute()``:

   a. This triggers the ``ProvisionNewCustomerWorkflow.process_input()``, which iterates through steps defined in ``ProvisionNewCustomerWorkflow.steps``. For each step (e.g., ``GetCreateCustomerStep``):

      i. A persistent model instance for the step is created/retrieved, linked to the workflow.
      ii. The step's ``execute()`` method is called. This, in turn, calls the step's
            specific ``process_input()`` (defined in ``apps/provisioning/models.py``).
      iii. The step's ``process_input()`` uses its specific input, data from previous steps
            (via ``accumulated_output``), and calls the relevant function from ``apps/provisioning/api.py``.
      iv. The result is returned as the step's output object.
      v. The step's output is added to the ``accumulated_output`` for the entire workflow.
5.  Response Generation:

    a. ``ProvisioningCreateView`` uses accessor methods on the completed ``workflow``
        instance (e.g., ``workflow.customer_output_dict()``) to get structured output.
    b. ``ProvisioningResponseSerializer`` formats this into JSON.
    c. HTTP ``201 Created`` response is sent.
6.  Error Handling:
    a. Exceptions during step execution are caught by ``AbstractUnitOfWork.execute()``,
    logged to the step record, and can be propagated. The view translates these into appropriate HTTP error responses.

Key Architectural Principles
****************************
*   **Layered Design**: API interface, workflow orchestration, core business logic,
    and data serialization are distinct.
*   **Model-Driven Workflows**: Workflows and steps are persistent Django models,
    allowing for state tracking, retries (potentially), and introspection.
*   **Generic Engine, Specific Implementations**: ``apps/workflow/models.py`` provides a reusable engine,
    while ``apps/provisioning/models.py`` provides a concrete application of this engine.
*   **Sequential Step Execution**: The ``AbstractWorkflow`` processes steps in the defined order,
    passing accumulated outputs.
*   **Idempotency**: Core functions in ``apps/provisioning/api.py`` are idempotent.
*   **Explicit Data Contracts**: ``attrs`` classes for I/O at step and workflow levels
    (via ``apps/workflow/serialization.py``) and DRF serializers at the API level ensure clarity.

Conclusion
****************************
The provisioning system leverages a generic, model-driven workflow engine to
orchestrate a sequence of persistent steps. Each step encapsulates a specific piece of business logic,
often calling idempotent functions that interact with external services. This architecture provides a
robust, traceable, and extensible way to manage complex multi-step processes like enterprise customer onboarding.
The clear separation of concerns between the generic workflow mechanics,
the specific provisioning workflow definition, the atomic business operations,
and the API layer makes the system maintainable and understandable.

Data Flow Diagram
****************************

This diagram illustrates the flow of data through the provisioning system. It shows how data is
transformed and handled across different application layers and external systems,
emphasizing data types and the idempotency of core operations.

.. code-block:: text

      +-------------------------------------------------+
      | External Client (UI / Service)                  |
      +-------------------------------------------------+
                         |
                         | HTTP Request (JSON)
                         V
      +-------------------------------------------------+
      | API Layer (apps/api/)                           |
      | - views/provisioning.py                         |
      | - serializers/provisioning.py (DRF Serializers) |
      |   - IN: JSON -> Validation & Deserialization    |
      |          to Python dicts                        |
      |   - Calls ProvisionNewCustomerWorkflow          |
      |     .generate_input_dict()                      |
      +-------------------------------------------------+
                         |
                         | Python Dict (for Workflow Input)
                         V
      +-----------------------------------------------------------------+
      | Workflow Orchestration (ProvisionNewCustomerWorkflow)           |
      | (apps/provisioning/models.py & apps/workflow/models.py)         |
      | - Receives dict, creates Workflow's `input_data`                |
      |   (`attrs` object, composed of step inputs)                     |
      | - Calls `workflow.execute()` -> AbstractWorkflow.process_input  |
      +-----------------------------------------------------------------+
                         |
                         | Iterates `ProvisionNewCustomerWorkflow.steps`:
                         | For each step:
                         |  1. Creates/gets Step Model instance
                         |  2. Passes step-specific input (`attrs` object)
                         |  3. Calls `step_record.execute()`
                         |  4. Aggregates step output (`attrs` object)
                         |     into workflow's `accumulated_output`
                         V
      +-----------------------------------------------------------------+
      | Workflow Step Execution (e.g., GetCreateCustomerStep)           |
      | (in apps/provisioning/models.py, inherits AbstractWorkflowStep) |
      |-----------------------------------------------------------------|
      | INPUT:                                                          |
      |  - Step-specific `attrs` object (e.g., GetCreateCustomerStepInput)|
      |  - `accumulated_output` from previous steps (`attrs` object)    |
      +-----------------------------------------------------------------+
                         |
                         | Python native arguments
                         | (from step input & accumulated_output)
                         V
      +-----------------------------------------------------------------+
      | Core Provisioning Logic Layer (apps/provisioning/api.py)        |
      |                                                                 |
      | ** IDEMPOTENT FUNCTIONS **                                      |
      | (e.g., get_or_create_enterprise_customer)                       |
      |-----------------------------------------------------------------|
      | INPUT: Python native arguments (e.g., name, slug, country)      |
      | PROCESSING: Interacts with API Clients                          |
      | OUTPUT: Python dictionary (result from API client)              |
      +-----------------------------------------------------------------+
                         |                                   /----------\
                         | API Client Calls                  | External |
                         +---------------------------------->| Systems  |
                         |                                   | (LMS,    |
                         | API Client Responses              | License  |
                         |<----------------------------------|  Mgr)    |
                         |                                   \----------/
                         | Python dictionary (result)
                         V
      +-----------------------------------------------------------------+
      | Workflow Step Execution (Continued)                             |
      |-----------------------------------------------------------------|
      | INPUT FROM CORE LOGIC: Python dictionary                        |
      | PROCESSING: Converts dict to step's output `attrs` object       |
      |             (e.g., GetCreateCustomerStepOutput.from_dict(result))|
      | OUTPUT: Step-specific `attrs` object                            |
      |         (e.g., GetCreateCustomerStepOutput)                     |
      +-----------------------------------------------------------------+
                         |
                         | Output (`attrs` object) back to
                         | Workflow Orchestration (`AbstractWorkflow.process_input`)
                         | to be added to `accumulated_output`
                         | (Loop for next step if any)
                         |
                         | When all steps complete:
                         | Workflow's `output_data` (`attrs` object,
                         | composed of all step outputs) is finalized.
                         V
      +-------------------------------------------------+
      | API Layer (apps/api/)                           |
      | (serializers/provisioning.py - DRF Serializers) |
      | - Receives Python Dicts (from Workflow          |
      |   Output Accessors e.g. workflow.customer_output_dict()) |
      | - OUT: Serialization to JSON                    |
      +-------------------------------------------------+
                         |
                         | HTTP Response (JSON)
                         V
      +-------------------------------------------------+
      | External Client (UI / Service)                  |
      +-------------------------------------------------+
