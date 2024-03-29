"""Exceptions for kwait."""


class KwaitError(Exception):
    """Base error all kwait errors inherit from."""


class InventoryError(KwaitError):
    """Base class for inventory-related exceptions."""


class MalformedManifestError(InventoryError):
    """Raised when a malformed Kubernetes manifest is passed in."""
