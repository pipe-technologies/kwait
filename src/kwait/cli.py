"""Main CLI entrypoint."""

from collections.abc import Iterable
import dataclasses
import enum
import json
import logging
import pathlib
import sys

import click
from ruamel import yaml
import tabulate

from kwait import exceptions
from kwait import inventory
from kwait import wait

_LOG = logging.getLogger(__name__)


class ReturnCodes(enum.Enum):
    """CLI return codes."""

    ARGUMENT_PARSING_FAILED = 1
    TIMEOUT = 2
    INVALID_YAML = 3


class OutputFormat(enum.Enum):
    """Output format options."""

    JSON = enum.auto()
    PLAIN = enum.auto()
    YAML = enum.auto()


def _setup_logging(verbose: int) -> None:
    if verbose > 2:
        # for -vvv, set up debug logging on the root logger, so that all
        # imported modules log as well
        logger = logging.getLogger()  # pragma: nocover
    else:
        logger = logging.getLogger("kwait")

    level = max(logging.DEBUG, logging.WARNING - 10 * verbose)
    logger.setLevel(level)
    logger.addHandler(logging.StreamHandler())


def _ok(msg: str) -> str:
    return click.style(msg, fg="green")


def _err(msg: str) -> str:
    return click.style(msg, fg="red")


def _warn(msg: str) -> str:
    return click.style(msg, fg="yellow")


def _get_resources(
    filenames: Iterable[pathlib.Path],
    default_namespace: str,
    *,
    recursive: bool = False,
) -> list[inventory.ResourceDescriptor]:
    """CLI convenience wrapper to inventory all resourcs in multiple files."""
    resources = []
    for filename in filenames:
        if recursive and filename.is_dir():
            resources.extend(
                _get_resources(
                    filename.iterdir(), default_namespace, recursive=recursive
                )
            )
        elif filename.is_dir():
            _LOG.warning("Skipping directory %s", filename)
        else:
            try:
                resources.extend(
                    inventory.get_resources(
                        # might be a click type hint bug -- pyright
                        # thinks `click.open_file()` only accepts a
                        # string, but the docs tell us it accepts a
                        # `PathLike`, which includes `pathlib.Path`:
                        # https://click.palletsprojects.com/en/8.1.x/api/#click.open_file
                        click.open_file(filename),  # type: ignore
                        default_namespace=default_namespace,
                    )
                )
            except exceptions.InventoryError as err:
                _LOG.exception("Error parsing %s: %s", filename, err)
                sys.exit(ReturnCodes.INVALID_YAML.value)
    return resources


@click.group()
@click.pass_context
@click.option("-v", "--verbose", count=True, help="Be verbose")
def cli(ctx: click.Context, verbose: int) -> None:
    """Main CLI entrypoint."""
    _setup_logging(verbose)

    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    if sys.stdout.isatty():  # pragma: nocover
        ctx.obj["echo"] = click.echo_via_pager
        ctx.obj["json_kwargs"] = {"indent": 2}
    else:
        ctx.obj["echo"] = click.echo
        ctx.obj["json_kwargs"] = {}


@cli.command("inventory")
@click.pass_context
@click.option(
    "-o",
    "--output",
    help="Output format",
    default=OutputFormat.PLAIN.name.lower(),
    type=click.Choice([f.name.lower() for f in OutputFormat]),
)
@click.option(
    "--no-header",
    help="Suppress the header in the 'plain' output format",
    is_flag=True,
    default=False,
)
@click.option(
    "-n",
    "--namespace",
    help="Default namespace for all resources",
    default="default",
)
@click.option(
    "-R",
    "--recursive",
    help="Process the directory recursively",
    is_flag=True,
    default=False,
)
@click.argument(
    "filename",
    type=click.Path(exists=True, allow_dash=True, path_type=pathlib.Path),
    nargs=-1,
    required=True,
)
def _inventory(
    ctx: click.Context,
    output: str,
    no_header: bool,
    namespace: str,
    recursive: bool,
    filename: tuple[pathlib.Path, ...],
) -> None:
    resources = _get_resources(filename, namespace, recursive=recursive)

    fmt = OutputFormat[output.upper()]
    echo = ctx.obj["echo"]
    resources.sort()
    if fmt == OutputFormat.PLAIN:
        table_data = [
            (f"{r.namespace}/{r.name}", f"{r.api_version}:{r.kind}") for r in resources
        ]
        echo(
            tabulate.tabulate(
                table_data,
                headers=() if no_header else ("Namespace", "Kind"),
                tablefmt="plain" if no_header else "simple",
            )
        )
    else:
        data = [dataclasses.asdict(r) for r in resources]
        if fmt == OutputFormat.JSON:
            echo(json.dumps(data, **ctx.obj["json_kwargs"]))
        if fmt == OutputFormat.YAML:
            yaml.YAML().dump(data, stream=click.get_text_stream("stdout"))


@cli.command("wait")
@click.pass_context
@click.option(
    "-o",
    "--output",
    help="Output format",
    default=OutputFormat.PLAIN.name.lower(),
    type=click.Choice([f.name.lower() for f in OutputFormat]),
)
@click.option(
    "-n",
    "--namespace",
    help="Default namespace for all resources",
    default="default",
)
@click.option(
    "-i",
    "--poll-interval",
    type=float,
    help="How often, in seconds, to poll for updates. Increase for large numbers of resources.",
    default=5.0,
)
@click.option(
    "-t",
    "--timeout",
    type=float,
    help="Timeout for all resources to be ready, in seconds",
    default=600.0,
)
@click.option(
    "-R",
    "--recursive",
    help="Process the directory recursively",
    is_flag=True,
    default=False,
)
@click.argument(
    "filename",
    type=click.Path(exists=True, allow_dash=True, path_type=pathlib.Path),
    nargs=-1,
    required=True,
)
def _wait(
    ctx: click.Context,
    output: str,
    namespace: str,
    poll_interval: float,
    timeout: float,
    recursive: bool,
    filename: tuple[pathlib.Path, ...],
) -> None:
    resources = _get_resources(filename, namespace, recursive=recursive)

    fmt = OutputFormat[output.upper()]
    if sys.stdout.isatty() and fmt == OutputFormat.PLAIN:  # pragma:nocover
        _fancy_wait(resources, poll_interval, timeout)
    else:
        echo = ctx.obj["echo"]
        is_timeout = False
        resource_states = {}
        try:
            for update in wait.wait_for(
                resources, interval=poll_interval, timeout=timeout
            ):
                if fmt == OutputFormat.JSON:
                    echo(
                        json.dumps(dataclasses.asdict(update), **ctx.obj["json_kwargs"])
                    )
                elif fmt == OutputFormat.YAML:
                    yaml.YAML().dump(
                        dataclasses.asdict(update),
                        stream=click.get_text_stream("stdout"),
                    )
                    echo("---")
                elif update.is_ready:
                    echo(f"{update.resource}: {_ok(update.state)}")
                else:
                    echo(f"{update.resource}: {_warn(update.state)}")
                resource_states[update.resource] = update.is_ready
        except TimeoutError as err:
            _LOG.error(err)
            is_timeout = True

        if fmt == OutputFormat.PLAIN:
            echo("")
            echo("SUMMARY:")
            echo("")

            table_data = [
                [
                    resource.kind,
                    f"{resource.namespace}/{resource.name}",
                    _ok("Yes") if state else _err("No"),
                ]
                for resource, state in resource_states.items()
            ]

            echo(tabulate.tabulate(table_data, headers=("Kind", "Resource", "Ready")))
        else:
            data = [
                {"resource": dataclasses.asdict(k), "is_ready": v}
                for k, v in resource_states.items()
            ]
            if fmt == OutputFormat.JSON:
                echo(json.dumps(data, **ctx.obj["json_kwargs"]))
            elif fmt == OutputFormat.YAML:
                yaml.YAML().dump(data, stream=click.get_text_stream("stdout"))

        if is_timeout:
            sys.exit(ReturnCodes.TIMEOUT.value)


def _fancy_wait(
    resources: list[inventory.ResourceDescriptor], interval: float, timeout: float
) -> None:  # pragma:nocover
    """When stdout is a TTY and output type is `plain`, do ANSI magic.

    This creates a magically updating table of the resources, with
    colors and other cool stuff.
    """
    resource_output_map = {}
    try:
        for update in wait.wait_for(resources, interval=interval, timeout=timeout):
            if update.is_ready:
                msg = f"{update.resource}: {_ok(update.state)}"
            else:
                msg = f"{update.resource}: {_warn(update.state)}"
            max_row = max(resource_output_map.values()) if resource_output_map else -1
            if (row := resource_output_map.get(update.resource)) is None:
                row = resource_output_map[update.resource] = max_row + 1
                click.echo(msg)
            else:
                # we have already printed a row for this
                # resource. find the row and update it.
                move_up = max_row - row + 1
                click.echo(
                    f"\u001b[{move_up}F\u001b[2K{msg}\u001b[{move_up}E", nl=False
                )
    except TimeoutError as err:
        _LOG.error(err)
        sys.exit(ReturnCodes.TIMEOUT.value)
