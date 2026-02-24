'''
Utility functions for the project.
'''

import os
from pathlib import Path
from typing import List, Dict, Any


def ensure_directory(path: str) -> None:
    """
    Ensure that a directory exists.
    """
    Path(path).mkdir(parents=True, exist_ok=True)


def flatten_dict(d: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, Any]:
    """
    Flatten a nested dictionary.
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def get_project_root() -> str:
    """
    Get the project root directory.
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

