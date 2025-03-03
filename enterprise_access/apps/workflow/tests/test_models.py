"""
Unit tests for the test implementations
of the abstract workflow models.
"""

from django.test import TestCase

from .models import TestStepInput, TestWorkflow


class TestWorkflowModels(TestCase):
    """
    Unit tests for the test implementations
    of the abstract workflow models.
    """
    def test_simple_workflow(self):
        input_data = {
            TestStepInput.KEY: {
                'argument_1': 2,
                'argument_2': 3,
            },
        }
        workflow = TestWorkflow.objects.create(
            input_data=input_data,
        )
        output_record = workflow.execute()
        self.assertEqual(output_record.test_step_output.result, 5)
