"""
Utils for workflows.
"""

import importlib


def resolve_action_reference(action_reference):
    """
    Dynamically imports and resolves the function from the action reference.
    :param action_reference: The dotted path to the function (e.g., 'my_module.my_function').
    :return: The resolved function (either sync or async).
    """
    module_name, func_name = action_reference.rsplit('.', 1)
    module = importlib.import_module(module_name)
    func = getattr(module, func_name)
    if not callable(func):
        raise ValueError(f"Invalid action reference: {action_reference}")
    return func
