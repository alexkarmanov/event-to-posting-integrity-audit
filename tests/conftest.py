import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from stocktrace import engine, loader  # noqa: E402


def path(*p):
    return os.path.join(ROOT, *p)


@pytest.fixture(scope="session")
def ds_demo():
    return loader.load_dataset(path("data", "raw", "demo"), "demo")


@pytest.fixture(scope="session")
def ds_clean():
    return loader.load_dataset(path("data", "fixtures", "clean"), "clean")


@pytest.fixture(scope="session")
def ds_boundary():
    return loader.load_dataset(path("data", "fixtures", "boundary"), "boundary")


@pytest.fixture(scope="session")
def res_demo(ds_demo):
    return engine.run(ds_demo)


@pytest.fixture(scope="session")
def res_clean(ds_clean):
    return engine.run(ds_clean)


@pytest.fixture(scope="session")
def res_boundary(ds_boundary):
    return engine.run(ds_boundary)


@pytest.fixture(scope="session")
def built_demo(tmp_path_factory, ds_demo, res_demo):
    from stocktrace import cli
    out = str(tmp_path_factory.mktemp("print") / "demo")
    cli.write_outputs(res_demo, ds_demo, out, False, "demo")
    return out
