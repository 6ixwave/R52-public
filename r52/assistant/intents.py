"""Разбор запроса пользователя на намерения. Правила простые и предсказуемые."""

import re
from dataclasses import dataclass, field

from ..money import parse_minor


@dataclass
class Intent:
    name: str
    query: str = ""
    other: str = ""
    amount_minor: int = None
    percent: int = None
    extra: dict = field(default_factory=dict)


_FLAGS = re.IGNORECASE
GREETING = re.compile(r"^(?:привет|здравствуй(?:те)?|добрый (?:день|вечер|утро)|доброе утро|hello|hi)[!?. ]*$", _FLAGS)
IDENTITY = re.compile(r"^(?:ты\s+(?:ии|бот|нейросеть|искусственный интеллект)|кто ты|что ты такое|какая (?:ты )?модель|какая у тебя модель)[!?. ]*$", _FLAGS)
HELP = re.compile(r"^(?:помощь|help|что ты умеешь|команды|\?)\s*[?!.]*$", _FLAGS)
STATUS = re.compile(r"^(?:статус|состояние|как дела|всё работает|все работает)\s*[?!.]*$", _FLAGS)
NEWS = re.compile(r"^(?:что нового|новости|что случилось|сводка|что изменилось)\s*[?!.]*$", _FLAGS)
INBOX = re.compile(r"^(?:входящие|что во входящих|что пришло)\s*[?!.]*$", _FLAGS)
FAVORITES = re.compile(r"^(?:избранное|моё избранное|мое избранное|покажи избранное)\s*[?!.]*$", _FLAGS)
WATCHES = re.compile(r"^(?:слежение|за чем ты следишь|что отслеживаешь|мои слежения)\s*[?!.]*$", _FLAGS)
COMPARE_FAVORITES = re.compile(r"^сравни\s+избранное\s*[?!.]*$", _FLAGS)
WATCH = re.compile(
    r"^(?:следи|отслеживай|отслежи|предупреди)\s+(?:за\s+)?(?:ценой\s+)?(?P<query>.+?)"
    r"(?:\s+(?:до|ниже|дешевле|когда будет)\s+(?P<amount>\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб\w*|р\.?|тыс\w*|k|к)?"
    r"|\s+(?:на|если подешевеет на)\s+(?P<percent>\d{1,2})\s*%)?\s*[?!.]*$",
    _FLAGS,
)
HISTORY = re.compile(r"^(?:история(?:\s+цен)?|цены\s+на|динамика|как менялась цена)\s+(?P<query>.+?)\s*[?!.]*$", _FLAGS)
COMPARE = re.compile(r"^сравни\s+(?P<a>.+?)\s+(?:и|с|vs|против)\s+(?P<b>.+?)\s*[?!.]*$", _FLAGS)
FAVORITE_ADD = re.compile(r"^(?:добавь|сохрани)\s+(?P<query>.+?)\s+в\s+избранное\s*[?!.]*$", _FLAGS)
SEARCH = re.compile(r"^(?:найди|поиск|покажи|есть ли|сколько стоит|цена|цены)\s+(?P<query>.+?)\s*[?!.]*$", _FLAGS)


def _amount(text):
    if not text:
        return None
    compact = re.sub(r"[\s\u00a0]", "", text)
    return parse_minor(compact)


def parse(text):
    text = " ".join(str(text).split())
    for pattern, name in ((GREETING, "greeting"), (IDENTITY, "identity"), (HELP, "help"), (STATUS, "status"), (NEWS, "news"), (INBOX, "inbox"),
                          (FAVORITES, "favorites"), (WATCHES, "watches"), (COMPARE_FAVORITES, "compare_favorites")):
        if pattern.match(text):
            return Intent(name)
    match = WATCH.match(text)
    if match:
        amount = _amount(match.group("amount"))
        if amount is not None and re.search(r"(?:тыс\w*|k|к)\s*[?!.]*$", text, _FLAGS):
            amount *= 1000
        percent = int(match.group("percent")) if match.group("percent") else None
        return Intent("watch", query=match.group("query"), amount_minor=amount, percent=percent)
    match = HISTORY.match(text)
    if match:
        return Intent("history", query=match.group("query"))
    match = COMPARE.match(text)
    if match:
        return Intent("compare", query=match.group("a"), other=match.group("b"))
    match = FAVORITE_ADD.match(text)
    if match:
        return Intent("favorite_add", query=match.group("query"))
    match = SEARCH.match(text)
    if match:
        return Intent("search", query=match.group("query"))
    return Intent("free", query=text)
