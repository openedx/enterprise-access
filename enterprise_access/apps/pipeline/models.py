""" Models to support pipelines/workflows.. """

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
    )
    succeeded_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    failed_at = models.DateTimeField(
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
        except:
            self.failed_at = timezone.now()
            result = {}

        self.save()
        return result

    def __str__(self):
        return str(self.uuid) + str(self.input_class) + str(self.output_class)


class AbstractPipeline(AbstractUnitOfWork):
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
        for PipelineStep in self.steps:
            input_object = self.get_input_object_for_step_type(PipelineStep)
            step_record_kwargs = {
                'pipeline_record': self,
                'defaults': {
                    'input_data': asdict(input_object),
                }
            }
            if preceding_step_record:
                step_record_kwargs['preceding_step'] = preceding_step_record

            step_record, _ = PipelineStep.objects.get_or_create(**step_record_kwargs)
            preceding_step_record = step_record
            if step_record.succeeded_at:
                setattr(
                    accumulated_output,
                    PipelineStep.output_class.KEY,
                    step_record.output_object,
                )
                continue

            step_output = step_record.execute(accumulated_output=accumulated_output)
            setattr(
                accumulated_output,
                PipelineStep.output_class.KEY,
                step_output,
            )

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


@define
class StretchDoughInput:
    KEY = 'stretch_dough_input'

    crust_style: str


@define
class StretchDoughOutput:
    KEY = 'stretch_dough_output'

    is_saucy: bool = False


class StretchDoughStep(PizzaPipelineStep):
    input_class = StretchDoughInput
    output_class = StretchDoughOutput

    def process_input(self, accumulated_output=None, **kwargs):
        print('Stretching dough...')
        print(self.input_object)

        output_object = self.output_class(
            is_saucy=(crust_style != 'thin'),
        )
        return asdict(output_object)


@define
class ToppingsInput:
    KEY = 'toppings_input'

    whole_pie: list[str] = Factory(list)
    left_half_pie: list[str] = Factory(list)
    right_half_pie: list[str] = Factory(list)


@define
class ToppingsOutput:
    KEY = 'toppings_output'

    is_awesome: bool = False


class AddToppingsStep(PizzaPipelineStep):
    input_class = ToppingsInput
    output_class = ToppingsOutput

    preceding_step = models.ForeignKey(
        StretchDoughStep,
        on_delete=models.CASCADE,
    )

    def process_input(self, accumulated_output=None, **kwargs):
        """
        """
        print('Adding toppings to whole pizza...')
        print(self.input_object.whole_pie)

        print('Adding toppings to left half of pizza...')
        print(self.input_object.left_half_pie)

        print('Adding toppings to right half of pizza...')
        print(self.input_object.right_half_pie)

        output_object = self.output_class(
            is_awesome=True,
        )
        return output_object


@define
class BakeInput:
    KEY = 'bake_input'

    doneness: str = 'regular'


@define
class BakeOutput:
    KEY = 'bake_output'

    structural_integrity: str = 'good'


class BakePizzaStep(PizzaPipelineStep):
    input_class = BakeInput
    output_class = BakeOutput

    preceding_step = models.ForeignKey(
        AddToppingsStep,
        on_delete=models.CASCADE,
    )

    def _are_toppings_awesome(self, accumulated_output):
        if not accumulated_output:
            return False

        toppings_output = getattr(accumulated_output, ToppingsOutput.KEY, None)
        if not toppings_output:
            return False

        return toppings_output.is_awesome

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Depends on output from prior step.
        """
        print('Baking...')
        print(self.input_object.doneness)

        if self._are_toppings_awesome(accumulated_output):
            print('Baking extra good because the toppings were awesome!')

        output_object = self.output_class(structural_integrity='excellent')
        return output_object



PizzaPipelineInput = make_class(
    'PizzaPipelineInput',
    {
        StretchDoughInput.KEY: field(type=StretchDoughInput),
        ToppingsInput.KEY: field(type=ToppingsInput),
        BakeInput.KEY: field(type=BakeInput),
    },
)


PizzaPipelineOutput = make_class(
    'PizzaPipelineOutput',
    {
        StretchDoughOutput.KEY: field(type=StretchDoughOutput, default=None),
        ToppingsOutput.KEY: field(type=ToppingsOutput, default=None),
        BakeOutput.KEY: field(type=BakeOutput, default=None),
    },
)


class PizzaPipeline(AbstractPipeline):
    """
    Concrete pipeline to bake a pizza.
    """
    steps = [
        StretchDoughStep,
        AddToppingsStep,
        BakePizzaStep,
    ]
    input_class = PizzaPipelineInput
    output_class = PizzaPipelineOutput

    @classmethod
    def run_test(cls):
        test_input = {
            StretchDoughInput.KEY: {
                'crust_style': 'thin',
            },
            ToppingsInput.KEY: {
                'whole_pie': ['bacon'],
                'left_half_pie': ['pineapple'],
            },
            BakeInput.KEY: {
                'doneness': 'well_done',
            }
        }
        pipeline = cls.objects.create(input_data=test_input)
        pipeline.execute()
        return pipeline
