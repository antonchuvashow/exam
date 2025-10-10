import os
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer
from django.conf import settings

MODEL_ONNX_PATH = settings.MODEL_PATH
TOKENIZER_NAME = "deepvk/USER2-base"

# Загружаем токенизатор и ONNX сессию один раз при импорте
_tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_NAME)
_session = ort.InferenceSession(MODEL_ONNX_PATH, providers=["CPUExecutionProvider"])


def _mean_pooling(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """
    last_hidden_state: (batch, seq_len, hidden)
    attention_mask: (batch, seq_len)  (ints)
    Возвращает (batch, hidden) — mean pooling с учётом маски, нормализованный L2.
    """
    mask = attention_mask.astype(np.float32)
    mask_expanded = mask[:, :, None]
    summed = (last_hidden_state * mask_expanded).sum(axis=1)
    counts = mask.sum(axis=1)[:, None]
    counts[counts == 0] = 1.0
    mean_emb = summed / counts
    # L2-нормализация
    norms = np.linalg.norm(mean_emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mean_emb = mean_emb / norms
    return mean_emb


def _encode(texts):
    """
    texts: str или list[str]
    Возвращает: np.ndarray формы (hidden,) для str или (batch, hidden) для list.
    """
    single = False
    if isinstance(texts, str):
        texts = [texts]
        single = True

    # Токенизация -> numpy
    enc = _tokenizer(
        texts,
        return_tensors="np",
        padding=True,
        truncation=True,
        max_length=512,
    )

    # Подготовка входов для onnx (ключи должны совпадать с именами входов модели)
    ort_inputs = {}
    for k, v in enc.items():
        # onnxruntime обычно ожидает int64 для input_ids / attention_mask / token_type_ids
        if v.dtype != np.int64:
            v = v.astype(np.int64)
        ort_inputs[k] = v

    # Выполняем инференс
    outputs = _session.run(None, ort_inputs)
    # Ожидаем, что первый выход — last_hidden_state (batch, seq_len, hidden)
    last_hidden = outputs[0]

    # Убедимся, что shapes совместимы
    if last_hidden.ndim != 3:
        # Если модель возвращает уже pooled embeddings, просто используем их:
        emb = np.asarray(last_hidden)
        # приводим к нужной форме (batch, hidden)
        if emb.ndim == 2:
            pooled = emb
        else:
            raise RuntimeError(f"Unexpected ONNX output shape: {emb.shape}")
    else:
        pooled = _mean_pooling(last_hidden, ort_inputs["attention_mask"])

    return pooled[0] if single else pooled


def semantic_similarity(a: str, b: str) -> float:
    """Вычисляет семантическое сходство между двумя строками"""
    if not a or not b:
        return 0.0

    emb1 = _encode(a)
    emb2 = _encode(b)

    sim = float(np.dot(emb1, emb2))
    sim = max(min(sim, 1.0), 0)
    return sim
