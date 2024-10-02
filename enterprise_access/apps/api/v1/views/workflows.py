"""
Views for workflows
"""

from rest_framework.response import Response
from rest_framework import status
from viewflow.workflow.models import Process
from viewflow.workflow.flow.views import CreateProcessView, UpdateProcessView

from enterprise_access.apps.workflows.flows import DefaultEnterpriseCourseEnrollmentFlow


class WorkflowViewSetMixin:
    """
    A reusable mixin for handling Viewflow workflows with Django Rest Framework viewsets.
    This mixin can be used across multiple workflow viewsets to handle common actions
    like starting a process, executing a task, and checking if a process exists.
    """

    process_class = None  # This should be set in the viewset to the associated workflow Process class

    def get_process_class(self):
        """Return the associated workflow Process class."""
        if not self.process_class:
            raise NotImplementedError('You must define `process_class` in your viewset.')
        return self.process_class

    
    def get_active_process(self, user=None):
        """
        Retrieve an active process instance if it exists. Optionally filter by user.
        This method assumes that the process model has a 'status' field and checks if there is an active process.
        """
        process_class = self.get_process_class()

        # Customize the query as needed. Here we're checking if the process is not done (still active).
        query = process_class.objects.filter(status__in=[Process.STATUS.NEW, Process.STATUS.IN_PROGRESS])

        # Optionally filter by user if provided
        if user:
            query = query.filter(created_by=user)  # Assuming 'created_by' is a field in the process model

        return query.first()  # Return the first active process, if any

    
    def start_workflow(self, request):
        """
        Starts a new workflow process.
        """
        # Check if any process already exists for the given criteria
        process_class = self.get_process_class()
        if not process_class.objects.exists():
            # Create a new process
            process = process_class.objects.create()

            # Optionally start the workflow
            request.activation.execute()  # Starts the workflow

            return Response({'status': 'new process started'}, status=status.HTTP_201_CREATED)
        else:
            return Response({'status': 'process already exists'}, status=status.HTTP_400_BAD_REQUEST)

    def execute_task(self, request, process_instance):
        """
        Executes the next task in the workflow.
        """
        if process_instance:
            request.activation.execute()  # Proceed to the next task in the workflow
            return Response({'status': 'task executed'}, status=status.HTTP_200_OK)
        return Response({'status': 'no process found'}, status=status.HTTP_404_NOT_FOUND)

    def get_current_task(self, process_instance):
        """
        Retrieve the current task in the workflow for a given process.
        """
        # Assuming there's a relationship between tasks and processes
        task = process_instance.task_set.filter(status=Process.STATUS.IN_PROGRESS).first()
        if task:
            return task
        return None


class EnterpriseCourseEnrollmentViewSet(FlowViewset):
