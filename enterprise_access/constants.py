"""
Enterprise Django application constants.
"""

BRAZE_GROUPS_EMAIL_CAMPAIGNS = {
    'INVITATION_NOTIFICATION_ID': '29a96da4-faea-499a-985b-423d0f4eb7bd',
    'REMOVAL_NOTIFICATION_ID': '',
    'AUTO_REMINDER': {
        'FIRST_NOTIFICATION': {
            'DAY': 5,
            'ID': '',
        },
        'SECOND_NOTIFICATION': {
            'DAY': 25,
            'ID': '',
        },
        'THIRD_NOTIFICATION': {
            'DAY': 50,
            'ID': '',
        },
        'FOURTH_NOTIFICATION': {
            'DAY': 65,
            'ID': '',
        },
        'FINAL_NOTIFICATION': {
            'DAY': 85,
            'ID': '',
        },
    }
}

DAYS_TO_PURGE_PII = 90