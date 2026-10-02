from pathlib import Path

import pytest

from notes2gdoc.config import Settings
from notes2gdoc.parsers import parse_file

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "Samples"
GOLDEN = Path(__file__).resolve().parent / "golden"

SYLLABUS = SAMPLES / "Syllabus.pdf"
SLIDES = SAMPLES / "6.%20Cross-Border%20Insolvency.pdf"
MA = SAMPLES / "1_%20Introduction%20to%20Mergers%20%26%20Acquisitions%20for%20P.pdf"
DIVORCE = next(SAMPLES.glob("2.*Divorce*.pdf"), None)


def need(path) -> None:
    """Skip the test if a course sample PDF (or its expected output) isn't here.

    The samples are copyrighted course materials, so they're kept out of the
    public GitHub project; on GitHub only the tests that don't need them run.
    """
    if path is None or not Path(path).exists():
        pytest.skip("course sample files aren't available here")


def parse_sample(path, settings=None):
    need(path)
    return parse_file(path, settings or Settings())


@pytest.fixture(scope="session")
def settings():
    # Defaults only - never the user's saved settings file
    return Settings()


@pytest.fixture(scope="session")
def syllabus(settings):
    return parse_sample(SYLLABUS, settings)


@pytest.fixture(scope="session")
def slides(settings):
    return parse_sample(SLIDES, settings)


@pytest.fixture(scope="session")
def ma(settings):
    return parse_sample(MA, settings)


@pytest.fixture(scope="session")
def divorce(settings):
    return parse_sample(DIVORCE, settings)


def find(doc, text, kind=None):
    """First block whose text contains `text` (optionally of a given kind)."""
    for b in doc.blocks:
        if text in b.text and (kind is None or b.kind == kind):
            return b
    raise AssertionError(f"no block containing {text!r}")
