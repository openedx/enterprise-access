""" Abstract models and classes to support concrete workflows.. """

import collections
from uuid import uuid4

from attrs import asdict, define, field, make_class, Factory
from cattrs import structure, unstructure
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from jsonfield.encoder import JSONEncoder
from jsonfield.fields import JSONField
from model_utils.models import SoftDeletableModel, TimeStampedModel
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_update_with_history

from .exceptions import UnitOfWorkException


@define
class Empty:
    pass


class AbstractUnitOfWork(TimeStampedModel, SoftDeletableModel):
    """
    An abstract models that encapsulates the following:
    * input data
    * process_input() function to do the actual work
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
        raise NotImplementedError

    def execute(self, accumulated_output=None, **kwargs):
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
            result = {}
            raise self.exception_class from exc
        finally:
            self.save()
        return result

    def __str__(self):
        return str(self.uuid) + str(self.input_class) + str(self.output_class)


class AbstractWorkflowStep(AbstractUnitOfWork):
    """
    """
    class Meta:
        abstract = True

    _workflow_record = None
    _preceding_step = None

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
    """
    class Meta:
        abstract = True

    steps = []
    input_class = Empty
    output_class = Empty

    @property
    def input_object(self):
        return structure(self.input_data, self.input_class)

    def get_input_object_for_step_type(self, step_type):
        return getattr(self.input_object, step_type.input_class.KEY, None)

    def process_input(self, **kwargs):
        if self.succeeded_at:
            return

        accumulated_output = self.output_class()

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
