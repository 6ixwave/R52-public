"""Ошибки с устойчивыми кодами.

Код виден в API, журнале и тестах. Текст написан для человека.
"""

MESSAGES = {
    "AUTOCOLLECT_DISABLED": "Включите автосбор и приём файлов в настройках",
    "SOURCE_ERRORS": "Часть источников не вернула данные",
    "AUTH_REQUIRED": "Нужен токен ядра или вход через окно R-52",
    "HOST_REFUSED": "Запрос пришёл не на локальный адрес ядра",
    "ORIGIN_REFUSED": "Запрос пришёл с чужой страницы",
    "TOKEN_REQUIRED": "[REDACTED_AUTH_SECRET]",
    "UI_HEADER_REQUIRED": "Запрос окна R-52 пришёл без служебного заголовка",
    "OPEN_FAILED": "Система не смогла открыть папку или ссылку",
    "BODY_TOO_LARGE": "Слишком большой запрос",
    "JSON_REQUIRED": "Нужен JSON объект",
    "LENGTH_REQUIRED": "Нужен заголовок Content-Length",
    "NOT_FOUND": "Не найдено",
    "METHOD_NOT_ALLOWED": "Метод не поддерживается",
    "INVALID_INPUT": "Неверные данные",
    "INTERNAL_ERROR": "Внутренняя ошибка ядра, подробности в журнале",
    "PRODUCT_NOT_FOUND": "Товар не найден",
    "TASK_NOT_FOUND": "Задача не найдена",
    "UNSUPPORTED_TASK": "Такой тип задачи не поддерживается",
    "IDEMPOTENCY_CONFLICT": "Ключ повтора уже использован с другими параметрами",
    "QUEUE_FULL": "Очередь задач заполнена",
    "INVALID_TASK_TRANSITION": "Недопустимый переход состояния задачи",
    "TASK_NOT_CANCELLABLE": "Задачу уже нельзя отменить",
    "TASK_CANCELLED": "Задача отменена",
    "LOCAL_TASK_DEADLINE": "Задача не уложилась в отведённое время",
    "SOURCE_NOT_IMPLEMENTED": "Сбор с этого источника пока не реализован",
    "AWARE_TIME_REQUIRED": "Нужно время с часовым поясом",
    "EXPORT_FORMAT_UNSUPPORTED": "Это не выгрузка Eye",
    "MANUAL_CAPTURE_REQUIRED": "Принимаются только выгрузки, сделанные нажатием в Eye",
    "EXPORT_TOO_LARGE": "Файл выгрузки пустой или слишком большой",
    "EXPORT_NOT_JSON": "Файл выгрузки не является JSON",
    "PUBLIC_SOURCE_URL_REQUIRED": "Нужна публичная https ссылка на карточку поддерживаемого источника",
    "PRIVATE_PATH_BLOCKED": "Ссылка ведёт в личный кабинет, корзину или оплату",
    "CARD_IDENTITY_NOT_CONFIRMED": "Номер карточки не совпадает со ссылкой",
    "OBSERVATION_TIME_REQUIRED": "В выгрузке нет времени наблюдения с часовым поясом",
    "TITLE_WITH_EVIDENCE_REQUIRED": "В выгрузке нет названия с доказательством",
    "UNSUPPORTED_FIELD_VALUE": "В выгрузке есть значение неподдерживаемого вида",
    "INBOX_ITEM_NOT_FOUND": "Элемент входящих не найден",
    "INBOX_ITEM_DECIDED": "Решение по этому элементу уже принято",
    "WATCH_NOT_FOUND": "Слежение не найдено",
    "GROUP_NOT_FOUND": "Группа сравнения не найдена",
    "SETTING_UNKNOWN": "Неизвестная настройка",
    "SETTING_INVALID": "Неверное значение настройки",
    "SCHEDULE_NOT_FOUND": "Расписание не найдено",
    "SESSION_NOT_FOUND": "Разговор не найден",
    "ACTION_NOT_FOUND": "Предложенное действие не найдено или устарело",
    "TEXT_REQUIRED": "Введите от 1 до 500 символов",
    "NOTIFICATION_NOT_FOUND": "Уведомление не найдено",
    "SCHEMA_TOO_NEW": "База создана более новой версией R-52",
    "MIGRATION_CHECKSUM_MISMATCH": "Применённая миграция отличается от кода",
    "SQLITE_TOO_OLD": "Версия SQLite слишком старая, нужна 3.37 или новее",
    "DB_WAL_UNAVAILABLE": "Не удалось включить журнал WAL для базы",
    "NESTED_WRITE": "Вложенная запись в базу запрещена",
    "CORE_ALREADY_RUNNING": "Ядро R-52 уже запущено для этой папки данных",
    "CORE_NOT_RUNNING": "Ядро R-52 не запущено",
    "WRONG_SERVICE": "По адресу ядра отвечает другая программа",
    "CORE_MUST_BE_STOPPED": "Остановите R-52 перед этой операцией",
    "BACKUP_EXISTS": "Резервная копия с таким именем уже есть",
    "BACKUP_VERIFY_FAILED": "Резервная копия не прошла проверку",
    "BACKUP_NOT_FOUND": "Резервная копия не найдена",
    "CONFIG_INVALID": "Ошибка в config.toml",
    "LEGACY_DB_NOT_FOUND": "Старая база не найдена",
    "LEGACY_DB_UNSUPPORTED": "Старая база не похожа на ожидаемую",
    "NETWORK_BLOCKED": "Ядру разрешены только локальные соединения",
    "LLM_UNAVAILABLE": "Локальная модель недоступна",
}


class AppError(Exception):
    """Ожидаемая ошибка операции с кодом, HTTP статусом и деталями."""

    def __init__(self, code, message=None, status=400, detail=None):
        super().__init__(code)
        self.code = code
        self.message = message or MESSAGES.get(code, code)
        self.status = status
        self.detail = detail

    def as_dict(self):
        data = {"code": self.code, "message": self.message}
        if self.detail is not None:
            data["detail"] = self.detail
        return data
