'''
API client for interacting with external services.
'''

import requests
from typing import Dict, Any, Optional
from .exceptions import ProjectError


class APIClient:
    """
    A simple API client.
    """

    def __init__(self, base_url: str, headers: Optional[Dict[str, str]] = None):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        if headers:
            self.session.headers.update(headers)

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a GET request.

        Args:
            endpoint (str): The API endpoint.
            params (Dict[str, Any]): Query parameters.

        Returns:
            Dict[str, Any]: The response JSON.

        Raises:
            ProjectError: If the request fails.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ProjectError(f"GET request failed: {e}")

    def post(self, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a POST request.

        Args:
            endpoint (str): The API endpoint.
            data (Dict[str, Any]): Request body.

        Returns:
            Dict[str, Any]: The response JSON.

        Raises:
            ProjectError: If the request fails.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = self.session.post(url, json=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ProjectError(f"POST request failed: {e}")
