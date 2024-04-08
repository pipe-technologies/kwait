"""Test utilities."""

import dataclasses
from unittest import mock

from kwait import drivers
from kwait import inventory
from kwait import wait


def get_fake_resource(fake) -> inventory.ResourceDescriptor:
    """Get a fake ResourceDescriptor object.

    Kind is guaranteed to be unique. This ensures that every fake
    resource is unique without squandering our precious unique words.
    """
    return inventory.ResourceDescriptor(
        fake.word(),
        fake.unique.word(),
        fake.word(),
        fake.word(),
    )


@dataclasses.dataclass
class MockExtensionReturn:
    """Return value from `mock_extension()`."""

    mock_driver: type
    mock_extension: mock.Mock
    mock_extension_manager: mock.Mock

    @property
    def is_ready(self) -> mock.Mock:
        """Shortcut to the `is_ready()` function on the instantiated driver."""
        return self.mock_driver.return_value.is_ready


def get_mock_driver_for(resource: inventory.ResourceDescriptor) -> type:
    """Get a mock driver for the given resource."""
    driver = mock.Mock(spec=drivers.BaseDriver)
    driver.api_version = resource.api_version
    driver.kind = resource.kind
    driver.return_value.is_ready.return_value = wait.ReadyResult(
        resource, False, "not ready"
    )
    driver.return_value.resource = resource
    return driver
