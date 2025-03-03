""" Abstract models and classes to support concrete workflows.. """

from uuid import uuid4

from attrs import asdict, define
from cattrs import structure
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from jsonfield.fields import JSONField
from model_utils.models import SoftDeletableModel, TimeStampedModel

from .exceptions import UnitOfWorkException


@define
class Empty:
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
        return self.input_class(**self.input_data)

    @property
    def output_object(self):
        return self.output_class(**self.output_data)

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Should be implemented to do some operation on ``self.input_object``
        and return a resulting instance of ``self.output_object``.

        Params:
          accumulated_output (obj): An optional accumulator object to which
            the resulting output can be added.

        Returns:
          An instance of ``self.output_class``.
        """
        raise NotImplementedError

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
            self.output_data = asdict(result)
            self.succeeded_at = timezone.now()
        except Exception as exc:
            self.failed_at = timezone.now()
            self.exception_message = str(exc)
            result = self.output_class()
            raise self.exception_class from exc
        finally:
            self.save()
        return result

    def __str__(self):
        return str(self.uuid) + str(self.input_class) + str(self.output_class)


class AbstractWorkflowStep(AbstractUnitOfWork):
    """
    An abstract step of a workflow.  Concrete implementations should
    define the following concrete FK fields:
    * ``workflow_record`` - a non-null FK to the concrete Workflow model.
    * ``preceding_step`` - (more optional) a nullable FK to the same concrete Workflow Step model.
    """
    class Meta:
        abstract = True

    _workflow_record = None
    _preceding_step = None

    def process_input(self, accumulated_output=None, **kwargs):
        raise NotImplementedError

    @property
    def workflow(self):
        """
        Concrete implementations should define a FK field to
        the concrete workflow model, named ``workflow_record``.
        """
        return self._workflow_record

    @property
    def preceding_step(self):
        """
        Concrete implementations can define a FK field to
        the preceding, concrete step model, called ``preceding_step``.
        If this step is the first, it won't have a preceding step.
        """
        return getattr(self, '_preceding_step', None)


class AbstractWorkflow(AbstractUnitOfWork):
    """
    An abstract workflow model.
    """
    class Meta:
        abstract = True

    steps = []

    @property
    def input_object(self):
        return structure(self.input_data, self.input_class)

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
        for WorkflowStep in self.steps:
            input_object = self.get_input_object_for_step_type(WorkflowStep)
            step_record_kwargs = {
                'workflow_record': self,
                'defaults': {
                    'input_data': asdict(input_object),
                }
            }
            if preceding_step_record:
                step_record_kwargs['preceding_step'] = preceding_step_record

            step_record, _ = WorkflowStep.objects.get_or_create(**step_record_kwargs)
            preceding_step_record = step_record
            if step_record.succeeded_at:
                setattr(
                    accumulated_output,
                    WorkflowStep.output_class.KEY,
                    step_record.output_object,
                )
                continue

            step_output = step_record.execute(accumulated_output=accumulated_output)
            setattr(
                accumulated_output,
                WorkflowStep.output_class.KEY,
                step_output,
            )

        return accumulated_output
