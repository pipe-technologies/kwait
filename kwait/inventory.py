"""Inventory a Kubernetes YAML manifest stream."""

from collections.abc import Generator
import dataclasses
import logging
from typing import IO

import yaml

from kwait import exceptions

_LOG = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, order=True)
class ResourceDescriptor:
    """Description of a single resource found in the set of manifests."""

    api_version: str
    kind: str
    namespace: str
    name: str

    def __str__(self) -> str:
        return f"{self.api_version}:{self.kind} {self.namespace}/{self.name}"


def get_resources(
    stream: IO, namespace: str = "default"
) -> Generator[ResourceDescriptor, None, None]:
    """Yield all resources that are defined in a stream of k8s manifests."""
    for manifest in yaml.safe_load_all(stream):
        if not hasattr(manifest, "get"):
            raise exceptions.MalformedManifestError(
                f"Manifest is not a YAML object: {manifest}"
            )

        if (kind := manifest.get("kind")) is None:
            raise exceptions.MalformedManifestError(
                "Manifest has no 'kind': {manifest}"
            )

        if (api_version := manifest.get("apiVersion")) is None:
            raise exceptions.MalformedManifestError(
                "Manifest has no 'apiVersion': {manifest}"
            )

        if (metadata := manifest.get("metadata")) is None:
            raise exceptions.MalformedManifestError(
                "Manifest has no 'metadata': {manifest}"
            )

        if (name := metadata.get("name")) is None:
            raise exceptions.MalformedManifestError(
                "Manifest metadata has no 'name': {metadata}"
            )

        namespace = metadata.get("namespace", namespace)

        resource = ResourceDescriptor(api_version, kind, namespace, name)
        _LOG.debug("Found resource %s", resource)
        yield resource
