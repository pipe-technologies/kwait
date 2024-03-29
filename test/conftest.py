"""Global fixtures and test config."""

import logging

import faker
import pytest


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
