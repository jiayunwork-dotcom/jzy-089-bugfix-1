import os
import sys

# 让 pytest 不依赖安装即可 import app.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.modeling.loader import load_sample_model
from app.sqlgen import (FilterSpec, GroupEntry, QuerySpec, SqlGenerator,
                        apply_drill)


@pytest.fixture(scope="session")
def model():
    return load_sample_model()


@pytest.fixture()
def gen(model):
    return SqlGenerator(model)


def q(rows=(), cols=(), measures=(), filters=(), limit=200) -> QuerySpec:
    return QuerySpec(
        rows=[GroupEntry(dimension=d) for d in rows],
        columns=[GroupEntry(dimension=d) for d in cols],
        measures=list(measures),
        filters=list(filters),
        limit=limit,
    )
