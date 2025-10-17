import re
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

MODEL_ONNX_PATH = settings.MODEL_PATH
TOKENIZER_NAME = "deepvk/USER2-base"

# Загружаем токенизатор и ONNX сессию один раз при импорте
_tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_NAME)
_session = ort.InferenceSession(MODEL_ONNX_PATH, providers=["CPUExecutionProvider"])


_int_re = re.compile(r'[-+]?\d+(\.\d+)?')


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


# def semantic_similarity(a: str, b: str) -> float:
#     """Вычисляет семантическое сходство между двумя строками"""
#     if not a or not b:
#         return 0.0

#     emb1 = _encode(a)
#     emb2 = _encode(b)

#     sim = float(np.dot(emb1, emb2))
#     sim = max(min(sim, 1.0), 0)
#     return sim


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None:
        return 0.0
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def extract_numbers(text: str):
    return [float(m.group(0)) for m in _int_re.finditer(text or "")]

def split_sentences(text: str):
    # простая разделялка на предложения (достаточно для аспектов)
    if not text:
        return []
    parts = re.split(r'[.!?]\s*', text.strip())
    return [p.strip() for p in parts if p.strip()]

# --- Основная функция оценки ---
def score_open_answer(
    question_text: str,
    user_ans: str,
    correct_texts: list[str],
    incorrect_texts: list[str],
    embed_fn=_encode,
    points: float = 1.0,
    *,
    threshold: float = 0.65,
    full_credit_threshold: float = 0.92,
    incorrect_threshold: float = 0.92,
    penalty_weight: float = 1.0,
    correction_factor: float = 0.6,
    min_partial: float = 0.0,
    topk_incorrect: int = 3,
    aspect_weight: float = 0.5,
    length_penalty_min_ratio: float = 0.35
):
    """
    Возвращает (score: float) в диапазоне 0..points.
    Требует embed_fn(text)->np.ndarray (USER2-base).
    """
    user_ans = (user_ans or "").strip()
    if not user_ans:
        return 0.0

    # --- Соберём эмбеддинги (кэшируем повторно, если нужно) ---
    user_ctx = embed_fn(f"Q: {question_text}\nA: {user_ans}")
    user_nctx = embed_fn(user_ans)

    # correct embeddings
    corr_ctx_embs = embed_fn([f"Q: {question_text}\nA: {c.strip()}" for c in correct_texts if c and c.strip()])
    corr_nctx_embs = embed_fn([c.strip() for c in correct_texts if c and c.strip()])

    # incorrect embeddings
    inc_nctx_embs = embed_fn([inc.strip() for inc in incorrect_texts if inc and inc.strip()])

    # --- similarity measurements ---
    sim_user_correct_ctx = max((cosine(user_ctx, c) for c in corr_ctx_embs), default=0.0)
    sim_user_correct_nctx = max((cosine(user_nctx, c) for c in corr_nctx_embs), default=0.0)
    # комбинируем контекстную и неконтекстную меру (даёт преимущество контексту)
    sim_user_correct = 0.75 * sim_user_correct_ctx + 0.25 * sim_user_correct_nctx

    # неправильные: mean(top-k) по неконтекстной мере
    if inc_nctx_embs.any():
        sims_inc = sorted((cosine(user_nctx, inc) for inc in inc_nctx_embs), reverse=True)
        topk = sims_inc[:max(1, min(topk_incorrect, len(sims_inc)))]
        sim_user_incorrect_mean = float(sum(topk) / len(topk))
    else:
        sim_user_incorrect_mean = 0.0

    # насколько эталоны схожи с неверными (используется как коррекция)
    sim_correct_to_incorrect = 0.0
    if corr_nctx_embs.any() and inc_nctx_embs.any():
        sim_correct_to_incorrect = max(
            cosine(c, i) for c in corr_nctx_embs for i in inc_nctx_embs
        )

    # ---------- Hard rules ----------
    # слишком похож на явный неверный -> 0
    if sim_user_incorrect_mean >= incorrect_threshold:
        return 0.0

    # очень близко к эталону -> полный балл
    if sim_user_correct >= full_credit_threshold:
        return float(points)

    # разбиваем эталоны на предложения и смотрим покрытие
    aspects = []
    for c in correct_texts:
        aspects += split_sentences(c)
    aspects = [a for a in aspects if a]
    aspect_scores = []
    if aspects:
        aspects_ctx = [f"Q: {question_text}\nA: {asp}" for asp in aspects]
        asp_embs = embed_fn(aspects_ctx)
        for asp_emb in asp_embs:
            aspect_scores.append(cosine(user_ctx, asp_emb))
        # аспектный скор — средний из aspect_scores, но ограничим сверху 1.0
        aspect_score = float(sum(aspect_scores) / len(aspect_scores))
    else:
        aspect_score = 0.0

    # нормализация sim_user_correct относительно threshold
    denom = max(1e-6, 1.0 - threshold)
    norm_score = max(0.0, (sim_user_correct - threshold) / denom)  # 0..1

    # штраф за схожесть с неверными, скорректированный с учётом схожести эталонов с неверными
    adjusted_incorrect = max(0.0, sim_user_incorrect_mean - sim_correct_to_incorrect * correction_factor)
    penalty = min(1.0, adjusted_incorrect * penalty_weight)

    # длина ответа: короткие ответы получают штраф
    avg_correct_len = np.mean([len(c.split()) for c in correct_texts]) if correct_texts else 0
    user_len = len(user_ans.split())
    length_penalty = 1.0
    if avg_correct_len > 0:
        ratio = user_len / avg_correct_len
        if ratio < length_penalty_min_ratio:
            # линейный штраф при очень коротком ответе
            length_penalty = max(0.0, ratio / length_penalty_min_ratio)

    # объединяем факторы
    raw = norm_score * (1.0 - penalty)
    combined = (1.0 - aspect_weight) * raw + aspect_weight * aspect_score

    # length_penalty и min_partial
    final_factor = max(0.0, combined * length_penalty)
    if final_factor > 0 and min_partial > 0:
        final_factor = max(final_factor, min_partial)

    score = round(points * float(max(0.0, min(1.0, final_factor))), 2)
    logger.debug(
        f"[open_score] sim_user_correct={sim_user_correct:.3f}, sim_user_incorrect_mean={sim_user_incorrect_mean:.3f}, "
        f"sim_corr_inc={sim_correct_to_incorrect:.3f}, norm_score={norm_score:.3f}, penalty={penalty:.3f}, aspect={aspect_score:.3f}, "
        f"len_pen={length_penalty:.3f}, final={final_factor:.3f}, score={score}"
    )
    return score
