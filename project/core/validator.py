'''
Data validation utilities for the project.
'''

from typing import Any, Dict, List, Union
from .exceptions import ValidationError


def validate_dict(data: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """
    Validate a dictionary against a schema.

    Args:
        data (Dict[str, Any]): The data to validate.
        schema (Dict[str, Any]): The schema to validate against.

    Raises:
        ValidationError: If validation fails.
    """
    for key, expected_type in schema.items():
        if key not in data:
            raise ValidationError(f"Missing required key: {key}")

        if not isinstance(data[key], expected_type):
            raise ValidationError(
                f"Key '{key}' must be of type {expected_type.__name__}, got {type(data[key]).__name__}"
            )


def validate_list(data: List[Any], item_type: type) -> None:
    """
    Validate that all items in a list are of the expected type.

    Args:
        data (List[Any]): The list to validate.
        item_type (type): The expected type of items in the list.

    Raises:
        ValidationError: If validation fails.
    """
    for i, item in enumerate(data):
        if not isinstance(item, item_type):
            raise ValidationError(
                f"Item at index {i} must be of type {item_type.__name__}, got {type(item).__name__}"
            )


def validate_string(data: str, min_length: int = 0, max_length: int = None) -> None:
    """
    Validate a string.

    Args:
        data (str): The string to validate.
        min_length (int): Minimum allowed length.
        max_length (int): Maximum allowed length.

    Raises:
        ValidationError: If validation fails.
    """
    if not isinstance(data, str):
        raise ValidationError(f"Expected string, got {type(data).__name__}")

    if len(data) < min_length:
        raise ValidationError(f"String must be at least {min_length} characters long")

    if max_length is not None and len(data) > max_length:
        raise ValidationError(f"String must be at most {max_length} characters long")


def validate_number(data: Union[int, float], min_value: float = None, max_value: float = None) -> None:
    """
    Validate a number.

    Args:
        data (Union[int, float]): The number to validate.
        min_value (float): Minimum allowed value.
        max_value (float): Maximum allowed value.

    Raises:
        ValidationError: If validation fails.
    """
    if not isinstance(data, (int, float)):
        raise ValidationError(f"Expected number, got {type(data).__name__}")

    if min_value is not None and data < min_value:
        raise ValidationError(f"Number must be at least {min_value}")

    if max_value is not None and data > max_value:
        raise ValidationError(f"Number must be at most {max_value}")
