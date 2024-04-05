"""Wait for resources."""

from collections.abc import Generator
from collections.abc import Iterable
import dataclasses
import logging
import queue
import threading
import time
from typing import TYPE_CHECKING

import kubernetes
import stevedore

from kwait import inventory

if TYPE_CHECKING:
    from kwait import drivers  # pragma: nocover

_LOG = logging.getLogger(__name__)


@dataclasses.dataclass
class ReadyResult:
    """Representation of the result of a single check for a single resource."""

    resource: inventory.ResourceDescriptor
    is_ready: bool

    # state is a *brief* description of the state that will be used in
    # the status updates streamed to the caller. more details can be
    # emitted at the debug log level.
    state: str

    def __bool__(self) -> bool:
        return self.is_ready


def _wait_for_resource(
    driver: "drivers.BaseDriver",
    terminate: threading.Event,
    result_queue: queue.Queue,
    interval: float,
) -> None:
    result_queue.put(ReadyResult(driver.resource, False, "polling started"))
    is_ready = False

    while not is_ready:
        try:
            is_ready = driver.is_ready()
        except kubernetes.client.ApiException as err:
            if err.status == 404:
                _LOG.debug("%s does not exist yet", driver.resource)
                result_queue.put(ReadyResult(driver.resource, False, "does not exist"))
            else:
                _LOG.exception(err)
                result_queue.put(
                    ReadyResult(
                        driver.resource, False, f"unexpected Kubernetes error: {err}"
                    )
                )
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.exception(err)
            result_queue.put(
                ReadyResult(driver.resource, False, f"unexpected error: {err}")
            )
        else:
            result_queue.put(is_ready)

        if terminate.is_set():
            _LOG.debug("Breaking polling loop for %s", driver.resource)
            result_queue.put(ReadyResult(driver.resource, False, "aborted"))
            break

        if not is_ready:
            time.sleep(interval)

    _LOG.debug("Polling for %s complete, resource is ready", driver.resource)


def wait_for(
    resources: Iterable[inventory.ResourceDescriptor],
    *,
    interval: float = 5.0,
    timeout: float = 600.0,
) -> Generator[ReadyResult, None, None]:
    """Wait for a set of resources to be ready."""
    kubernetes.config.load_kube_config()
    client = kubernetes.client.ApiClient()

    extensions = {}
    extensions_loaded = []
    for extension in stevedore.ExtensionManager("kwait.drivers"):
        extensions.setdefault(extension.plugin.api_version, {})[
            extension.plugin.kind
        ] = extension.plugin
        extensions_loaded.append(
            f"{extension.plugin.api_version}:{extension.plugin.kind}"
        )
    _LOG.debug("Loaded %s drivers: %s", len(extensions_loaded), extensions_loaded)

    threads = {}
    terminate = threading.Event()
    result_queue = queue.Queue()
    try:
        for resource in resources:
            if driver_cls := extensions.get(resource.api_version, {}).get(
                resource.kind
            ):
                driver = driver_cls(client, resource)
                _LOG.debug("Starting polling for %s with %s", resource, driver_cls)
                threads[resource] = threading.Thread(
                    target=_wait_for_resource,
                    args=(driver, terminate, result_queue, interval),
                )
                threads[resource].start()
            else:
                _LOG.debug("No driver for %s, skipping", resource)
                continue

        start = time.time()
        while threads:
            thread_poll_interval = interval / len(threads)

            complete = []
            for resource, thread in threads.items():
                thread.join(timeout=thread_poll_interval)
                if not thread.is_alive():
                    _LOG.info("Polling for %s complete", resource)
                    complete.append(resource)

                # we break out of this loop when the queue is empty
                while True:
                    try:
                        result = result_queue.get_nowait()
                    except queue.Empty:
                        break
                    yield result

            for resource in complete:
                del threads[resource]

            if (elapsed := time.time() - start) > timeout:
                if terminate.is_set():
                    raise TimeoutError(f"{timeout}s timeout exceeded")

                _LOG.error("Timeout exceeded, aborting")
                terminate.set()
            else:
                _LOG.debug(
                    "%s seconds elapsed, waiting on %s resources",
                    elapsed,
                    len(threads),
                )
    except Exception:
        terminate.set()
        raise
