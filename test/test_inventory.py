"""Unit tests for inventory bits."""

import io
from typing import Any

import pytest
from ruamel import yaml

from kwait import exceptions
from kwait import inventory

_MANIFEST = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {"name": "test"},
    "spec": {
        "containers": [
            {
                "name": "test",
                "image": "test:1.2.3",
            }
        ]
    },
}


def _dict_without_key(data: dict[str, Any], key: str) -> dict[str, Any]:
    retval = data.copy()
    retval.pop(key)
    return retval


def _get_yaml_stream(data: dict[str, Any]) -> io.StringIO:
    stream = io.StringIO()
    yaml.YAML().dump(data, stream=stream)
    stream.seek(0)
    return stream


# pylint: disable=missing-function-docstring


def test_malformed_yaml() -> None:
    stream = io.StringIO("{]")
    stream.seek(0)
    with pytest.raises(exceptions.MalformedManifestError) as excinfo:
        list(inventory.get_resources(stream))
    assert "not valid YAML" in str(excinfo.value)


@pytest.mark.parametrize(
    ("manifest_data", "error_match"),
    [
        ([], "not a YAML object"),
        (_dict_without_key(_MANIFEST, "kind"), "has no 'kind'"),
        (
            _dict_without_key(_MANIFEST, "apiVersion"),
            "has no 'apiVersion'",
        ),
        (
            _dict_without_key(_MANIFEST, "metadata"),
            "has no 'metadata'",
        ),
        ({**_MANIFEST, "metadata": {}}, "metadata has no 'name'"),
    ],
)
def test_malformed_manifest(manifest_data: dict[str, Any], error_match: str) -> None:
    with pytest.raises(exceptions.MalformedManifestError) as excinfo:
        list(inventory.get_resources(_get_yaml_stream(manifest_data)))
    assert error_match in str(excinfo.value)


@pytest.mark.parametrize("include_manifest_namespace", [True, False])
@pytest.mark.parametrize("include_default_namespace", [True, False])
def test_single_resource(
    fake, include_manifest_namespace: bool, include_default_namespace: bool
) -> None:
    api_version = fake.unique.word()
    kind = fake.unique.word()
    default_ns = fake.unique.word()
    manifest_ns = fake.unique.word()
    name = fake.unique.word()

    manifest = {
        "apiVersion": api_version,
        "kind": kind,
        "metadata": {"name": name},
        "spec": {},
    }
    if include_manifest_namespace:
        manifest["metadata"]["namespace"] = manifest_ns

    stream = _get_yaml_stream(manifest)
    if include_default_namespace:
        results = list(inventory.get_resources(stream, default_namespace=default_ns))
    else:
        results = list(inventory.get_resources(stream))

    assert len(results) == 1
    result = results[0]

    assert result.api_version == api_version
    assert result.kind == kind
    assert result.name == name

    if include_manifest_namespace:
        assert result.namespace == manifest_ns
    elif include_default_namespace:
        assert result.namespace == default_ns
    else:
        assert result.namespace == "default"


def test_multiple_resources(fake) -> None:
    expected = []
    manifests = []

    default_ns = fake.unique.word()

    api_version = fake.unique.word()
    kind = fake.unique.word()
    namespace = fake.unique.word()
    name = "explicit-namespace"
    manifests.append(
        {
            "apiVersion": api_version,
            "kind": kind,
            "metadata": {"name": name, "namespace": namespace},
            "spec": {},
        }
    )
    expected.append(inventory.ResourceDescriptor(api_version, kind, namespace, name))

    api_version = fake.unique.word()
    kind = fake.unique.word()
    name = "default-namespace"
    manifests.append(
        {
            "apiVersion": api_version,
            "kind": kind,
            "metadata": {"name": name},
            "spec": {},
        }
    )
    expected.append(inventory.ResourceDescriptor(api_version, kind, default_ns, name))

    stream = io.StringIO()
    yaml.YAML(typ="full").dump_all(manifests, stream=stream)
    stream.seek(0)

    assert sorted(
        inventory.get_resources(stream, default_namespace=default_ns)
    ) == sorted(expected)
