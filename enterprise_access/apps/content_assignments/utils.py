"""
Utility functions for the content_assignments app.
"""
import traceback

from django.utils import timezone


def chunks(a_list, chunk_size):
    """
    Helper to break a list up into chunks. Returns a generator of lists.
    """
    for i in range(0, len(a_list), chunk_size):
        yield a_list[i:i + chunk_size]


def format_traceback(exception):
    trace = ''.join(traceback.format_tb(exception.__traceback__))
    return f'{exception}\n{trace}'


def are_dates_matching_with_day_offset(days_offset, target_date, date_to_offset):
    """
    Takes an integer number of days to offset from the date_to_offset to determine if
    the target_date matches the date_to_offset + days_offset date

    The target_date and date_to_offset arguments are UTC timezone objects
    """
    offset_date = date_to_offset + timezone.timedelta(days=days_offset)
    are_dates_matching = target_date.strftime('%Y-%m-%d') == offset_date.strftime('%Y-%m-%d')
    return are_dates_matching
