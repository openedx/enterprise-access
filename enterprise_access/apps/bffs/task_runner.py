"""
Task runner for executing concurrent tasks.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings

logger = logging.getLogger(__name__)


class ConcurrentTaskRunner:
    """
    Accepts a dictionary of task definitions and runs them concurrently.
    """

    def __init__(self, task_definitions):
        """
        Initializes the runner with a pre-built dictionary of tasks.

        Args:
            task_definitions (dict): A dictionary where keys are group names
                                     and values are lists of callable tasks.
        """
        self.task_registry = task_definitions or {}

    def run_group(self, group, max_workers=None):
        """
        Runs all tasks for a specific group using a ThreadPoolExecutor.
        """
        tasks_to_run = self.task_registry.get(group, [])
        if not tasks_to_run:
            logger.warning(f"No tasks found for group '{group.name}'.")
            return []

        num_tasks_to_run = len(tasks_to_run)
        logger.info(
            f"Running task group: '{group.name}' with {num_tasks_to_run} tasks"
        )
        if not max_workers:
            default_max_workers = (os.cpu_count() or 1) + 4
            max_workers = min(num_tasks_to_run, default_max_workers)
            if settings.MAX_CONCURRENT_TASK_WORKERS is not None:
                max_workers = min(max_workers, settings.MAX_CONCURRENT_TASK_WORKERS)
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(task): task for task in tasks_to_run
            }
            for future in as_completed(future_to_task):
                task_name = future_to_task[future].__name__
                try:
                    result = future.result()
                    logger.info(f"Task {task_name} completed successfully")
                    results.append({
                        'task_name': task_name,
                        'result': result,
                        'error': None
                    })
                except Exception as exc:  # pylint: disable=broad-except
                    logger.exception(f"Task {task_name} failed")
                    results.append({
                        'task_name': task_name,
                        'result': None,
                        'error': str(exc)
                    })
        return results

    def handle_failed_tasks(self, task_results, error_callback):
        """
        Process any failed tasks from the results.

        Args:
            task_results (list): List of task result dictionaries.
            error_callback (callable): A function that will be called for each failed task.
                                     Signature: error_callback(task_name, error_message)
        """
        if not task_results:
            return
            
        failed_tasks = [result for result in task_results if result['error'] is not None]
        for failed_task in failed_tasks:
            error_callback(failed_task['task_name'], str(failed_task['error']))

    def __enter__(self):
        """Entering the 'with' block returns the runner instance."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exiting the 'with' block. No cleanup needed."""
