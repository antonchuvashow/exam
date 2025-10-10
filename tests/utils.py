from sentence_transformers import SentenceTransformer, util
import torch
import os
from django.conf import settings

_model = SentenceTransformer(settings.MODEL_PATH)


def semantic_similarity(a: str, b: str) -> float:
    """Вычисляет семантическое сходство между двумя строками (0..1)."""
    if not a or not b:
        return 0.0

    emb1 = _model.encode(a, convert_to_tensor=True)
    emb2 = _model.encode(b, convert_to_tensor=True)
    sim = util.cos_sim(emb1, emb2)
    return float(sim.item())
