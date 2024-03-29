"""Main CLI entrypoint.

CLI return codes:

1: Argument parsing failed
2: Timeout exceeded waiting for resources to become ready
"""

import dataclasses
import enum
import json
import logging
import sys
from typing import TextIO

import click
import tabulate
import yaml

from kwait import inventory
from kwait import wait

_LOG = logging.getLogger(__name__)


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


@click.group()
@click.pass_context
@click.option("-v", "--verbose", count=True, help="Be verbose")
def cli(ctx: click.Context, verbose: int) -> None:
    """Main CLI entrypoint."""
    _setup_logging(verbose)

    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    if sys.stdout.isatty():
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
    "-n",
    "--namespace",
    help="Default namespace for all resources",
    default="default",
)
@click.argument(
    "filename",
    type=click.File(),
    nargs=-1,
    required=True,
)
def _inventory(
    ctx: click.Context,
    output: str,
    namespace: str,
    filename: tuple[TextIO, ...],
) -> None:
    resources = []
    for filehandle in filename:
        resources.extend(inventory.get_resources(filehandle, namespace=namespace))

    fmt = OutputFormat[output.upper()]
    echo = ctx.obj["echo"]
    resources.sort()
    if fmt == OutputFormat.PLAIN:
        echo("\n".join(str(r) for r in resources))
    else:
        data = [dataclasses.asdict(r) for r in resources]
        if fmt == OutputFormat.JSON:
            echo(json.dumps(data, **ctx.obj["json_kwargs"]))
        if fmt == OutputFormat.YAML:
            echo(yaml.dump(data))


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
@click.argument(
    "filename",
    type=click.File(),
    nargs=-1,
    required=True,
)
def _wait(
    ctx: click.Context,
    output: str,
    namespace: str,
    poll_interval: float,
    timeout: float,
    filename: tuple[TextIO, ...],
) -> None:
    resources = []
    for filehandle in filename:
        resources.extend(inventory.get_resources(filehandle, namespace=namespace))

    fmt = OutputFormat[output.upper()]
    if sys.stdout.isatty() and fmt == OutputFormat.PLAIN:
        resource_output_map = {}
        try:
            for update in wait.wait_for(
                resources, interval=poll_interval, timeout=timeout
            ):
                if update.is_ready:
                    msg = f"{update.resource}: {_ok(update.state)}"
                else:
                    msg = f"{update.resource}: {_warn(update.state)}"
                max_row = (
                    max(resource_output_map.values()) if resource_output_map else -1
                )
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
            sys.exit(2)

    else:
        echo = ctx.obj["echo"]
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
                    echo(yaml.dump(dataclasses.asdict(update)))
                elif update.is_ready:
                    echo(f"{update.resource}: {_ok(update.state)}")
                else:
                    echo(f"{update.resource}: {_warn(update.state)}")
                resource_states[update.resource] = update.is_ready
        except TimeoutError as err:
            _LOG.error(err)
            sys.exit(2)

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
            data = [{"resource": k, "is_ready": v} for k, v in resource_states.items()]
            if fmt == OutputFormat.JSON:
                echo(json.dumps(data, **ctx.obj["json_kwargs"]))
            elif fmt == OutputFormat.YAML:
                echo(yaml.dump(data))
