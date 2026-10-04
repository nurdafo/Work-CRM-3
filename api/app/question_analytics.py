"""Question breakdowns from the answers actually submitted through Meta forms."""
from collections import Counter

import re
import unicodedata

from .models import Lead
from .schemas import QuestionAnswerOut, QuestionAnalyticsOut


CONTACT_FIELDS = {
    "full_name", "first_name", "last_name", "phone_number", "email",
    "work_email", "work_phone_number", "mobile_phone_number", "home_phone_number",
    "company_name", "city", "state", "country", "zip_code", "street_address",
    "date_of_birth", "gender",
    "имя", "фамилия", "полное имя", "телефон", "номер телефона",
    "электронная почта", "почта", "компания", "название компании", "город", "адрес",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[‐‑‒–—−]", "-", value)
    value = re.sub(r"\s+", "", value.replace("_", " "))
    return re.sub(r"[?.!,]", "", value)


CONTACT_KEYS = {normalize(field) for field in CONTACT_FIELDS}


def display_label(value: str) -> str:
    """Turn Meta's underscore-separated field names into readable labels."""
    label = re.sub(r"\s+", " ", value.replace("_", " ")).strip()
    return label[:1].upper() + label[1:]


def question_charts(leads: list[Lead], form_questions: list[dict] | None = None) -> list[QuestionAnalyticsOut]:
    questions: dict[str, dict] = {}
    for lead in leads:
        if lead.source != "META":
            continue
        fields = lead.form_answers if isinstance(lead.form_answers, list) else []
        seen: set[str] = set()
        for field in fields:
            if not isinstance(field, dict):
                continue
            raw_question = str(field.get("question") or "").strip()
            key = normalize(raw_question)
            if not key or key in seen or key in CONTACT_KEYS:
                continue
            answers = field.get("answers") or []
            if not isinstance(answers, list):
                continue
            answer = next((str(value).strip() for value in answers if value is not None and str(value).strip()), "")
            if not answer:
                continue
            seen.add(key)
            chart = questions.setdefault(key, {"label": display_label(raw_question), "counts": Counter(), "labels": {}})
            answer_key = normalize(answer)
            chart["counts"][answer_key] += 1
            chart["labels"].setdefault(answer_key, display_label(answer))

    if form_questions is not None:
        result: list[QuestionAnalyticsOut] = []
        for definition in form_questions:
            if not isinstance(definition, dict) or definition.get("type") != "CUSTOM":
                continue
            key = normalize(str(definition.get("key") or ""))
            label = str(definition.get("label") or "").strip()
            if not key or not label:
                continue
            chart = questions.get(key, {"counts": Counter(), "labels": {}})
            counts = chart["counts"]
            answered = sum(counts.values())
            options: list[QuestionAnswerOut] = []
            known: set[str] = set()
            for option in definition.get("options") or []:
                if not isinstance(option, dict):
                    continue
                answer_key = normalize(str(option.get("key") or ""))
                answer_label = str(option.get("value") or "").strip()
                if not answer_key or not answer_label or answer_key in known:
                    continue
                known.add(answer_key)
                count = counts[answer_key]
                options.append(QuestionAnswerOut(
                    label=answer_label, count=count,
                    percent=round(100 * count / answered, 1) if answered else 0,
                ))
            for answer_key in sorted(set(counts) - known, key=lambda item: (-counts[item], chart["labels"][item])):
                count = counts[answer_key]
                options.append(QuestionAnswerOut(
                    label=chart["labels"][answer_key], count=count,
                    percent=round(100 * count / answered, 1) if answered else 0,
                ))
            result.append(QuestionAnalyticsOut(question=label, answered=answered, options=options))
        return result

    result: list[QuestionAnalyticsOut] = []
    for chart in questions.values():
        counts = chart["counts"]
        answered = sum(counts.values())
        options = sorted(counts, key=lambda key: (-counts[key], chart["labels"][key]))
        result.append(QuestionAnalyticsOut(
            question=chart["label"],
            answered=answered,
            options=[QuestionAnswerOut(
                label=chart["labels"][key],
                count=counts[key],
                percent=round(100 * counts[key] / answered, 1),
            ) for key in options],
        ))
    return result
