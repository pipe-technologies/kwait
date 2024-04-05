"""Unit tests for `wait_for` and friends."""

from collections.abc import Generator
import dataclasses
from unittest import mock

import kubernetes
import pytest

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


@pytest.fixture
def fake_resource(fake) -> inventory.ResourceDescriptor:
    """Fixture to get a fake ResourceDescriptor object."""
    return get_fake_resource(fake)


# pylint: disable=redefined-outer-name


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


@pytest.fixture
def mock_extension(
    fake_resource: inventory.ResourceDescriptor,
) -> Generator[MockExtensionReturn, None, None]:
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
    mock_driver = get_mock_driver_for(fake_resource)

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

        yield MockExtensionReturn(mock_driver, mock_ext, mock_ext_mgr)

    mock_load_kube_config.assert_called_once_with()
    mock_api_client.assert_called_once_with()
    mock_ext_mgr.assert_called_once_with("kwait.drivers")
    mock_driver.assert_called_once_with(mock_api_client.return_value, fake_resource)


def get_mock_driver_for(resource: inventory.ResourceDescriptor) -> type:
    """Get a mock driver for the given resource."""
    driver = mock.Mock(spec=drivers.BaseDriver)
    driver.api_version = resource.api_version
    driver.kind = resource.kind
    driver.return_value.is_ready.return_value = False
    return driver


# pylint: disable=missing-function-docstring


@pytest.mark.parametrize("is_ready", [True, False])
def test_ready_result_bool(fake, is_ready: bool) -> None:
    result = wait.ReadyResult(mock.Mock(), is_ready, fake.word())
    assert bool(result) == is_ready


@pytest.mark.parametrize(
    "initial_error_state",
    [
        None,
        kubernetes.client.ApiException(status=404, reason="Not found"),
        kubernetes.client.ApiException(status=418, reason="I'm a teapot"),
        ValueError("bogus"),
    ],
)
def test_wait_for(
    fake_resource: inventory.ResourceDescriptor,
    mock_extension: MockExtensionReturn,
    initial_error_state: Exception | None,
) -> None:
    if initial_error_state:
        mock_extension.is_ready.side_effect = initial_error_state

    is_ready = wait.wait_for([fake_resource], interval=0.1, timeout=0.5)

    result = next(is_ready)
    assert not result

    if initial_error_state:
        mock_extension.is_ready.side_effect = None
    mock_extension.is_ready.return_value = True
    for result in is_ready:
        if result:
            break
    else:
        assert result, "Resource never became ready"


def test_wait_for_skip_unsupported_resources(
    fake,
    fake_resource: inventory.ResourceDescriptor,
    mock_extension: MockExtensionReturn,  # pylint: disable=unused-argument
) -> None:
    mock_extension.is_ready.return_value = True
    unsupported = get_fake_resource(fake)

    is_ready = wait.wait_for([unsupported, fake_resource], interval=0.1, timeout=0.5)

    for result in is_ready:
        if result:
            break
    else:
        pytest.fail("Resources never became ready")


def test_wait_for_timeout(
    fake_resource: inventory.ResourceDescriptor,
    mock_extension: MockExtensionReturn,  # pylint: disable=unused-argument
) -> None:
    with pytest.raises(TimeoutError):
        for result in wait.wait_for([fake_resource], interval=0.1, timeout=0.5):
            assert not result, "Resource unexpectedly ready"


def test_wait_for_multiple(fake) -> None:
    resources = [get_fake_resource(fake) for _ in range(3)]
    mock_drivers = []
    extensions = []
    for resource in resources:
        mock_driver = get_mock_driver_for(resource)
        mock_drivers.append(mock_driver)

        mock_ext = mock.Mock()
        mock_ext.plugin = mock_driver
        extensions.append(mock_ext)

    with mock.patch("kubernetes.config.load_kube_config"), mock.patch(
        "kubernetes.client.ApiClient"
    ), mock.patch("stevedore.ExtensionManager") as mock_ext_mgr:
        mock_ext_mgr.return_value = extensions

        is_ready = wait.wait_for(resources, interval=0.1, timeout=1)

        result = next(is_ready)
        assert not result

        mock_drivers[0].is_ready.return_value = True
        result = next(is_ready)
        assert not result

        for mock_driver in mock_drivers:
            mock_driver.return_value.is_ready.return_value = True

        for result in is_ready:
            if result:
                break
        else:
            assert result, "Resource never became ready"

    mock_ext_mgr.assert_called_once_with("kwait.drivers")
