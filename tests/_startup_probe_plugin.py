"""Records which heavy libraries a pytest session has imported by collection time; loaded by test_pytest_startup_is_light.py."""

import os
import sys

HEAVY = ("thinc", "torch", "cupy")


def pytest_collection_finish(session):
    loaded = sorted(name for name in HEAVY if name in sys.modules)
    with open(os.environ["PCS_PROBE_OUT"], "w", encoding="utf-8") as f:
        f.write(",".join(loaded))
