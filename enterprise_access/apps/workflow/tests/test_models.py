from django.test import TestCase

from .models import TestWorkflow, TestWorkflowStep, TestStepInput, TestWorkflowInput


class TestWorkflowModels(TestCase):
    """
    """
    def test_empty_workflow(self):
        input_data = {
            TestStepInput.KEY: {
                'argument_1': 2,
                'argument_2': 3,
            },
        }
        workflow = TestWorkflow.objects.create(
            input_data=input_data,
        )
        workflow.execute()
