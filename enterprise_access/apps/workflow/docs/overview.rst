*****************
Workflow Overview
*****************

:Author: iloveagent57
:Date: 2025-05-19
:Version: 1.1
:Generated-At: 2025-05-19 18:16:29 UTC

.. contents:: Table of Contents

Introduction
============

This document provides an architectural overview of the workflow pattern
located within ``enterprise_access/apps/workflow/``.
This pattern provides a reusable and extensible framework for defining, executing, and managing
multi-step processes as persistent Django models. It is designed to be domain-agnostic,
allowing for the creation of various types of workflows beyond the specific provisioning examples
found elsewhere in the repository.

Core Components of the Generic Workflow Pattern
-----------------------------------------------

The pattern is built upon a few key abstract models and serialization helpers.

Workflow Data Serialization (``apps/workflow/serialization.py``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This module is fundamental to how data is structured and managed within the workflow pattern.
It defines the mechanisms for creating structured data classes for workflow and step inputs and outputs,
ensuring data consistency and facilitating serialization to/from formats suitable for database storage
(typically JSON).

Key Mechanisms
^^^^^^^^^^^^^^

``attrs`` and ``cattrs``
  These libraries are heavily utilized. ``attrs`` is used to define Python classes with attributes, validators,
  and default boilerplate (e.g., ``__init__``, ``__repr__``) with minimal code.
  For our workflow app, these classes represent the specific input or output data structures
  for units of work. ``cattrs`` is used for structuring (converting dictionaries, often from JSON,
  into ``attrs`` instances) and unstructuring (converting ``attrs`` instances back into dictionaries).

``BaseInputOutput``
  This class serves as a base for all specific input and output data classes.
  It provides common functionality, such as ``from_dict()`` and ``to_dict()`` methods,
  leveraging ``cattrs`` for conversion.

Static ``KEY`` Attribute
  Concrete input/output classes (defined by specific workflow implementations)
  should have a static ``KEY`` string attribute. This key is used by the ``AbstractWorkflow`` model
  to dynamically construct its composite input/output classes
  and to correctly map data between the overall workflow and its individual steps.

Validation
  ``attrs`` validators are used to ensure data integrity
  for the fields within these input/output classes.

Abstract Unit of Work (``apps/workflow/models.py:AbstractUnitOfWork``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the most fundamental building block of the workflow pattern, representing a persistent set of
work that can be executed. Both individual workflow steps and entire workflows are types of "units of work."

Key Django Model Attributes
^^^^^^^^^^^^^^^^^^^^^^^^^^^

``uuid``
  Primary key, a unique identifier for this unit of work instance.
``input_data`` (``JSONField``)
  Stores the input required for this unit, serialized as JSON.
``output_data`` (``JSONField``)
  Stores the result after execution, serialized as JSON.
``created``, ``modified`` (from ``TimeStampedModel``)
  Timestamps for creation and last modification.
``is_removed`` (from ``SoftDeletableModel``)
  For soft deletion.
``succeeded_at``, ``failed_at``
  Timestamps indicating successful or failed execution.
``exception_message``
  Stores the error message if execution failed.

Attributes and Methods to be defined by subclasses
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``input_class``
  Specifies the ``attrs``-based class (inheriting from ``BaseInputOutput``)
  used to structure and validate the ``input_data``. Defaults to an empty class.
``output_class``
  Specifies the ``attrs``-based class for the ``output_data``.
  Defaults to an empty class.
``exception_class``
  The specific exception type to be raised by the ``execute`` method upon failure.
  Defaults to ``UnitOfWorkException``.
``process_input(self, accumulated_output=None, **kwargs)``
  This **abstract method must be implemented by concrete subclasses**
  (like specific workflow steps or workflow types). It contains the actual logic for performing the work.
  It receives the deserialized ``self.input_object`` and an optional ``accumulated_output``
  object from previous operations, which steps within a workflow may refer to for outputs of
  prior steps. It should return an instance of ``self.output_class``.

Abstract Workflow Step (``apps/workflow/models.py:AbstractWorkflowStep``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This model represents a single, distinct step within a larger, sequential workflow. It inherits
from ``AbstractUnitOfWork``, gaining all its properties for input, output, execution, and persistence.
Its role is to encapsulate one part of a multi-stage workflow process, where each step is
individually executable and records its own success or failure.

Key Django Model Attributes (additional to ``AbstractUnitOfWork``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``workflow_record_uuid`` (``UUIDField``)
  Stores the UUID of the parent ``AbstractWorkflow`` instance
  to which this step belongs. This links the step to its specific workflow run.
``preceding_step_uuid`` (``UUIDField``, nullable)
  Stores the UUID of the ``AbstractWorkflowStep`` instance
  that comes immediately before this one in the sequence. This helps clarify the order of steps.

Usage
-----
Concrete step classes (e.g., ``GetCreateCustomerStep`` in the provisioning context)
should inherit from ``AbstractWorkflowStep`` and implement the ``process_input()`` method
to define their specific action.

Abstract Workflow (``apps/workflow/models.py:AbstractWorkflow``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This model represents the entire workflow, orchestrating a sequence of ``AbstractWorkflowStep`` instances.
It inherits from ``AbstractUnitOfWork``; an entire workflow is itself a unit of work with an overall input,
an aggregated output, and an execution lifecycle. Its primary role is to define and manage
a sequence of operations (steps) that achieve a larger business goal.

Key Class Attributes (to be defined by subclasses)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``steps = []``
  This **crucial definition** is a list that must be defined by concrete workflow
  subclasses (e.g., ``ProvisionNewCustomerWorkflow.steps = [StepA, StepB, StepC]``).
  It contains the *Python classes* of the concrete steps (which inherit from ``AbstractWorkflowStep``)
  in the precise order they should be executed.

Dynamically Generated I/O Classes
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``input_class`` (``@cached_property``)
  The input class for the entire workflow is dynamically generated
  using ``attrs.make_class()``. Its fields correspond to the ``KEY`` attributes of the ``input_class``
  of each step defined in ``self.steps``. This creates a composite input object where each attribute
  holds the specific input for one of its constituent steps.
``output_class`` (``@cached_property``)
  Similarly, the output class for the workflow is dynamically generated.
  Its fields correspond to the ``KEY`` attributes of the ``output_class`` of each step.
  This creates a composite output object that aggregates the results from all individual steps.

Key Methods
^^^^^^^^^^^

``get_input_object_for_step_type(self, step_type)``
  A helper to retrieve the specific input
  portion for a given step class from the workflow's overall ``input_object``.
``process_input(self, accumulated_output=None, **kwargs)``
  This method provides the **core orchestration logic**. It overrides the one from ``AbstractUnitOfWork``
  and implements the logic for running the entire sequence of steps.
  It iterates through the ``workflow_step_class`` list defined in ``self.steps``.
  For each ``workflow_step_class``:

  1.  It retrieves or creates a persistent model instance for that specific step
      (e.g., an instance of ``StepA``), linking it to the current workflow's UUID (``self.uuid``)
      and the UUID of the ``preceding_step_record`` (if any).
  2.  The input for this step instance is populated from the workflow's main ``input_object``
      (using ``get_input_object_for_step_type``).
  3.  If the step instance has not already succeeded, its ``execute()`` method is called.
      The ``accumulated_output`` (an instance of the workflow's ``output_class``,
      containing results from prior steps) is passed to the step's ``execute()`` method,
      making prior results available.
  4.  The output from the current step (an ``attrs`` object) is then set as an attribute
      on the ``accumulated_output`` object, using the current step's
      ``output_class.KEY`` as the attribute name.

  After all steps have been processed, it returns the ``accumulated_output`` object,
  which now contains the aggregated results from all successfully executed steps.

Example Workflow Execution Flow
===============================

Workflow Definition
-------------------
A concrete workflow class (e.g., ``MyCustomWorkflow``) is created by inheriting
from ``AbstractWorkflow`` and defining its ``steps`` list (e.g., ``steps = [MyStep1, MyStep2]``).

Step Definitions
----------------
Concrete step classes (``MyStep1``, ``MyStep2``) are created
by inheriting from ``AbstractWorkflowStep``. Each defines its ``input_class`` and ``output_class``
(these are ``attrs`` classes with ``KEY`` fields) and its specific logic within its ``process_input()`` method.

Initiation
----------
An instance of ``MyCustomWorkflow`` is created,
typically with ``input_data`` that conforms to its dynamically generated
``input_class`` (i.e., a structure containing inputs for ``MyStep1`` and ``MyStep2``, keyed appropriately).
The ``my_workflow_instance.execute()`` method is then called.

Orchestration
-------------
The ``MyCustomWorkflow.process_input`` method (via ``AbstractWorkflow``) orchestrates the flow.
The workflow execution iterates through its defined steps, for example ``[MyStep1, MyStep2]``.

For ``MyStep1``, a ``MyStep1`` model instance is created/fetched and linked to ``my_workflow_instance``.
Its specific input is extracted from ``my_workflow_instance.input_object``.
``my_step1_instance.execute()`` is called, which in turn runs ``MyStep1.process_input()``.
The output of ``MyStep1`` is stored in ``my_step1_instance.output_data``
and also added to the ``accumulated_output`` object of the main workflow.

For ``MyStep2``, a ``MyStep2`` model instance is created/fetched, linked to ``my_workflow_instance`` and ``my_step1_instance``.
Its specific input is extracted. ``my_step2_instance.execute(accumulated_output=...)`` is called.
The ``MyStep2.process_input()`` method can now access the output of ``MyStep1`` from the ``accumulated_output`` object.
The output of ``MyStep2`` is stored and added to ``accumulated_output``.

Completion
----------
Once all steps are processed, ``my_workflow_instance.output_data``
will contain the aggregated results from all steps.
The ``succeeded_at`` field of ``my_workflow_instance`` is updated accordingly.

Benefits of this Generic Pattern
================================

The generic workflow pattern offers several advantages:

Reusability
  The abstract models provide a common structure applicable to any multi-step process.
Persistence
  Workflow and step states, inputs, and outputs are saved to the database. This allows for
  auditability and, potentially, for resuming failed workflows (though resume logic isn't
  explicitly detailed in the abstract models themselves, the persisted state enables such functionality).
Modularity
  Each step is a distinct unit of work, which makes workflows easier to build, test, and maintain.
Clarity of Data Flow
  The use of ``attrs`` classes for inputs and outputs at each stage,
  along with the ``accumulated_output`` mechanism, provides a clear contract for how data moves through the workflow.
Extensibility
  New types of workflows and steps can be easily added
  by inheriting from the abstract base classes.
Domain Agnostic
  While utilized for provisioning tasks in ``enterprise-access``,
  the pattern itself is not tied to provisioning and can be readily adapted for other business processes.

Conclusion
----------

The generic workflow pattern in ``openedx/enterprise-access`` offers a powerful and flexible foundation
for orchestrating complex, multi-step operations. By combining abstract Django models with structured data
classes defined using ``attrs``, it provides a robust system for defining, executing,
and tracking the state of various automated processes in a persistent and modular way.
