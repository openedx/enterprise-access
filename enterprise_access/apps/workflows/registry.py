"""
Registry for workflows app.
"""

import logging

from django.apps import apps

logger = logging.getLogger(__name__)


class WorkflowActionNotRegisteredError(Exception):
    """Raised when a requested workflow action is not registered."""


class WorkflowActionRegistry:
    """
    Registry for workflow actions.
    """
    _registry = {}
    _original_slugs = {}
    _original_names = {}

    @classmethod
    def register_action_step(cls, slug, name):
        """
        Registers an action with the workflow registry.
        :param slug: The unique identifier
        :param name: The human-readable name
        """
        def decorator(func):
            # Ensure the function is callable
            if not callable(func):
                raise ValueError(f"Registered action '{name}' is not callable.")

            # Handle slug changes
            if func in cls._original_slugs and cls._original_slugs[func] != slug:
                original_slug = cls._original_slugs[func]
                cls.update_action_slug(original_slug, slug)
                logger.info(f"Action slug '{original_slug}' has been renamed to '{slug}'. Updated database references.")

            # Handle name changes
            if func in cls._original_names and cls._original_names[func] != name:
                original_name = cls._original_names[func]
                cls.update_action_name(original_name, name)
                logger.info(f"Action name '{original_name}' has been renamed to '{name}'. Updated database references.")

            # Register the action in the registry
            cls._registry[slug] = {"name": name, "func": func}
            cls._original_slugs[func] = slug
            cls._original_names[func] = name

            # Return the original function to allow it to be called
            return func

        return decorator

    @classmethod
    def list_actions(cls):
        """
        Returns all registered workflow actions as a list of slugs and names.
        """
        return [(slug, entry["name"]) for slug, entry in cls._registry.items()]

    @classmethod
    def get(cls, identifier):
        """
        Returns the registered function for the given identifier.
        :param identifier: The slug or name of the registered action
        """
        if identifier in cls._registry:
            return cls._registry[identifier]["func"]

        for _, entry in cls._registry.items():
            if entry["name"] == identifier:
                return entry["func"]

        raise WorkflowActionNotRegisteredError(f"Action '{identifier}' is not registered.")

    @classmethod
    def update_action_slug(cls, old_slug, new_slug):
        """
        Updates the slug of a registered action.
        :param old_slug: The old slug of the action
        :param new_slug: The new slug of the action
        """
        if old_slug in cls._registry:
            cls._registry[new_slug] = cls._registry.pop(old_slug)

    @classmethod
    def update_action_name(cls, old_name, new_name):
        """
        Updates the name of a registered action.
        :param old_name: The old name of the action
        :param new_name: The new name of the action
        """
        for _, entry in cls._registry.items():
            if entry["name"] == old_name:
                entry["name"] = new_name

    @classmethod
    def unregister_action(cls, slug):
        """
        Unregisters an action and deletes the corresponding WorkflowActionStep from the database.
        :param slug: The slug of the action to unregister
        """
        if slug in cls._registry:
            del cls._registry[slug]

        # Delete the corresponding WorkflowActionStep from the database
        WorkflowActionStep = apps.get_model("workflows", "WorkflowActionStep")
        try:
            action_step = WorkflowActionStep.objects.get(action_reference=slug)
            action_step.delete()  # Hard delete
            logger.info(f"Deleted WorkflowActionStep '{slug}'.")
        except WorkflowActionStep.DoesNotExist:
            logger.warning(f"WorkflowActionStep '{slug}' not found.")

    @classmethod
    def cleanup_registry(cls):
        """
        Cleanup the registry by removing WorkflowActionSteps that no longer have associated functions.
        Called during app startup.
        """
        WorkflowActionStep = apps.get_model("workflows", "WorkflowActionStep")

        # Get the current slugs in the registry
        current_slugs = set(cls._registry.keys())

        # Fetch all WorkflowActionSteps in the database
        steps = WorkflowActionStep.objects.all()

        for step in steps:
            if step.action_reference not in current_slugs:
                # If the action is no longer in the registry, unregister and delete it
                cls.unregister_action(step.action_reference)
                logger.info(f"Action '{step.action_reference}' is no longer in registry. Deleted.")
