"""样例模型加载。"""
from __future__ import annotations

import json
import os
from typing import Any

from .schema import ModelSpec

_SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "sample_model.json")


def model_from_dict(data: dict[str, Any]) -> ModelSpec:
    return ModelSpec.model_validate(data)


def load_sample_model() -> ModelSpec:
    with open(_SAMPLE_PATH, "r", encoding="utf-8") as f:
        return model_from_dict(json.load(f))
