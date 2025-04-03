"""
Models with concrete implementations of abstract
workflow models, for unit-testing.
"""

from attrs import define

from ..models import AbstractWorkflow, AbstractWorkflowStep
from ..serialization import BaseInputOutput


@define
class TestStepInput(BaseInputOutput):
    KEY = 'test_step_input'

    argument_1: int = 0
    argument_2: int = 0


@define
class TestStepOutput(BaseInputOutput):
    KEY = 'test_step_output'

    result: int = None


@define
class TestSquareInput(BaseInputOutput):
    KEY = 'test_square_input'

    argument_1: int = 0


@define
class TestSquareOutput(BaseInputOutput):
    KEY = 'test_square_output'

    result: int = None


class TestWorkflowStep(AbstractWorkflowStep):
    """
    Concrete implementation of a workflow step for unit-testing.
    """
    input_class = TestStepInput
    output_class = TestStepOutput

    def process_input(self, accumulated_output=None, **kwargs):
        return self.output_class(
            result=self.input_object.argument_1 + self.input_object.argument_2
        )


class TestWorkflow(AbstractWorkflow):
    """
    Concrete implementation of a workflow for unit-testing.
    Concretely, it does (x + y).
    """
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


class TestTwoStepWorkflow(AbstractWorkflow):
    """
    Concrete implementation of a two-step workflow for unit-testing.
    Concretely, it does (x + y) ** 2.
    """
    steps = [
        TestWorkflowStep,
        TestSquaredWorkflowStep,
    ]
