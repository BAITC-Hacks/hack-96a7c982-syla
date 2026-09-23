"""Review-only campaign copy derived from tariff facts, never from uplift estimates.

Templates deliberately make no claim about savings, speed, suitability or campaign
performance. This dashboard-only module does not send messages or call an LLM.
"""

from decimal import Decimal, InvalidOperation
from numbers import Real


GENERATOR = "templates-v1"
CHANNELS = frozenset(("sms", "push", "digital_ads", "call"))
FACT_FIELDS = (
    "current_price", "target_price", "current_data_gb", "target_data_gb",
)
REVIEW_WARNINGS = (
    "Демонстрационный черновик для симуляции, не действующее предложение оператора.",
    "Требует проверки маркетологом: подтвердите параметры и условия тарифа перед публикацией.",
    "Цена — абонентская плата в у.е./мес., а не прогноз полного счёта клиента. Текст не отправлен.",
)


def _number(value):
    """Accept actual numeric facts only; booleans and numeric-looking strings are not facts."""
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _amount(number, minimum_decimals=0):
    """Keep significant input decimals, including sub-GB packages and sub-unit fees."""
    value = format(number, "f")
    integer, _, fractional = value.partition(".")
    fractional = fractional.rstrip("0").ljust(minimum_decimals, "0")
    # A minus sign on Decimal('-0.0') is not a meaningful negative tariff price.
    if number == 0:
        integer = "0"
    groups = []
    while integer:
        groups.append(integer[-3:])
        integer = integer[:-3]
    integer = " ".join(reversed(groups))
    return integer + ("," + fractional if fractional else "")


def generate_creatives(target, channel, tariff_context):
    """Return three deterministic, review-only drafts or fail closed on invalid facts.

    The returned mapping is JSON-safe. IDs and descriptions are plain text; a UI
    must render them with textContent/escaping, never as HTML. The original target
    ID is preserved except for whitespace normalization. Extra context (segment,
    ARPU, lift, predictions) is intentionally ignored.
    """
    selected_channel = channel if isinstance(channel, str) else ""
    result = {
        "generator": GENERATOR,
        "requires_review": True,
        "channel": selected_channel,
        "variants": [],
        "warnings": list(REVIEW_WARNINGS),
    }
    if selected_channel not in CHANNELS:
        result["warnings"].append("Канал не поддерживается: черновики не сформированы.")
        return result
    if not isinstance(target, str) or not target.strip():
        result["warnings"].append("Не указан целевой тариф: черновики не сформированы.")
        return result
    target = " ".join(target.split())
    if not isinstance(tariff_context, dict):
        result["warnings"].append("Нет проверенных параметров тарифа: черновики не сформированы.")
        return result
    facts = {key: _number(tariff_context.get(key)) for key in FACT_FIELDS}
    invalid = [key for key, number in facts.items() if number is None]
    if invalid:
        result["warnings"].append(
            "Некорректные параметры (нужны конечные числа не меньше нуля): "
            + ", ".join(invalid) + ". Черновики не сформированы."
        )
        return result

    price = _amount(facts["target_price"], minimum_decimals=2) + " у.е./мес."
    data = facts["target_data_gb"]
    data_text = _amount(data) + " ГБ в пакете" if data > 0 else ""
    terms = price + ("; " + data_text if data_text else "")
    terms_sentence = terms.rstrip(".") + "."
    explanation = "Абонентская плата — " + price
    if data_text:
        explanation += " Интернет: " + data_text + "."

    if selected_channel == "sms":
        drafts = (
            ("Кратко", "Параметры тарифа", f"Тариф {target}: {terms_sentence} Ознакомьтесь с полными условиями тарифа."),
            ("Приглашение", "Предложение к рассмотрению", f"Рассмотрите тариф {target}. {explanation} Проверьте условия перехода."),
            ("Нейтрально", "Информация о тарифе", f"Параметры {target}: {terms_sentence} Подробности — в условиях тарифа."),
        )
    elif selected_channel == "push":
        drafts = (
            ("Параметры", f"Тариф {target}", f"{explanation} Ознакомьтесь с полными условиями."),
            ("Приглашение", "Рассмотрите тариф", f"{target}: {terms_sentence} Проверьте условия перехода."),
            ("Нейтрально", "Изучите параметры тарифа", f"Тариф {target}. {explanation} Подробности — в условиях тарифа."),
        )
    elif selected_channel == "digital_ads":
        drafts = (
            ("Параметры", f"Знакомьтесь: {target}", f"{explanation} Изучите полные условия тарифа."),
            ("Приглашение", "Тариф к рассмотрению", f"{target}: {terms_sentence} Узнайте условия перехода."),
            ("Нейтрально", f"Параметры тарифа {target}", f"{explanation} Условия и дополнительные платежи уточняйте перед подключением."),
        )
    else:
        drafts = (
            ("Параметры", f"Разговор о тарифе {target}",
             f"1. Уточните, удобно ли обсудить тариф {target}.\n"
             f"2. Сообщите параметры: {terms_sentence}\n"
             "3. Спросите, подходят ли параметры; не обещайте экономию полного счёта.\n"
             "4. Предложите изучить полные условия; запросите согласие на дальнейшие действия."),
            ("Потребности", f"Обсуждение {target}",
             "1. Уточните потребности клиента, не делая предположений по сегменту.\n"
             f"2. Представьте тариф {target}: {terms_sentence}\n"
             "3. На вопрос о выгоде предложите сверить полный счёт и условия.\n"
             "4. Уточните интерес и предложите ознакомиться с условиями перехода."),
            ("Нейтрально", f"Справка о тарифе {target}",
             "1. Спросите разрешение рассказать о параметрах тарифа.\n"
             f"2. Тариф {target}: {terms_sentence}\n"
             "3. Уточните вопросы; неизвестные условия не додумывайте.\n"
             "4. Предложите проверить полные условия перед решением о переходе."),
        )

    result["variants"] = [
        {
            "id": f"{selected_channel}-{index}",
            "label": label,
            "headline": headline,
            "body": body,
            "copy_text": body if selected_channel == "sms" else headline + "\n" + body,
        }
        for index, (label, headline, body) in enumerate(drafts, start=1)
    ]
    return result
