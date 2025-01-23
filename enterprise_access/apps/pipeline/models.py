""" Models to support pipelines/workflows.. """

import collections
from uuid import uuid4

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

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    input_data = JSONField(
        blank=True,
        null=False,
    )
    output_data = JSONField(
        blank=True,
        null=True,
        editable=False,
    )
    succeeded_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    failed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    def process_input(self, **kwargs):
        raise NotImplementedError

    def execute(self, **kwargs):
        try:
            result = self.process_input(**kwargs)
            self.output_data = result
            self.succeeded_at = timezone.now()
        except:
            self.failed_at = timezone.now()
            result = {}

        self.save()
        return result


class AbstractPipeline(AbstractUnitOfWork):
    """
    """
    class Meta:
        abstract = True

    steps = []

    def get_input_data_for_step_type(self, step_type):
        raise NotImplementedError

    def process_input(self, **kwargs):
        if self.succeeded_at:
            return

        accumulated_output = {}

        preceding_step_record = None
        for PipelineStep in self.steps:
            input_data = self.get_input_data_for_step_type(PipelineStep)
            kwargs = {
                'pipeline_record': self,
                'defaults': {
                    'input_data': input_data,
                }
            }
            if preceding_step_record:
                kwargs['preceding_step'] = preceding_step_record

            step_record, _ = PipelineStep.objects.get_or_create(**kwargs)
            preceding_step_record = step_record
            if step_record.succeeded_at:
                accumulated_output.update(step_record.output_data)
                continue

            step_output = step_record.execute(**accumulated_output)
            accumulated_output.update(step_output)

        return accumulated_output


class AbstractPipelineStep(AbstractUnitOfWork):
    """
    """
    class Meta:
        abstract = True

    @property
    def pipeline(self):
        """
        Concrete implementations should define a FK field to
        the concrete pipeline model, named `pipeline_record`.
        """
        return self.pipeline_record

    @property
    def preceding_step(self):
        """
        Concrete implementations can define a FK field to
        the preceding, concrete step model, called `preceding_step`.
        If this step is the first, it won't have a preceding step.
        """
        return getattr(self, 'preceding_step', None)


class PizzaPipelineStep(AbstractPipelineStep):
    class Meta:
        abstract = True

    pipeline_record = models.ForeignKey(
        'PizzaPipeline',
        null=False,
        on_delete=models.CASCADE,
    )


class StretchDoughStep(PizzaPipelineStep):
    def process_input(self, **kwargs):
        print('Stretching dough...')
        crust_style = self.input_data.get('crust_style')
        print(crust_style)

        result = {**self.input_data}
        result.update(kwargs)
        result['is_saucy'] = crust_style != 'thin'
        return result


class AddToppingsStep(PizzaPipelineStep):
    preceding_step = models.ForeignKey(
        StretchDoughStep,
        on_delete=models.CASCADE,
    )

    def process_input(self, **kwargs):
        """
        Depends on output from prior step.
        """
        print('Adding toppings to whole pizza...')
        print(self.input_data.get('whole_pie'))

        print('Adding toppings to half pizza...')
        print(self.input_data.get('half_pie'))

        print('Sauciness:')
        print(kwargs.get('is_saucy'))

        result = {**self.input_data}
        result.update(kwargs)
        return result


class BakePizzaStep(PizzaPipelineStep):
    preceding_step = models.ForeignKey(
        AddToppingsStep,
        on_delete=models.CASCADE,
    )

    def process_input(self, **kwargs):
        """
        Depends on output from prior step.
        """
        print('Baking...')
        doneness = self.input_data.get('doneness')
        print(doneness)

        result = {**self.input_data}
        result.update(kwargs)
        return result

class PizzaPipeline(AbstractPipeline):
    """
    Concrete pipeline to bake a pizza.
    """
    steps = [
        StretchDoughStep,
        AddToppingsStep,
        BakePizzaStep,
    ]

    def get_input_data_for_step_type(self, step_type):
        if step_type == StretchDoughStep:
            return self.input_data.get('stretch_dough')
        elif step_type == AddToppingsStep:
            return self.input_data.get('toppings')
        elif step_type == BakePizzaStep:
            return self.input_data.get('bake')

    @classmethod
    def test_input(cls):
        return {
            'stretch_dough': {
                'crust_style': 'thin',
            },
            'toppings': {
                'whole_pie': ['bacon'],
                'half_pie': ['pineapple'],
            },
            'bake': {
                'doneness': 'well_done',
            }
        }
