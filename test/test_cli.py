"""CLI unit tests."""

import dataclasses
import io
import json
import logging
import operator
import pathlib
from typing import Any

import click.testing
import pytest
from ruamel import yaml

from kwait import cli
from kwait import inventory
from kwait import wait

from . import utils

_LOG = logging.getLogger()


def _run(*args: str, expect_failure: bool = False):
    runner = click.testing.CliRunner(mix_stderr=False)
    all_args = ["-vv"] + list(args)
    _LOG.info("Invoking CLI with %s", all_args)
    result = runner.invoke(cli.cli, all_args)
    if expect_failure:
        assert result.exit_code != 0, f"{all_args!r} unexpectedly returned 0"
    else:
        assert (
            result.exit_code == 0
        ), f"{all_args!r} returned non-zero ({result.exit_code}): {result.stderr}"
    return result


@dataclasses.dataclass
class Manifest:
    """Testable representation of a k8s manifest.

    `Manifest.get_random()` isn't a pytest fixture because we often
    need to create multiple manifests, or manifests with different
    options.
    """

    api_version: str
    kind: str
    name: str
    namespace: str | None = None

    @classmethod
    def from_resource(cls, resource: inventory.ResourceDescriptor) -> "Manifest":
        """Get a testable k8s manifest from a `ResourceDescriptor` object."""
        return cls(
            resource.api_version,
            resource.kind,
            resource.name,
            resource.namespace,
        )

    @classmethod
    def get_random(cls, fake, *, include_namespace: bool = True) -> "Manifest":
        """Create a random testable k8s manifest.

        Definitely not guaranteed to be a real manifest. :)
        """
        return cls(
            "v1",
            fake.word(),
            fake.unique.word(),
            fake.word() if include_namespace else None,
        )

    def write(self, filename: pathlib.Path) -> None:
        """Write the manifest to a file on disk."""
        yaml.YAML().dump(self.manifest, stream=filename.open("w"))

    @property
    def manifest(self) -> dict[str, Any]:
        """Get the actual manifest data as a dict."""
        manifest = {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "metadata": {"name": self.name},
            "spec": {
                "containers": [
                    {
                        "name": self.name,
                        "image": "test:1.2.3",
                    }
                ]
            },
        }
        if self.namespace is not None:
            manifest["metadata"]["namespace"] = self.namespace

        return manifest


# pylint: disable=missing-function-docstring


@pytest.mark.parametrize("set_default_namespace", [True, False])
@pytest.mark.parametrize("include_namespace", [True, False])
def test_inventory(
    fake,
    tmp_path: pathlib.Path,
    include_namespace: bool,
    set_default_namespace: bool,
) -> None:
    manifest = Manifest.get_random(fake, include_namespace=include_namespace)
    manifest_file = tmp_path / fake.file_name(extension="yaml")
    manifest.write(manifest_file)

    args = ["inventory", "--output", "json", str(manifest_file)]
    default_ns = fake.unique.word()
    if set_default_namespace:
        args.extend(["--namespace", default_ns])
    result = _run(*args)

    if include_namespace:
        expected_namespace = manifest.namespace
    elif set_default_namespace:
        expected_namespace = default_ns
    else:
        expected_namespace = "default"
    assert json.loads(result.stdout) == [
        {
            "api_version": manifest.api_version,
            "kind": manifest.kind,
            "namespace": expected_namespace,
            "name": manifest.name,
        }
    ]


def test_inventory_multiple_manifests(fake, tmp_path: pathlib.Path) -> None:
    manifests = [
        Manifest.get_random(fake),
        Manifest.get_random(fake),
        Manifest.get_random(fake),
    ]
    manifest_file = tmp_path / fake.file_name(extension="yaml")
    yaml.YAML().dump_all(
        [m.manifest for m in manifests], stream=manifest_file.open("w")
    )

    result = _run("inventory", "--output", "json", str(manifest_file))

    assert sorted(json.loads(result.stdout), key=operator.itemgetter("name")) == sorted(
        [
            {
                "api_version": m.api_version,
                "kind": m.kind,
                "namespace": m.namespace,
                "name": m.name,
            }
            for m in manifests
        ],
        key=operator.itemgetter("name"),
    )


def test_inventory_multiple_files(fake, tmp_path: pathlib.Path) -> None:
    manifests = [
        Manifest.get_random(fake),
        Manifest.get_random(fake),
        Manifest.get_random(fake),
    ]
    args = ["inventory", "--output", "json"]
    for manifest in manifests:
        manifest_file = tmp_path / fake.unique.file_name(extension="yaml")
        manifest.write(manifest_file)
        args.append(str(manifest_file))

    result = _run(*args)

    assert sorted(json.loads(result.stdout), key=operator.itemgetter("name")) == sorted(
        [
            {
                "api_version": m.api_version,
                "kind": m.kind,
                "namespace": m.namespace,
                "name": m.name,
            }
            for m in manifests
        ],
        key=operator.itemgetter("name"),
    )


@pytest.mark.parametrize("subcommand", ["inventory", "wait"])
def test_malformed_file(fake, tmp_path: pathlib.Path, subcommand: str) -> None:
    manifest_file = tmp_path / fake.file_name(extension="yaml")
    manifest_file.open("w").write("[}")

    result = _run(subcommand, str(manifest_file), expect_failure=True)
    assert result.exit_code == 3
    assert "Error parsing" in result.stderr


def test_inventory_plain_output(fake, tmp_path: pathlib.Path) -> None:
    manifest = Manifest.get_random(fake)
    manifest_file = tmp_path / fake.file_name(extension="yaml")
    manifest.write(manifest_file)

    result = _run("inventory", "--output", "plain", "--no-header", str(manifest_file))

    # we split the result on whitespace to do the comparison because
    # tabulate controls the number of spaces between the fields
    assert result.stdout.strip().split() == [
        f"{manifest.namespace}/{manifest.name}",
        f"{manifest.api_version}:{manifest.kind}",
    ]


def test_inventory_yaml_output(fake, tmp_path: pathlib.Path) -> None:
    manifest = Manifest.get_random(fake)
    manifest_file = tmp_path / fake.file_name(extension="yaml")
    manifest.write(manifest_file)

    result = _run("inventory", "--output", "yaml", str(manifest_file))

    assert yaml.YAML(typ="safe").load(stream=io.StringIO(result.stdout)) == [
        {
            "api_version": manifest.api_version,
            "kind": manifest.kind,
            "namespace": manifest.namespace,
            "name": manifest.name,
        }
    ]


def test_wait_json_output(
    fake,
    tmp_path: pathlib.Path,
    fake_resource: inventory.ResourceDescriptor,
    mock_extension: utils.MockExtensionReturn,  # pylint: disable=unused-argument
) -> None:
    manifest = Manifest.from_resource(fake_resource)
    manifest_file = tmp_path / fake.file_name(extension="yaml")
    manifest.write(manifest_file)

    mock_extension.is_ready.return_value = wait.ReadyResult(
        fake_resource, True, "ready"
    )

    result = _run(
        "wait",
        "--output",
        "json",
        "--poll-interval",
        "0.1",
        "--timeout",
        "0.5",
        str(manifest_file),
    )

    summary_line = result.stdout.splitlines()[-1]
    assert json.loads(summary_line) == [
        {"resource": dataclasses.asdict(fake_resource), "is_ready": True}
    ]


def test_wait_yaml_output(
    fake,
    tmp_path: pathlib.Path,
    fake_resource: inventory.ResourceDescriptor,
    mock_extension: utils.MockExtensionReturn,  # pylint: disable=unused-argument
) -> None:
    manifest = Manifest.from_resource(fake_resource)
    manifest_file = tmp_path / fake.file_name(extension="yaml")
    manifest.write(manifest_file)

    mock_extension.is_ready.return_value = wait.ReadyResult(
        fake_resource, True, "ready"
    )

    result = _run(
        "wait",
        "--output",
        "yaml",
        "--poll-interval",
        "0.1",
        "--timeout",
        "0.5",
        str(manifest_file),
    )

    summary_doc = result.stdout.split("\n---\n")[-1]
    assert yaml.YAML(typ="safe").load(stream=io.StringIO(summary_doc)) == [
        {"resource": dataclasses.asdict(fake_resource), "is_ready": True}
    ]


def test_wait_plain_output(
    fake,
    tmp_path: pathlib.Path,
    fake_resource: inventory.ResourceDescriptor,
    mock_extension: utils.MockExtensionReturn,  # pylint: disable=unused-argument
) -> None:
    manifest = Manifest.from_resource(fake_resource)
    manifest_file = tmp_path / fake.file_name(extension="yaml")
    manifest.write(manifest_file)

    mock_extension.is_ready.return_value = wait.ReadyResult(
        fake_resource, True, "ready"
    )

    result = _run(
        "wait",
        "--output",
        "plain",
        "--poll-interval",
        "0.1",
        "--timeout",
        "0.5",
        str(manifest_file),
    )

    raw_summary_table = result.stdout.split("\nSUMMARY:\n")[-1]
    lines = []
    table_data_started = False
    for line in raw_summary_table.splitlines():
        if line.startswith("-"):
            table_data_started = True
            continue

        if table_data_started:
            # we split the result on whitespace to do the comparison
            # because tabulate controls the number of spaces between
            # the fields
            lines.append(line.split())

    assert lines == [
        [
            fake_resource.kind,
            f"{fake_resource.namespace}/{fake_resource.name}",
            "Yes",
        ]
    ]


def test_wait_timeout(
    fake,
    tmp_path: pathlib.Path,
    fake_resource: inventory.ResourceDescriptor,
    mock_extension: utils.MockExtensionReturn,  # pylint: disable=unused-argument
) -> None:
    manifest = Manifest.from_resource(fake_resource)
    manifest_file = tmp_path / fake.file_name(extension="yaml")
    manifest.write(manifest_file)

    result = _run(
        "wait",
        "--output",
        "json",
        "--poll-interval",
        "0.1",
        "--timeout",
        "0.5",
        str(manifest_file),
        expect_failure=True,
    )

    assert result.exit_code == 2
    assert "timeout" in result.stderr.lower()

    summary_line = result.stdout.splitlines()[-1]
    assert json.loads(summary_line) == [
        {"resource": dataclasses.asdict(fake_resource), "is_ready": False}
    ]
