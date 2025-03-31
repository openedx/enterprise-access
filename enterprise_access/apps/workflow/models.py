""" Abstract models and classes to support concrete workflows.. """

from uuid import uuid4

from attrs import define, field, make_class
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from jsonfield.fields import JSONField
from model_utils.models import SoftDeletableModel, TimeStampedModel

from .exceptions import UnitOfWorkException
from .serialization import BaseInputOutput


@define
class Empty(BaseInputOutput):
    pass


class AbstractUnitOfWork(TimeStampedModel, SoftDeletableModel):
    """
    An abstract model that encapsulates the following:
    * input data
    * ``process_input()`` function to do the actual work
    * output data

    .. no_pii: This model has no PII
    """

    class Meta:
        abstract = True

    input_class = Empty
    output_class = Empty
    exception_class = UnitOfWorkException

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    input_data = JSONField(
        blank=True,
        null=False,
        default=None,
    )
    output_data = JSONField(
        blank=True,
        null=True,
        default=None,
    )
    succeeded_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    failed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    exception_message = models.TextField(
        null=True,
        blank=True,
    )

    @property
    def input_object(self):
        return self.input_class.from_dict(self.input_data)

    @property
    def output_object(self):
        return self.output_class.from_dict(self.output_data)

    def process_input(self, accumulated_output=None, **kwargs):  # pylint: disable=unused-argument
        """
        Should be implemented to do some operation on ``self.input_object``
        and return a resulting instance of ``self.output_object``.

        Params:
          accumulated_output (obj): An optional accumulator object to which
            the resulting output can be added.

        Returns:
          An instance of ``self.output_class``.
        """
        return self.output_object

    def execute(self, accumulated_output=None, **kwargs):
        """
        Executes this unit of work via ``self.process_input()``,
        then stores the output (as a dictionary for json serialization)
        and time of successful execution.
        On any exception, the exception time and message are stored,
        and an empty output object is ultimately returned.

        Params:
          accumulated_output (obj): An optional accumulator object, which will be
            passed along to ``process_input()``, which should be implemented
            in a way that adds the successful output to the accumulator.

        Returns:
          An instance of ``self.output_class``.
        """
        try:
            result = self.process_input(
                accumulated_output=accumulated_output,
                **kwargs,
            )
            self.output_data = result.to_dict()
            self.succeeded_at = timezone.now()
        except Exception as exc:
            self.failed_at = timezone.now()
            self.exception_message = str(exc)
            result = self.output_class()
            raise self.exception_class(str(exc)) from exc
        finally:
            self.save()
        return result

    def __str__(self):
        return str(self.uuid) + str(self.input_class) + str(self.output_class)


class AbstractWorkflowStep(AbstractUnitOfWork):
    """
    An abstract step of a workflow. The workflow_record_identifier and
    preceding_step_identifier help to maintain linkages between steps and within workflows.
    However, since we want workflows to be composable and modular, we're required
    to allow any workflow step type to be included in the list for one *or more*
    workflow types. So these can't be strict foreign keys.
    """
    class Meta:
        abstract = True

    workflow_record_uuid = models.UUIDField(
        null=False,
        help_text='UUID of the workflow record',
    )
    preceding_step_uuid = models.UUIDField(
        null=True,
        help_text='UUID of the preceding workflow step record, if any',
    )


class AbstractWorkflow(AbstractUnitOfWork):
    """
    An abstract workflow model.
    """
    class Meta:
        abstract = True

    steps = []

    @cached_property
    def input_class(self):
        """
        For constructing workflow input/output classes, we use the attrs.make_class() helper
        to dynamically create a class with fields corresponding to the ``KEY`` fields of the *step*
        input/output classes. This helps maintain some semblance of a rigid interface
        at the boundaries of a given workflow and the steps of which the workflow is composed.
        """
        class_name = self.__class__.__name__ + 'Input'
        attributes = {
            step_class.input_class.KEY: field(type=step_class.input_class, default=None)
            for step_class in self.steps
        }
        return make_class(class_name, attributes, bases=(BaseInputOutput,))

    @cached_property
    def output_class(self):
        """
        For constructing workflow input/output classes, we use the attrs.make_class() helper
        to dynamically create a class with fields corresponding to the ``KEY`` fields of the *step*
        input/output classes. This helps maintain some semblance of a rigid interface
        at the boundaries of a given workflow and the steps of which the workflow is composed.
        """
        class_name = self.__class__.__name__ + 'Output'
        attributes = {
            step_class.output_class.KEY: field(type=step_class.output_class, default=None)
            for step_class in self.steps
        }
        return make_class(class_name, attributes, bases=(BaseInputOutput,))

    def get_input_object_for_step_type(self, step_type):
        return getattr(self.input_object, step_type.input_class.KEY, None)

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Processes the input for an entire workflow, which consists of:
        1. Get/creating a step record for each step of the workflow.
        2. Calling ``execute()`` on each of these steps (unless they've already succeeded).
        3. On success, accumulating the step output and
        passing it along to the next step's ``process_input()`` call.

        Returns:
          An instance of ``self.output_class``, which should just be an accumulation
          of the output of each step in this workflow.
        """
        if self.succeeded_at:
            return None

        accumulated_output = accumulated_output or self.output_class()

        preceding_step_record = None
        for workflow_step_class in self.steps:
            input_object = self.get_input_object_for_step_type(workflow_step_class)
            input_data = input_object.to_dict() if input_object else {}
            step_record_kwargs = {
                'workflow_record_uuid': self.uuid,
                'defaults': {
                    'input_data': input_data,
                }
            }
            if preceding_step_record:
                step_record_kwargs['defaults']['preceding_step_uuid'] = preceding_step_record.uuid

            step_record, _ = workflow_step_class.objects.get_or_create(**step_record_kwargs)
            preceding_step_record = step_record
            if step_record.succeeded_at:
                setattr(
                    accumulated_output,
                    workflow_step_class.output_class.KEY,
                    step_record.output_object,
                )
                continue

            step_output = step_record.execute(accumulated_output=accumulated_output)
            setattr(
                accumulated_output,
                workflow_step_class.output_class.KEY,
                step_output,
            )

        return accumulated_output
