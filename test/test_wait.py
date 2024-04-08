"""Unit tests for `wait_for` and friends."""

from unittest import mock

import kubernetes
import pytest

from kwait import inventory
from kwait import wait

from . import utils

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
    mock_extension: utils.MockExtensionReturn,
    initial_error_state: Exception | None,
) -> None:
    if initial_error_state:
        mock_extension.is_ready.side_effect = initial_error_state

    is_ready = wait.wait_for([fake_resource], interval=0.1, timeout=0.5)

    result = next(is_ready)
    assert not result

    if initial_error_state:
        mock_extension.is_ready.side_effect = None
    mock_extension.is_ready.return_value = wait.ReadyResult(
        fake_resource, True, "ready"
    )
    for result in is_ready:
        if result:
            break
    else:
        assert result, "Resource never became ready"


def test_wait_for_skip_unsupported_resources(
    fake,
    fake_resource: inventory.ResourceDescriptor,
    mock_extension: utils.MockExtensionReturn,  # pylint: disable=unused-argument
) -> None:
    mock_extension.is_ready.return_value = wait.ReadyResult(
        fake_resource, True, "ready"
    )
    unsupported = utils.get_fake_resource(fake)

    is_ready = wait.wait_for([unsupported, fake_resource], interval=0.1, timeout=0.5)

    for result in is_ready:
        if result:
            break
    else:
        pytest.fail("Resources never became ready")


def test_wait_for_timeout(
    fake_resource: inventory.ResourceDescriptor,
    mock_extension: utils.MockExtensionReturn,  # pylint: disable=unused-argument
) -> None:
    with pytest.raises(TimeoutError):
        for result in wait.wait_for([fake_resource], interval=0.1, timeout=0.5):
            assert not result, "Resource unexpectedly ready"


def test_wait_for_multiple(fake) -> None:
    resources = [utils.get_fake_resource(fake) for _ in range(3)]
    mock_drivers = []
    extensions = []
    for resource in resources:
        mock_driver = utils.get_mock_driver_for(resource)
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

        mock_drivers[0].is_ready.return_value = wait.ReadyResult(
            resources[0], True, "ready"
        )
        result = next(is_ready)
        assert not result

        for i, mock_driver in enumerate(mock_drivers):
            mock_driver.return_value.is_ready.return_value = wait.ReadyResult(
                resources[i], True, "ready"
            )

        for result in is_ready:
            if result:
                break
        else:
            assert result, "Resource never became ready"

    mock_ext_mgr.assert_called_once_with("kwait.drivers")
