"""
Helper utilities for api_client tests.
"""
import requests


class MockResponse(requests.Response):
    """
    Useful for mocking HTTP responses, especially for code that relies on raise_for_status().
    """
    def __init__(self, json_data, status_code):
        super().__init__()
        self.json_data = json_data
        self.status_code = status_code

    def json(self):  # pylint: disable=arguments-differ
        return self.json_data
