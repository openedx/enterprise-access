"""
Utility functions for the content_assignments app.
"""
import traceback


def chunks(a_list, chunk_size):
    """
    Helper to break a list up into chunks. Returns a generator of lists.
    """
    for i in range(0, len(a_list), chunk_size):
        yield a_list[i:i + chunk_size]


def format_traceback(exception):
    return ''.join(traceback.format_tb(exception.__traceback__))
