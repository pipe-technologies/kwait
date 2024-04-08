"""Global fixtures and test config."""

from collections.abc import Generator
import logging
from unittest import mock

import faker
import pytest

from kwait import inventory

from . import utils


@pytest.fixture(autouse=True)
def debug(caplog):
    """Set log capture level to DEBUG."""
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.WARNING, logger="faker")
    return caplog


@pytest.fixture()
def fake() -> faker.Faker:
    """Get a Faker object."""
    return faker.Faker()


# pylint: disable=redefined-outer-name


@pytest.fixture
def fake_resource(fake) -> inventory.ResourceDescriptor:
    """Fixture to get a fake ResourceDescriptor object."""
    return utils.get_fake_resource(fake)


@pytest.fixture
def mock_extension(
    fake_resource: inventory.ResourceDescriptor,
) -> Generator[utils.MockExtensionReturn, None, None]:
    """Mock out extension loading with a single driver and resource.

    This performs all the mocking needed to call `wait_for()` with a
    single driver for a single resource. It:

    * Creates a driver class for the fake resource.
    * Creates a mock extension for the driver class.
    * Patches that extension into the return value of
      `stevedore.ExtensionManager`.
    * Patches `kubernetes.client.ApiClient` and
      `kubernetes.config.load_kube_config`.
    * Asserts that `stevedore.ExtensionManager`,
      `kubernetes.client.ApiClient`, and
      `kubernetes.config.load_kube_config` were called properly and
      the mock driver was instantiated properly.
    """
    mock_driver = utils.get_mock_driver_for(fake_resource)

    mock_ext = mock.Mock()
    mock_ext.plugin = mock_driver

    with mock.patch(
        "kubernetes.config.load_kube_config"
    ) as mock_load_kube_config, mock.patch(
        "kubernetes.client.ApiClient"
    ) as mock_api_client, mock.patch(
        "stevedore.ExtensionManager"
    ) as mock_ext_mgr:
        mock_ext_mgr.return_value = [mock_ext]

        yield utils.MockExtensionReturn(mock_driver, mock_ext, mock_ext_mgr)

    mock_load_kube_config.assert_called_once_with()
    mock_api_client.assert_called_once_with()
    mock_ext_mgr.assert_called_once_with("kwait.drivers")
    mock_driver.assert_called_once_with(mock_api_client.return_value, fake_resource)
