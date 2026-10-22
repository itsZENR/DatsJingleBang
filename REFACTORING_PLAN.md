# План разделения bot.py на модули

## Текущая ситуация
Файл `bot.py` содержит 1390 строк кода с различными компонентами, что затрудняет поддержку и тестирование.

## Анализ структуры bot.py

### Текущие компоненты:
1. **StrategyConfig** (строки 33-108) - загрузка настроек стратегии из JSON
2. **CascadeCalculator** (строки 110-286) - расчет каскадных взрывов
3. **WorldMemory** (строки 288-483) - память карты с fog of war
4. **RateLimiter** (строки 485-506) - ограничитель частоты запросов
5. **DataHandler + HTTP сервер** (строки 509-578) - визуализатор
6. **BombermanBot** (строки 581-1363) - основной класс бота (~780 строк!)
7. **Main** (строки 1365-1391) - точка входа

### Методы BombermanBot (по категориям):

**API взаимодействие:**
- `_request_with_retry`, `get_arena`, `move`, `buy_booster`, `try_buy_boosters`

**Параметры игры:**
- `get_bomb_radius`, `get_bomber_speed`, `get_bomb_timer`

**Поиск пути:**
- `get_neighbors`, `is_walkable`, `bfs_path`, `count_exits`, `is_dead_end`

**Расчет опасности:**
- `get_explosion_zone`, `get_bomb_danger_zones`, `get_mob_danger_zones`, `find_safe_position`

**Тактика и стратегия:**
- `find_bomb_target`, `get_patrol_target`, `calculate_action`

**Логирование:**
- `log_iteration`

**Главный цикл:**
- `run`

## Предлагаемая структура модулей

### Вариант 1: По компонентам (рекомендуемый)

```
bomberman/
├── bomberman/
│   ├── __init__.py
│   ├── config.py                 # Существует - настройки API
│   ├── strategy_config.py        # StrategyConfig класс
│   ├── world_memory.py           # WorldMemory класс
│   ├── cascade.py                # CascadeCalculator класс
│   ├── rate_limiter.py           # RateLimiter класс
│   ├── visualizer.py             # DataHandler + HTTP сервер
│   ├── pathfinding.py            # Методы поиска пути
│   ├── danger.py                 # Расчет опасных зон
│   ├── tactics.py                # Тактические решения (find_bomb_target и т.д.)
│   ├── game_params.py            # Параметры игры (радиус, скорость и т.д.)
│   ├── api_client.py             # API клиент (запросы к серверу)
│   ├── bot.py                    # BombermanBot (основной класс + calculate_action + run)
│   └── main.py                   # Точка входа
```

### Вариант 2: Более агрессивное разделение

```
bomberman/
├── bomberman/
│   ├── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── api_config.py         # API настройки
│   │   └── strategy_config.py    # StrategyConfig
│   ├── core/
│   │   ├── __init__.py
│   │   ├── world_memory.py       # Память карты
│   │   ├── cascade.py            # Каскадные взрывы
│   │   └── rate_limiter.py       # Rate limiting
│   ├── pathfinding/
│   │   ├── __init__.py
│   │   ├── bfs.py                # BFS алгоритм
│   │   └── utils.py              # Вспомогательные функции
│   ├── combat/
│   │   ├── __init__.py
│   │   ├── danger.py             # Расчет опасности
│   │   └── tactics.py            # Тактические решения
│   ├── api/
│   │   ├── __init__.py
│   │   └── client.py             # API клиент
│   ├── visualization/
│   │   ├── __init__.py
│   │   └── server.py             # HTTP сервер
│   ├── bot.py                    # BombermanBot главный класс
│   └── main.py                   # Точка входа
```

## Рекомендация: Вариант 1

**Преимущества:**
- Плоская структура - проще навигация
- Четкое разделение ответственности
- Легко понять где что находится
- Минимальные изменения импортов

**Разделение:**

1. **strategy_config.py** (~80 строк)
   - Класс StrategyConfig
   - Управление настройками стратегии

2. **world_memory.py** (~200 строк)
   - Класс WorldMemory
   - Fog of war, память карты

3. **cascade.py** (~180 строк)
   - Класс CascadeCalculator
   - Статические методы для каскадных взрывов

4. **rate_limiter.py** (~25 строк)
   - Класс RateLimiter
   - Ограничение частоты запросов

5. **visualizer.py** (~80 строк)
   - DataHandler класс
   - start_data_server функция
   - shared_data (глобальная переменная)

6. **pathfinding.py** (~150 строк)
   - Функции: get_neighbors, is_walkable, bfs_path
   - Функции: count_exits, is_dead_end
   - Вспомогательные для навигации

7. **danger.py** (~100 строк)
   - Функции: get_explosion_zone, get_bomb_danger_zones
   - Функции: get_mob_danger_zones, find_safe_position
   - Расчет опасных зон

8. **tactics.py** (~350 строк)
   - Функции: find_bomb_target, get_patrol_target
   - Тактические решения

9. **game_params.py** (~50 строк)
   - Функции: get_bomb_radius, get_bomber_speed, get_bomb_timer
   - Константы игры

10. **api_client.py** (~100 строк)
    - Класс BombermanAPIClient
    - Методы: get_arena, move, buy_booster, try_buy_boosters
    - _request_with_retry

11. **bot.py** (~150-200 строк)
    - Класс BombermanBot (использует все выше)
    - Метод calculate_action (главная AI логика)
    - Метод run (главный цикл)
    - Метод log_iteration

12. **main.py** (~30 строк)
    - Точка входа
    - Парсинг аргументов
    - Запуск бота

## Порядок рефакторинга

1. Создать новые файлы с самодостаточными компонентами:
   - strategy_config.py
   - world_memory.py
   - cascade.py
   - rate_limiter.py

2. Создать файлы с утилитами:
   - pathfinding.py
   - danger.py
   - game_params.py

3. Создать файлы с бизнес-логикой:
   - tactics.py
   - api_client.py
   - visualizer.py

4. Рефакторить bot.py:
   - Убрать все перенесенные классы и функции
   - Добавить импорты
   - Упростить BombermanBot (композиция вместо наследования)

5. Создать main.py:
   - Перенести точку входа
   - Обновить bot.py чтобы не был исполняемым

6. Создать __init__.py:
   - Экспортировать основные классы
   - Упростить импорты для пользователей

## Зависимости между модулями

```
main.py
  └─> bot.py
       ├─> api_client.py
       ├─> world_memory.py
       │    └─> (standalone)
       ├─> strategy_config.py
       │    └─> (standalone)
       ├─> cascade.py
       │    └─> (standalone)
       ├─> pathfinding.py
       │    └─> (standalone)
       ├─> danger.py
       │    └─> pathfinding.py
       ├─> tactics.py
       │    ├─> pathfinding.py
       │    ├─> danger.py
       │    └─> cascade.py
       ├─> game_params.py
       │    └─> (standalone)
       └─> visualizer.py
            └─> (standalone)
```

## Тестирование

После рефакторинга:
1. Убедиться что бот запускается: `python -m bomberman.main`
2. Проверить что API вызовы работают
3. Проверить логику принятия решений
4. Запустить visualizer на localhost:8888

## Совместимость

Для обратной совместимости можно оставить старый `bot.py` с импортами:
```python
# Legacy compatibility
from bomberman.bot import BombermanBot
from bomberman.main import main
```
