"""
Models with concrete implementations of abstract
workflow models, for unit-testing.
"""

from attrs import define, field, make_class
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

    result: int = None


@define
class TestSquareInput:
    KEY = 'test_square_input'

    argument_1: int = 0


@define
class TestSquareOutput:
    KEY = 'test_square_output'

    result: int = None


# For constructing workflow input/output classes,
# we use the attrs.make_class() helper
# to dynamically create a class with fields
# corresponding to the ``KEY`` fields of the *step*
# input/output classes. This helps maintain some semblance
# of a rigid interface at the boundaries of workflows and
# the steps that comprise them.
# The ``make_class()`` call below will result in a class
# equivalent to:
#
# class TestWorkflowInput:
#     test_step_input: TestStepInput

TestWorkflowInput = make_class(
    'TestWorkflowInput',
    {
        TestStepInput.KEY: field(type=TestStepInput),
    },
)


# The ``make_class()`` call below will result in a class
# equivalent to:
#
# class TestWorkflowOutput:
#     test_step_output: TestStepOutput

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
    Concretely, it does (x + y).
    """
    input_class = TestWorkflowInput
    output_class = TestWorkflowOutput

    steps = [
        TestWorkflowStep,
    ]


class TestSquaredWorkflowStep(AbstractWorkflowStep):
    """
    Concrete test workflow step that multiplies a number by itself.
    """
    input_class = TestSquareInput
    output_class = TestSquareOutput

    def process_input(self, accumulated_output=None, **kwargs):
        if accumulated_output:
            operand = accumulated_output.test_step_output.result
        else:
            operand = self.input_object.argument_1

        return self.output_class(result=operand ** 2)


TestTwoStepWorkflowInput = make_class(
    'TestTwoStepWorkflowInput',
    {
        TestStepInput.KEY: field(type=TestStepInput),
    },
)


TestTwoStepWorkflowOutput = make_class(
    'TestTwoStepWorkflowOutput',
    {
        TestStepOutput.KEY: field(type=TestStepOutput, default=None),
        TestSquareOutput.KEY: field(type=TestSquareOutput, default=None),
    },
)


class TestTwoStepWorkflow(AbstractWorkflow):
    """
    Concrete implementation of a two-step workflow for unit-testing.
    Concretely, it does (x + y) ** 2.
    """
    input_class = TestTwoStepWorkflowInput
    output_class = TestTwoStepWorkflowOutput

    steps = [
        TestWorkflowStep,
        TestSquaredWorkflowStep,
    ]
