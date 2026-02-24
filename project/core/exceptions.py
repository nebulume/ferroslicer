'''
Custom exceptions for the project.
'''


class ProjectError(Exception):
    """
    Base exception for the project.
    """
    pass


class ConfigError(ProjectError):
    """
    Exception raised when there is an error in configuration.
    """
    pass


class ValidationError(ProjectError):
    """
    Exception raised when data validation fails.
    """
    pass

