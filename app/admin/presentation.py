"""Отображение для владельца. Не меняет расчёты, права или точность данных в БД."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy.engine import make_url

LABELS = {
    "guest": "Гость сайта", "payg": "По использованию", "subscription": "Подписка",
    'user': 'Пользователь', 'admin': 'Администратор', 'service': 'Служебный',
    'account': 'Пользователь', 'plan': 'Тариф', 'token': 'Ключ доступа',
    'active': 'Активен', 'disabled': 'Заблокирован', 'archived': 'В архиве',
    'interrupted': 'Прервано · расход уточняется',
    'stream_interrupted': 'Поставщик прервал поток',
    'stream_timeout': 'Истекло время потока',
    'client_disconnected': 'Клиент отключился',
    'web': 'Сайт',
    'success': 'Успешно', 'failed': 'Ошибка', 'pending': 'В работе',
    'revoked': 'Отозван', 'cancelled': 'Остановлена',
    'harness': 'Десктоп / API-клиент', 'site': 'Сайт', 'telegram': 'Telegram',
    'api': 'API', 'bootstrap': 'Служебный доступ', 'mock': 'Тестовая модель',
    'venice': 'Venice', 'development': 'Разработка', 'production': 'Рабочий контур',
    'owner-check': 'Локальная проверка', 'test': 'Тестирование', 'testing': 'Тестирование',
    'paid': 'Платный', 'free': 'Бесплатный', 'text': 'Текст', 'image': 'Изображения',
    'audio': 'Аудио', 'private': 'Приватный', 'public': 'Публичный',
    'tokens': 'Токены', 'RPM': 'Запросов в минуту', 'TPM': 'Токенов в минуту',
    '7d': '7 дней', '30d': '30 дней', '1d': '1 день', 'LLM': 'Языковая модель',
    'request_limit_exceeded': 'Исчерпан лимит запросов',
    'credit_limit_exceeded': 'Недостаточно кредитов',
    'spend_limit_exceeded': 'Исчерпан бюджет на модели',
    'subscription_required': 'Нет активного тарифа',
    'provider_error': 'Ошибка поставщика модели',
    'provider_authentication_error': 'Поставщик не принял серверный ключ',
    'provider_balance_exhausted': 'Баланс поставщика недоступен',
    'provider_rate_limit': 'Поставщик ограничил частоту запросов',
    'reservation_expired': 'Время ожидания истекло',
}

HELP = {
    "account_name": (
        "Имя пользователя",
        "Отображаемое имя аккаунта в Shluz: в списках, расходах и профиле. "
        "Можно задать удобное имя, например «Мой аккаунт». "
        "Это не Telegram ID, не @username и не пароль; доступ и баланс не меняются.",
    ),
    "telegram_identity": (
        "Привязка Telegram",
        "Связь с подтверждённым числовым Telegram ID появляется при подключении бота "
        "или официальном входе через Telegram. Название аккаунта и выбор канала "
        "у ключа сами по себе не связывают его с человеком в Telegram.",
    ),
    "billing_mode": (
        "Способ оплаты",
        "Подписка: цена за месяц и включённая квота. PAYG: пополнение кошелька "
        "и списание за каждый запрос по себестоимости с наценкой, без абонентской платы. "
        "В PAYG месячные поля подписки не действуют, но хранятся для переключения обратно. "
        "Выбор режима не подключает платёжную систему и не пополняет баланс.",
    ),
    'requests': (
        'Запросы',
        'Одно обращение к модели. Месячная квота подписки учитывает успешные запросы и '
        'действующие резервы. При назначении тарифа отдельного канала его квота считается '
        'отдельно; общий тариф делит квоту между остальными каналами. Период — календарный '
        'месяц UTC. PAYG расходует кошелёк, а демо — свой разовый пакет.'
    ),
    'request_log': ('Журнал запросов', 'Здесь записи об обращениях к моделям: успешные, в работе '
                    'и завершившиеся ошибкой. Отказ до вызова модели, например из-за неверного '
                    'ключа или исчерпанной квоты, не создаёт такую запись. Поэтому HTTP 429 '
                    'при исчерпании тарифа не увеличивает счётчик ошибок поставщика.'),
    'price': ('Цена подписки', 'Справочная цена тарифа за месяц в долларах США. Сейчас она '
              'не списывается автоматически: приём платежей не подключён. Это не лимит '
              'расходов на модели и не подтверждённый доход.'),
    'cost': ('Расходы на модели', 'Себестоимость: сколько стоили обращения к поставщикам '
             'моделей по данным шлюза. Без вашей наценки. Это расходы сервиса, '
             'а не оплата подписок пользователями. У тестовой mock-модели они равны нулю.'),
    'cost_limit': ('Бюджет на модели', 'Максимальная себестоимость запросов одного аккаунта '
                   'за календарный месяц UTC, в USD. Например, 5 USD — бюджет сервиса на этого '
                   'пользователя, не цена его подписки. Пусто или 0 — без ограничения. '
                   'Проверяется до запроса; фактическая цена ответа может превысить резерв.'),
    'credits': (
        'Внутренние кредиты',
        'Здесь квотные кредиты подписки, а не токены модели и не деньги кошелька. Расход с '
        'наценкой переводится в них по серверному курсу с округлением вверх. Не путайте с '
        'отображением кошелька: там 100 кредитов = 1 USD, и это только другой вид той же '
        'денежной суммы.'
    ),
    'reserve': (
        'Резерв кредитов на запрос',
        'Временная бронь кредитов перед обращением к модели, не дополнительная плата. Из '
        '100 кредитов при резерве 10 временно доступно 90. После ответа бронь заменяется '
        'расходом; при однозначном отказе до генерации снимается. При неизвестном '
        'результате резерв сохраняется до сверки. 0 — без брони квотных кредитов. Это не '
        'предел длины или максимальной цены ответа. Для mock-проверки оставьте 0.'
    ),
    'markup': ('Наценка', 'Наценка к себестоимости поставщика для расчётного списания: '
               'при расходе 1 USD и наценке 20% получается 1,20 USD. Разница — 0,20 USD. '
               'Не меняет справочную месячную цену тарифа и не означает реальную оплату.'),
    'revenue': ('Расчётная выручка', 'Себестоимость с применённой наценкой. Это расчёт шлюза, '
                'не банковское поступление и не сумма оплаченных подписок. '
                'Подтверждённые платежи пока не учитываются.'),
    'margin': ('Расчётная маржа', 'Расчётная выручка минус себестоимость моделей. '
               'Это не чистая прибыль: здесь не вычтены сервер, комиссии, '
               'налоги и другие расходы.'),
    'model_tokens': ('Токены нейросети', 'Фрагменты текста, которыми модель измеряет объём. '
                     'Вход — запрос и переданная история; выход — сгенерированный ответ. '
                     'Это не API-ключ доступа. У mock-модели вход и выход всегда 0.'),
    'source': (
        'Канал доступа',
        'Откуда приходит обращение: Telegram, сайт или десктоп/API-клиент. Канал участвует '
        'в проверке доступных моделей и выбирает отдельный тариф, если он назначен в '
        '«Кошельках». PAYG-баланс общий для аккаунта. Выбор канала Telegram у личного ключа '
        'не привязывает аккаунт к Telegram ID; бот подключается автоматически.'
    ),
    'token_name': ('Название ключа', 'Необязательная подпись только для вас: например, '
                   '«Телефон» или «Telegram для проверки». Помогает различать и отзывать '
                   'ключи. Не пароль, не тариф и не разрешение. Оставьте пустым — '
                   'название создастся по каналу и дате.'),
    'api_token': ('Ключ доступа', 'Личный секретный ключ пользователя (API-токен). '
                  'Даёт приложениям доступ к Shluz, но не к админке. Полное значение '
                  'показывается один раз. Потерянный ключ нужно отозвать и выдать новый.'),
    'token_prefix': ('Префикс ключа', 'Несекретное начало ключа для сверки. По нему удобно '
                     'отличать доступы, но использовать его вместо полного ключа нельзя.'),
    'revoke': ('Отзыв доступа', 'Отозванный ключ перестанет проходить авторизацию. '
               'Другие ключи этого пользователя продолжат работать в рамках общей квоты. '
               'Отмена ключа не гарантирует остановку уже выполняющегося запроса.'),
    'plan_code': ('Код тарифа', 'Уникальное техническое имя тарифа для интеграций: латинские '
                  'буквы, цифры, дефис или подчёркивание. В интерфейсе показывается обычное '
                  'название. Код не пароль; в отличие от названия он не должен повторяться.'),
    'id': ('Служебный ID', 'Внутренний постоянный идентификатор записи. Нужен для поддержки '
           'и интеграций, не для ежедневной работы. Не является ключом доступа.'),
    'role': (
        'Роль аккаунта',
        'Внутренняя метка пользователя, не учётная запись входа владельца в эту панель. '
        'Логин и пароль панели задаются отдельно на сервере. Созданного вручную '
        'пользователя с меткой «Администратор» можно архивировать: ваш вход в панель '
        'сохранится. Служебный bootstrap защищён.'
    ),
    'identity': ('Способы входа', 'Связи аккаунта, например с Telegram ID. Отсутствие '
                 'привязки не мешает пользоваться активным личным ключом.'),
    'diem': ('DIEM', 'Отдельная единица баланса и расхода Venice. Не смешивается с USD '
             'и внутренними кредитами Shluz; автоматическое приравнивание не выполняется. '
             'Для mock и поставщиков без DIEM этот показатель не нужен.'),
    'database': ('База данных', 'SQLite хранит данные в файле на том компьютере или сервере, '
                 'где запущен шлюз. PostgreSQL — отдельная база; Supabase предоставляет '
                 'управляемый PostgreSQL. Тип определяется настройкой подключения. '
                 'Перезапуск не переносит данные в Supabase; для миграции нужен отдельный этап.'),
    'provider': ('Поставщик модели', 'Сервис, которому шлюз передаёт запросы. Ключ поставщика '
                 'остаётся на сервере. mock — искусственный бесплатный ответ для проверки '
                 'интеграции, не настоящая нейросеть.'),
    'dates': ('Время', 'Даты показаны в часовом поясе браузера, если JavaScript включён. '
              'Иначе — UTC. Месячные квоты всегда считаются по UTC. '
              'Сырой точный момент доступен при наведении на дату.'),
    'bootstrap': ('Служебный аккаунт', 'Технический доступ для первоначальной настройки '
                  'и тестов, не обычный пользователь. Не выдавайте его ключ клиентам '
                  'или Telegram-боту. Скрыт из основного списка, но его данные не удалены.'),
    'advanced': (
        'Расширенные настройки',
        'Бюджет, квотные кредиты и резерв — дополнительные ограничения подписки. Для '
        'простой проверки достаточно числа запросов. Сворачивание блока не сбрасывает '
        'значения. В PAYG параметры подписки не действуют, но сохраняются для переключения '
        'обратно.'
    ),
    'audit': ('Журнал действий', 'История изменений в админке: кто выдал или отозвал ключ, '
              'назначил тариф, изменил настройки. Это не журнал переписок. '
              'Скрытые технические данные доступны в каждой записи.'),
}


def _number(value: Any) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _localized(raw: str) -> str:
    negative = raw.startswith('-')
    whole, _, fraction = raw.lstrip('-').partition('.')
    grouped = f'{int(whole):,}'.replace(',', '\u00a0')
    return ('−' if negative else '') + grouped + (',' + fraction if fraction else '')


def money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return '—'
    if not number:
        return '0,00'
    if abs(number) < Decimal('0.01'):
        return _localized(format(number, 'f').rstrip('0').rstrip('.'))
    return _localized(format(number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), 'f'))


def precise(value: Any) -> str:
    number = _number(value)
    return _localized(format(number, 'f')) if number is not None else '—'


def form_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ''
    if not number:
        return '0'
    raw = format(number, 'f')
    return raw.rstrip('0').rstrip('.') if '.' in raw else raw


def percent(value: Any) -> str:
    raw = form_number(value)
    return _localized(raw) if raw else '—'


def integer(value: Any) -> str:
    number = _number(value)
    return f'{int(number):,}'.replace(',', '\u00a0') if number is not None else '—'


def label(value: Any) -> str:
    return LABELS.get(str(value), str(value) if value is not None else '—')


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def datetime_ru(value: Any) -> str:
    parsed = _datetime(value)
    return parsed.strftime('%d.%m.%Y, %H:%M:%S UTC') if parsed else 'Не указано'


def datetime_iso(value: Any) -> str:
    parsed = _datetime(value)
    return parsed.isoformat() if parsed else ''


def database_info(url: str) -> dict[str, str]:
    """Не отдавать хост, имя базы, URL, логин или пароль в шаблон."""
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    if backend == 'sqlite':
        memory = not parsed.database or parsed.database == ':memory:'
        return {
            'name': 'SQLite · в памяти' if memory else 'SQLite',
            'description': ('Временная база: данные исчезнут при остановке процесса.' if memory else
                            'Файл на сервере шлюза. Supabase не используется.'),
        }
    if backend == 'postgresql':
        host = (parsed.host or '').lower().rstrip('.')
        supabase = host.endswith(('.supabase.co', '.pooler.supabase.com'))
        return {
            'name': 'Supabase' if supabase else 'PostgreSQL',
            'description': ('PostgreSQL в Supabase по адресу подключения.' if supabase else
                            'Отдельная PostgreSQL. По этому адресу Supabase не определён.'),
        }
    return {'name': 'Другая база', 'description': 'Подключение настроено на сервере.'}


def install_presentation(environment: Any) -> None:
    environment.filters.update({
        'money': money, 'precise': precise, 'integer': integer, 'percent': percent,
        'form_number': form_number, 'label': label, 'datetime_ru': datetime_ru,
        'datetime_iso': datetime_iso, 'plural': plural,
    })
    environment.globals['audit_label'] = lambda key: AUDIT_LABELS.get(key, key)
    environment.globals['audit_value'] = audit_value
    environment.globals['help_text'] = lambda key: HELP[key][1]
    environment.globals['help_title'] = lambda key: HELP[key][0]


AUDIT_LABELS = {
    'code': 'Код тарифа', 'name': 'Название', 'display_name': 'Имя пользователя',
    'monthly_price_usd': 'Цена в месяц', 'monthly_cost_limit_usd': 'Бюджет на модели',
    'monthly_credit_limit': 'Кредитов в месяц', 'monthly_request_limit': 'Запросов в месяц',
    'request_credit_reserve': 'Резерв кредитов', 'markup_percent': 'Наценка',
    'active': 'Тариф активен', 'role': 'Роль', 'status': 'Статус', 'source': 'Канал',
    'plan_id': 'ID тарифа', 'account_id': 'ID пользователя',
    'cancelled_subscriptions': 'Остановлено подписок', 'revoked_tokens': 'Отозвано ключей',
}


def audit_value(key: str, value: Any) -> str:
    if key == 'active':
        return 'Да' if value else 'Нет'
    if value is None:
        return 'Без лимита' if key in {
            'monthly_cost_limit_usd', 'monthly_credit_limit', 'monthly_request_limit',
        } else 'Не задано'
    if key.endswith('_usd'):
        return f'{money(value)} USD'
    if key == 'markup_percent':
        return f'{percent(value)}%'
    if isinstance(value, int):
        return integer(value)
    return label(value)


def plural(count: int, one: str, few: str, many: str) -> str:
    value = abs(int(count))
    if 11 <= value % 100 <= 14:
        return many
    if value % 10 == 1:
        return one
    return few if 2 <= value % 10 <= 4 else many
