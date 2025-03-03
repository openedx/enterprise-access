"""
Unit tests for the test implementations
of the abstract workflow models.
"""
from unittest import mock

from django.test import TestCase

from ..exceptions import UnitOfWorkException
from .models import TestStepInput, TestTwoStepWorkflow, TestWorkflow, TestWorkflowStep


class TestWorkflowModels(TestCase):
    """
    Unit tests for the test implementations
    of the abstract workflow models.
    """
    INPUT_DATA = {
        TestStepInput.KEY: {
            'argument_1': 2,
            'argument_2': 3,
        },
    }

    def test_simple_workflow(self):
        """
        Tests that we can execute a simple workflow consisting of one step that adds two numbers.
        """
        workflow = TestWorkflow.objects.create(
            input_data=self.INPUT_DATA,
        )
        output_record = workflow.execute()
        self.assertEqual(output_record.test_step_output.result, 5)

    def test_workflow_error(self):
        """
        Tests that exception handling and propagation within a workflow works
        as expected.
        """
        test_exception = Exception('this step failed')
        with mock.patch.object(TestWorkflowStep, 'process_input', side_effect=test_exception):
            workflow = TestWorkflow.objects.create(
                input_data=self.INPUT_DATA,
            )
            with self.assertRaises(UnitOfWorkException):
                output_record = workflow.execute()
                self.assertIsNone(output_record.test_step_output.result)

            step = TestWorkflowStep.objects.filter(workflow_record_uuid=workflow.uuid).first()
            self.assertIsNotNone(step.failed_at)
            self.assertEqual(step.exception_message, str(test_exception))

            self.assertIsNotNone(workflow.failed_at)
            self.assertEqual(workflow.exception_message, str(test_exception))

    def test_two_step_workflow(self):
        """
        Tests that we can execute a two-step workflow that adds two numbers and then squares them.
        """
        workflow = TestTwoStepWorkflow.objects.create(
            input_data=self.INPUT_DATA,
        )
        output_record = workflow.execute()
        self.assertEqual(output_record.test_square_output.result, 25)
