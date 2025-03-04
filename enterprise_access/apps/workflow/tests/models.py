"""
Models with concrete implementations of abstract
workflow models, for unit-testing.
"""

from attrs import asdict, define, field, make_class, Factory
from django.db import models

from ..models import AbstractWorkflow, AbstractWorkflowStep


@define
class TestStepInput:
    KEY = 'test_step_input'

    argument_1: int = 0
    argument_2: int = 0


@define
class TestStepOutput:
    KEY = 'test_step_output'

    result: int = 0


TestWorkflowInput = make_class(
    'TestWorkflowInput',
    {
        TestStepInput.KEY: field(type=TestStepInput),
    },
)


TestWorkflowOutput = make_class(
    'TestWorkflowOutput',
    {
        TestStepOutput.KEY: field(type=TestStepOutput, default=None),
    },
)


class TestWorkflowStep(AbstractWorkflowStep):
    """
    Concrete implementation of a workflow step for unit-testing.
    """
    input_class = TestStepInput
    output_class = TestStepOutput

    workflow_record = models.ForeignKey(
        'TestWorkflow',
        null=True,
        on_delete=models.CASCADE,
    )

    def process_input(self, accumulated_output=None, **kwargs):
        return self.output_class(
            result=self.input_object.argument_1 + self.input_object.argument_2
        )


class TestWorkflow(AbstractWorkflow):
    """
    Concrete implementation of a workflow for unit-testing.
    """
    input_class = TestWorkflowInput
    output_class = TestWorkflowOutput

    steps = [
        TestWorkflowStep,
    ]
