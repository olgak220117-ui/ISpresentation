# IS Channel Manager

Синхронизация календарей между Airbnb, Booking.com и Agoda для управляющей компании.

## Быстрый старт

```bash
cd channel_manager
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # настрой BASE_URL
python run.py
```

Открой http://localhost:8000

## Как подключить объект

1. **Добавить объект** — нажми "+ Добавить объект"

2. **Добавить платформы** — для каждой платформы получи iCal Export URL:
   - **Airbnb**: Календарь → Настройки → Экспорт → скопируй ссылку `.ics`
   - **Booking.com**: Экстранет → Апартаменты → Календарь → Синхронизация iCal → Export
   - **Agoda**: YCS → Управление объектом → iCal → Export URL

3. **Скопируй мастер-iCal URL** из карточки объекта

4. **Вставь мастер-iCal на каждой платформе** как Import URL:
   - Airbnb: Календарь → Синхронизация → Импорт
   - Booking.com: Экстранет → iCal → Import
   - Agoda: YCS → iCal → Import

Теперь при бронировании на любой платформе — через 30 минут даты заблокируются на всех остальных.

## Как работает синхронизация

```
Airbnb iCal ──┐
Booking iCal ─┼──► Мастер-БД ──► Мастер-iCal URL ──► Импорт на всех платформах
Agoda iCal ───┘
```

- Сервер опрашивает каждую платформу каждые 30 минут (настройка: `SYNC_INTERVAL_MINUTES`)
- При появлении нового бронирования — оно записывается в БД
- Мастер-iCal всегда отдаёт актуальный список занятых дат
- Каждая платформа автоматически блокирует даты из импортированного iCal

## API

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/` | Дашборд |
| GET | `/api/properties` | Список объектов |
| POST | `/api/properties` | Создать объект |
| GET | `/api/properties/{id}` | Детали объекта |
| PUT | `/api/properties/{id}` | Обновить объект |
| DELETE | `/api/properties/{id}` | Удалить объект |
| POST | `/api/properties/{id}/platforms` | Добавить платформу |
| PUT | `/api/platforms/{id}` | Обновить платформу |
| DELETE | `/api/platforms/{id}` | Удалить платформу |
| POST | `/api/sync/all` | Синхронизировать всё |
| POST | `/api/sync/{id}` | Синхронизировать объект |
| GET | `/calendar/{token}` | Мастер-iCal (публичный) |
| GET | `/api/bookings` | Список бронирований |

## .env параметры

```
DATABASE_URL=sqlite:///./channel_manager.db
SECRET_KEY=your-secret-key
SYNC_INTERVAL_MINUTES=30
BASE_URL=https://your-domain.com
```

## Деплой на сервер

```bash
# На Ubuntu/Debian
pip install gunicorn
gunicorn app.main:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

Рекомендуется поставить Nginx как reverse proxy и настроить HTTPS — платформы требуют HTTPS для импорта iCal.
