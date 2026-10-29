# Мини-склад

Небольшое веб-приложение для учёта склада: остатки, приход, выдача на объекты, отмена операций,
история, выгрузка в Excel. Три роли — руководитель, кладовщик, менеджер. Интерфейс на русском и узбекском.

Стек: Python 3.11+, Django 5.1+ (серверные шаблоны), SQLite, openpyxl. Других зависимостей нет,
CDN не используется — приложение работает без интернета.

## Запуск за 5 минут

Нужен только Python 3.11 или новее.

**Windows**

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

**macOS / Linux**

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Откройте http://127.0.0.1:8000. `migrate` сам создаёт базу и трёх пользователей.

То же самое одной командой: `run.bat` (Windows) или `./run.sh` (macOS/Linux) — создадут venv,
поставят зависимости, применят миграции и запустят сервер с включённым `DEBUG`.

Данные лежат в `db.sqlite3` и переживают перезапуск сервера. Ключ `SECRET_KEY` при первом запуске
генерируется в `.secret_key` (в git не попадает).

## Пользователи

| Логин | Пароль | Роль |
|---|---|---|
| `director` | `Director2026!` | Руководитель — всё, включая товары, цены, отмену операций |
| `storekeeper` | `Store2026!` | Кладовщик — остатки **без цен**, приход, выдача, Excel без цен |
| `manager` | `Manager2026!` | Менеджер — остатки с ценами, история, Excel; ничего не меняет |

Это демо-пароли. В рабочей установке смените их: `python manage.py changepassword director`.

## Права

| Действие | Руководитель | Кладовщик | Менеджер |
|---|---|---|---|
| Остатки | с ценами | без цен | с ценами |
| Товары: добавить, изменить, архив | ✅ | ❌ | ❌ |
| Приход / выдача | ✅ | ✅ | ❌ |
| Отмена операции | ✅ | ❌ | ❌ |
| История | ✅ | ❌ | ✅ |
| Excel | с ценами | без цен | с ценами |

Матрица задана в одном месте — [accounts/permissions.py](accounts/permissions.py). Всё, чего в ней нет,
запрещено. Каждая страница закрыта декоратором `@role_required`, запрещённое действие даёт страницу 403,
сервисный слой проверяет права ещё раз.

## С телефона по локальной сети

Компьютер и телефон должны быть в одной Wi-Fi сети.

```
run.bat lan          # Windows
./run.sh lan         # macOS / Linux
```

Вручную: задайте `DJANGO_ALLOWED_HOSTS=*` и запустите `python manage.py runserver 0.0.0.0:8000`.

На телефоне откройте `http://<IP компьютера>:8000`. IP можно узнать так: `ipconfig` (Windows),
`ipconfig getifaddr en0` (macOS) или `hostname -I` (Linux). Если не открывается, разрешите Python
во входящих подключениях брандмауэра Windows. В режиме `lan` отладка выключена: в сети не видны
технические страницы ошибок.

## Docker (по желанию)

```
docker compose up --build
```

Приложение откроется на http://localhost:8000. База и ключ хранятся в томе `sklad-data` и
переживают пересборку. Для доступа из сети: `DJANGO_ALLOWED_HOSTS=* docker compose up --build`.

## Тесты

```
python manage.py test
```

Около 110 тестов, примерно 15 секунд:

- сквозной сценарий через настоящий вход и POST-запросы (100 м / 80 $ → 69,5 м / 55,60 $ → «На складе только 69,5 м» → отмена прихода запрещена → отмена выдачи → 100 м);
- матрица «каждая роль × каждый URL × GET/POST»: ожидаемый код и неизменность базы при отказе;
- кладовщик нигде не видит цену: HTML, выпадающие списки, ошибки, файл Excel;
- граничные значения (выдать ровно остаток — можно, на 0,001 больше — нельзя), дробные штуки, запятая;
- двойная отправка, двойная отмена, отмена прихода, после которой остаток ушёл бы в минус;
- параллельные запросы в отдельных потоках против файловой SQLite;
- деньги (3 × 0,10 $ = 0,30 $), итоги по валютам отдельно;
- нет ответов 500 на мусорный ввод, вход с ограничением попыток, CSRF, безопасный `next`;
- полнота узбекского перевода и актуальность `.mo`.

## Настройки

Всё задаётся переменными окружения или файлом `.env` (образец — [.env.example](.env.example)).

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `DJANGO_DEBUG` | `0` | Режим отладки; `run.bat`/`run.sh` включают его локально |
| `DJANGO_SECRET_KEY` | генерируется в `.secret_key` | Секретный ключ |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Разрешённые имена хоста |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | — | Нужно за HTTPS-доменом |
| `DJANGO_SECURE_COOKIES`, `DJANGO_SSL_REDIRECT`, `DJANGO_HSTS_SECONDS` | выкл. | Включать только при HTTPS |
| `DJANGO_DATA_DIR` / `DJANGO_DB_PATH` | папка проекта | Где лежат база и ключ |

`python manage.py check --deploy` при `DEBUG=0` показывает только предупреждения про HTTPS. Они
исчезают, когда включены три HTTPS-переменные выше (подробности в [DECISIONS.md](DECISIONS.md)).

## Переводы

Исходные строки — на русском, узбекский (латиница) лежит в `locale/uz/LC_MESSAGES/django.po`. Готовый
`.mo` закоммичен, GNU gettext не нужен. После правки `.po`:

```
python manage.py compile_translations
```

Команда собирает `.mo` и перечисляет строки, которые остались без перевода.

## Структура

```
accounts/    пользователь с ролью, матрица прав, вход с ограничением попыток
inventory/   модели, services.py (вся бизнес-логика), числа и деньги, представления, Excel
templates/   шаблоны; CSS и JS встроены (templates/includes), без отдельной раздачи статики
locale/uz/   узбекский перевод
```

Принятые решения и их причины — в [DECISIONS.md](DECISIONS.md).

---

## Oʻzbekcha qisqacha

1. Python 3.11+ oʻrnating.
2. Windows: `run.bat`, macOS/Linux: `./run.sh` — virtual muhit, kutubxonalar, migratsiya va server bitta buyruqda.
3. http://127.0.0.1:8000 ni oching. Loginlar yuqoridagi jadvalda: `director`, `storekeeper`, `manager`.
4. Telefondan: `run.bat lan` yoki `./run.sh lan`, keyin telefonda `http://<kompyuter IP>:8000`.
5. Testlar: `python manage.py test`.

Interfeys tilini yuqoridagi RU / UZ tugmalari bilan almashtirish mumkin. Xato xabarlari ham tarjima qilingan.
