"""
Бот для игры Bomberman DatsJingleBang v2.8

Стратегия:
1. Каскадные взрывы - размещение бомб для цепной детонации
2. Искать позиции для взрыва 3+ препятствий (6+ очков)
3. Если нет доступных - разведка неизведанных областей карты
4. Если заблокирован - пробивать путь (приоритет: 2 препятствия, потом 1)
5. Избегать мобов и зон взрыва

v2.1: Добавлена память карты (fog of war)
v2.2: Исправлен расчёт радиуса бомбы (базовый=1, +1 за уровень bomb_range)
v2.3: Оптимизация очков - приоритет 2+ препятствий
v2.4: Увеличен радиус поиска целей (10 клеток), fallback на 1 препятствие
v2.5: Разведка неизведанных областей, приоритеты взрыва (3+, 2, 1)
v2.6: Исправлена логика fallback - если high_value недоступен, пробуем medium/low
v2.7: Retry логика для таймаутов (3 попытки, timeout 10 сек)
v2.8: Каскадные взрывы + настройки стратегии из JSON
"""

import requests
import time
import math
from collections import deque
from typing import List, Tuple, Dict, Optional, Set
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime


class StrategyConfig:
    """Загрузка и управление настройками стратегии из JSON"""

    DEFAULT_CONFIG = {
        "scoring": {
            "min_obstacles_to_bomb": 1,
            "high_value_threshold": 6,
            "medium_value_threshold": 3,
            "prefer_chain_reactions": True,
            "chain_bonus_multiplier": 1.5
        },
        "cascade": {
            "enabled": True,
            "max_bombs_in_chain": 4,
            "min_obstacles_per_bomb": 1,
            "prefer_cascade_over_single": True
        },
        "exploration": {
            "enabled": True,
            "priority_over_low_value": True,
            "max_path_length": 15
        },
        "safety": {
            "escape_margin_steps": 2,
            "avoid_dead_ends": True,
            "min_exits_required": 2
        },
        "debug": {
            "verbose": False,
            "log_cascade_calculations": False
        }
    }

    def __init__(self, config_path: str = None):
        self.config = self.DEFAULT_CONFIG.copy()
        self.config_path = config_path or Path(__file__).parent / 'strategy_config.json'
        self.last_modified = 0
        self.load()

    def load(self) -> None:
        """Загрузить конфиг из файла"""
        try:
            path = Path(self.config_path)
            if path.exists():
                mtime = path.stat().st_mtime
                if mtime != self.last_modified:
                    with open(path, 'r', encoding='utf-8') as f:
                        loaded = json.load(f)
                        # Мержим с дефолтами
                        for key, value in loaded.items():
                            if key.startswith('_'):
                                continue
                            if key in self.config and isinstance(value, dict):
                                self.config[key].update(value)
                            else:
                                self.config[key] = value
                    self.last_modified = mtime
                    print(f"[Config] Loaded from {path}")
        except Exception as e:
            print(f"[Config] Error loading: {e}, using defaults")

    def get(self, section: str, key: str, default=None):
        """Получить значение настройки"""
        return self.config.get(section, {}).get(key, default)

    def reload_if_changed(self) -> bool:
        """Перезагрузить если файл изменился"""
        try:
            path = Path(self.config_path)
            if path.exists() and path.stat().st_mtime != self.last_modified:
                self.load()
                return True
        except:
            pass
        return False


class CascadeCalculator:
    """Расчёт каскадных (цепных) взрывов"""

    @staticmethod
    def get_explosion_zone(pos: Tuple[int, int], walls: Set, obstacles: Set,
                           radius: int) -> Set[Tuple[int, int]]:
        """Получить зону взрыва бомбы"""
        danger = {pos}
        bx, by = pos
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            for dist in range(1, radius + 1):
                nx, ny = bx + dx * dist, by + dy * dist
                if (nx, ny) in walls:
                    break
                danger.add((nx, ny))
                if (nx, ny) in obstacles:
                    break
        return danger

    @staticmethod
    def simulate_chain_explosion(bomb_positions: List[Tuple[int, int]],
                                  walls: Set, obstacles: Set,
                                  radius: int) -> Tuple[Set[Tuple[int, int]], Set[Tuple[int, int]]]:
        """
        Симулировать цепной взрыв нескольких бомб.
        Возвращает: (total_danger_zone, destroyed_obstacles)
        """
        if not bomb_positions:
            return set(), set()

        remaining_obstacles = obstacles.copy()
        total_danger = set()
        bombs_to_explode = set(bomb_positions)
        exploded_bombs = set()

        # Итеративно взрываем бомбы (цепная реакция)
        while bombs_to_explode:
            new_explosions = set()

            for bomb_pos in bombs_to_explode:
                if bomb_pos in exploded_bombs:
                    continue

                # Зона взрыва этой бомбы
                zone = CascadeCalculator.get_explosion_zone(
                    bomb_pos, walls, remaining_obstacles, radius)
                total_danger |= zone
                exploded_bombs.add(bomb_pos)

                # Уничтожаем препятствия в зоне
                destroyed = remaining_obstacles & zone
                remaining_obstacles -= destroyed

                # Проверяем детонацию других бомб
                for other_bomb in bomb_positions:
                    if other_bomb not in exploded_bombs and other_bomb in zone:
                        new_explosions.add(other_bomb)

            bombs_to_explode = new_explosions

        destroyed_obstacles = obstacles - remaining_obstacles
        return total_danger, destroyed_obstacles

    @staticmethod
    def find_cascade_positions(start_pos: Tuple[int, int],
                                walls: Set, obstacles: Set,
                                bombs: Set, radius: int,
                                max_bombs: int = 4,
                                map_size: Tuple[int, int] = None) -> List[Dict]:
        """
        Найти позиции для каскадного взрыва.
        Возвращает список вариантов: [{positions: [...], score: N, obstacles: [...]}]
        """
        if max_bombs < 2:
            return []

        results = []
        visited_combos = set()

        # Ищем первую позицию рядом с препятствиями
        candidate_positions = []
        for dx in range(-6, 7):
            for dy in range(-6, 7):
                pos = (start_pos[0] + dx, start_pos[1] + dy)
                if map_size:
                    if pos[0] < 0 or pos[0] >= map_size[0]:
                        continue
                    if pos[1] < 0 or pos[1] >= map_size[1]:
                        continue
                if pos in walls or pos in obstacles or pos in bombs:
                    continue

                # Проверяем есть ли препятствия в радиусе взрыва
                zone = CascadeCalculator.get_explosion_zone(pos, walls, obstacles, radius)
                hits = len(zone & obstacles)
                if hits > 0:
                    candidate_positions.append((pos, hits))

        # Сортируем по количеству попаданий
        candidate_positions.sort(key=lambda x: -x[1])

        # Для каждой стартовой позиции ищем цепочку
        for first_pos, _ in candidate_positions[:10]:
            chain = [first_pos]
            chain_obstacles = set()

            # Добавляем бомбы в цепочку
            for _ in range(max_bombs - 1):
                best_next = None
                best_score = 0

                # Ищем следующую позицию которая:
                # 1. В зоне взрыва предыдущей бомбы
                # 2. Задевает новые препятствия
                last_zone = CascadeCalculator.get_explosion_zone(
                    chain[-1], walls, obstacles - chain_obstacles, radius)

                for dx in range(-radius - 1, radius + 2):
                    for dy in range(-radius - 1, radius + 2):
                        next_pos = (chain[-1][0] + dx, chain[-1][1] + dy)

                        if next_pos in chain:
                            continue
                        if next_pos in walls or next_pos in obstacles or next_pos in bombs:
                            continue
                        if map_size:
                            if next_pos[0] < 0 or next_pos[0] >= map_size[0]:
                                continue
                            if next_pos[1] < 0 or next_pos[1] >= map_size[1]:
                                continue

                        # Должна быть в зоне взрыва предыдущей (для цепной реакции)
                        if next_pos not in last_zone:
                            continue

                        # Считаем новые препятствия
                        next_zone = CascadeCalculator.get_explosion_zone(
                            next_pos, walls, obstacles - chain_obstacles, radius)
                        new_hits = len((next_zone & obstacles) - chain_obstacles)

                        if new_hits > best_score:
                            best_score = new_hits
                            best_next = next_pos

                if best_next and best_score > 0:
                    chain.append(best_next)
                    # Симулируем взрыв для обновления chain_obstacles
                    _, destroyed = CascadeCalculator.simulate_chain_explosion(
                        chain, walls, obstacles, radius)
                    chain_obstacles = destroyed
                else:
                    break

            # Если цепочка >= 2 бомб, добавляем результат
            if len(chain) >= 2:
                _, total_destroyed = CascadeCalculator.simulate_chain_explosion(
                    chain, walls, obstacles, radius)

                # Считаем очки по правилам игры
                obs_count = len(total_destroyed)
                score = sum(range(1, min(obs_count, 4) + 1))

                # Ключ для дедупликации
                combo_key = tuple(sorted(chain))
                if combo_key not in visited_combos:
                    visited_combos.add(combo_key)
                    results.append({
                        'positions': chain,
                        'score': score,
                        'obstacles': total_destroyed,
                        'bombs_count': len(chain)
                    })

        # Сортируем по эффективности (очки / количество бомб)
        results.sort(key=lambda x: (-x['score'], x['bombs_count']))
        return results[:5]


class WorldMemory:
    """
    Память карты с учётом тумана войны.
    Хранит разведанные участки и обновляет их только в зоне видимости бомберов.
    """

    def __init__(self):
        self.obstacles: Set[Tuple[int, int]] = set()  # Препятствия (могут быть уничтожены)
        self.walls: Set[Tuple[int, int]] = set()       # Стены (статичны)
        self.enemies: Dict[str, Dict] = {}             # Враги: id -> {pos, ...}
        self.mobs: Dict[str, Dict] = {}                # Мобы: id -> {pos, ...}
        self.map_size: Tuple[int, int] = (0, 0)
        self.base_view_radius: int = 5                 # Базовый радиус видимости
        self.explored: Set[Tuple[int, int]] = set()    # Разведанные клетки

    def get_view_radius(self, boosters: Dict) -> int:
        """Получить радиус видимости с учётом апгрейдов (+3 за уровень)"""
        view_level = boosters.get('view', 0)
        return self.base_view_radius + (view_level * 3)

    def is_in_view(self, pos: Tuple[int, int], bomber_pos: Tuple[int, int], radius: int) -> bool:
        """Проверка находится ли клетка в радиусе видимости (r² = x² + y²)"""
        dx = pos[0] - bomber_pos[0]
        dy = pos[1] - bomber_pos[1]
        return (dx * dx + dy * dy) <= (radius * radius)

    def get_visible_cells(self, bombers: List[Dict], boosters: Dict) -> Set[Tuple[int, int]]:
        """Получить все клетки в зоне видимости живых бомберов"""
        visible = set()
        radius = self.get_view_radius(boosters)

        for bomber in bombers:
            if not bomber.get('alive'):
                continue
            bx, by = bomber['pos']

            # Перебираем квадрат вокруг бомбера
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if dx * dx + dy * dy <= radius * radius:
                        x, y = bx + dx, by + dy
                        if 0 <= x < self.map_size[0] and 0 <= y < self.map_size[1]:
                            visible.add((x, y))

        return visible

    def update(self, arena: Dict) -> None:
        """
        Обновить память карты на основе текущих данных API.
        В зоне видимости - актуальные данные.
        Вне зоны видимости - сохраняем запомненные.
        """
        self.map_size = tuple(arena['map_size'])
        boosters = arena.get('boosters', {})
        bombers = arena.get('bombers', [])

        # Получаем зону видимости
        visible_cells = self.get_visible_cells(bombers, boosters)
        self.explored.update(visible_cells)

        # --- Обновление стен (статичны, просто запоминаем) ---
        for wall in arena['arena'].get('walls', []):
            self.walls.add(tuple(wall))

        # --- Обновление препятствий ---
        current_obstacles = set(tuple(o) for o in arena['arena'].get('obstacles', []))

        # Удаляем препятствия в зоне видимости, которых нет в JSON
        obstacles_to_remove = set()
        for obs in self.obstacles:
            if obs in visible_cells and obs not in current_obstacles:
                obstacles_to_remove.add(obs)
        self.obstacles -= obstacles_to_remove

        # Добавляем новые препятствия из JSON
        self.obstacles.update(current_obstacles)

        # --- Обновление врагов ---
        current_enemies = {e['id']: e for e in arena.get('enemies', [])}

        # Удаляем врагов, которые были в зоне видимости но пропали
        enemies_to_remove = []
        for eid, enemy in self.enemies.items():
            enemy_pos = tuple(enemy['pos'])
            if enemy_pos in visible_cells and eid not in current_enemies:
                enemies_to_remove.append(eid)
        for eid in enemies_to_remove:
            del self.enemies[eid]

        # Обновляем/добавляем врагов из JSON
        for eid, enemy in current_enemies.items():
            self.enemies[eid] = enemy

        # --- Обновление мобов ---
        current_mobs = {m.get('id', str(m['pos'])): m for m in arena.get('mobs', [])}

        # Удаляем мобов, которые были в зоне видимости но пропали
        mobs_to_remove = []
        for mid, mob in self.mobs.items():
            mob_pos = tuple(mob['pos'])
            if mob_pos in visible_cells and mid not in current_mobs:
                mobs_to_remove.append(mid)
        for mid in mobs_to_remove:
            del self.mobs[mid]

        # Обновляем/добавляем мобов из JSON
        for mid, mob in current_mobs.items():
            self.mobs[mid] = mob

    def get_merged_arena(self, arena: Dict) -> Dict:
        """
        Возвращает arena с merged данными:
        - В зоне видимости: актуальные данные из API
        - Вне зоны видимости: запомненные данные
        """
        merged = dict(arena)
        merged['arena'] = dict(arena['arena'])

        boosters = arena.get('boosters', {})
        bombers = arena.get('bombers', [])

        # Текущая зона видимости
        visible_cells = self.get_visible_cells(bombers, boosters)

        # Препятствия: объединяем видимые + запомненные вне видимости
        merged['arena']['obstacles'] = [list(o) for o in self.obstacles]

        # Стены: просто из памяти (они статичны)
        merged['arena']['walls'] = [list(w) for w in self.walls]

        # Враги: объединяем
        merged['enemies'] = list(self.enemies.values())

        # Мобы: объединяем
        merged['mobs'] = list(self.mobs.values())

        # Добавляем мета-информацию для визуализатора
        merged['_memory'] = {
            'explored_cells': len(self.explored),
            'total_cells': self.map_size[0] * self.map_size[1] if self.map_size[0] > 0 else 0,
            'remembered_obstacles': len(self.obstacles),
            'remembered_enemies': len(self.enemies),
            'remembered_mobs': len(self.mobs),
            'visible_cells': [list(c) for c in visible_cells],
            'explored': [list(c) for c in self.explored],
            'view_radius': self.get_view_radius(boosters)
        }

        return merged

    def find_exploration_targets(self, bomber_pos: Tuple[int, int], walls: Set, obstacles: Set,
                                   bombs: Set, max_targets: int = 5) -> List[Tuple[int, int]]:
        """
        Найти клетки на границе изведанной области, к которым можно дойти.
        Возвращает список целей для разведки, отсортированных по расстоянию.
        """
        if not self.explored:
            return []

        # Ищем клетки на границе explored - соседи explored но сами не explored
        frontier = set()
        for ex, ey in self.explored:
            for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
                nx, ny = ex + dx, ey + dy
                if (nx, ny) not in self.explored:
                    if 0 <= nx < self.map_size[0] and 0 <= ny < self.map_size[1]:
                        if (nx, ny) not in walls:
                            # Цель - клетка в explored рядом с неизведанной
                            if (ex, ey) not in walls and (ex, ey) not in obstacles and (ex, ey) not in bombs:
                                frontier.add((ex, ey))

        # Сортируем по расстоянию от бомбера
        sorted_frontier = sorted(frontier,
                                 key=lambda p: abs(p[0] - bomber_pos[0]) + abs(p[1] - bomber_pos[1]))

        return sorted_frontier[:max_targets]

    def is_blocked_by_obstacles(self, pos: Tuple[int, int], obstacles: Set) -> bool:
        """Проверить окружена ли позиция препятствиями (нет свободных соседей)"""
        free_neighbors = 0
        for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
            nx, ny = pos[0] + dx, pos[1] + dy
            if (nx, ny) not in self.walls and (nx, ny) not in obstacles:
                if 0 <= nx < self.map_size[0] and 0 <= ny < self.map_size[1]:
                    free_neighbors += 1
        return free_neighbors == 0

    def reset(self) -> None:
        """Сброс памяти (новый раунд)"""
        self.obstacles.clear()
        self.walls.clear()
        self.enemies.clear()
        self.mobs.clear()
        self.explored.clear()
        self.map_size = (0, 0)


class RateLimiter:
    """Ограничитель частоты запросов: макс 3 req/sec"""

    def __init__(self, max_requests: int = 3, per_seconds: float = 1.0):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.requests = deque()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            while self.requests and self.requests[0] < now - self.per_seconds:
                self.requests.popleft()
            if len(self.requests) >= self.max_requests:
                sleep_time = self.requests[0] + self.per_seconds - now
                if sleep_time > 0:
                    time.sleep(sleep_time)
                if self.requests:
                    self.requests.popleft()
            self.requests.append(time.time())


# Данные для визуализатора
shared_data = {'arena': None, 'iteration': 0, 'actions': [], 'lock': threading.Lock()}


class DataHandler(BaseHTTPRequestHandler):
    logs_dir = Path(__file__).parent / 'arena_logs'

    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == '/arena':
            with shared_data['lock']:
                data = shared_data['arena'] or {}
                data = dict(data)
                data['_iteration'] = shared_data['iteration']
                data['_actions'] = shared_data['actions']
            self.send_json(data)

        elif self.path == '/logs':
            # Список файлов логов
            files = []
            if self.logs_dir.exists():
                for f in sorted(self.logs_dir.glob('*.jsonl'), key=lambda x: x.stat().st_mtime, reverse=True):
                    files.append({
                        'name': f.name,
                        'size': f.stat().st_size,
                        'modified': f.stat().st_mtime
                    })
            self.send_json(files)

        elif self.path.startswith('/logs/'):
            # Содержимое конкретного файла
            filename = self.path[6:]  # Убираем '/logs/'
            # Защита от path traversal
            if '..' in filename or '/' in filename or '\\' in filename:
                self.send_json({'error': 'Invalid filename'}, 400)
                return
            filepath = self.logs_dir / filename
            if filepath.exists() and filepath.suffix == '.jsonl':
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(filepath.read_bytes())
            else:
                self.send_json({'error': 'File not found'}, 404)

        else:
            self.send_json({'error': 'Not found'}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()


def start_data_server(port=8888):
    server = HTTPServer(('localhost', port), DataHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class BombermanBot:
    def __init__(self, api_url: str, auth_token: str):
        self.api_url = api_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'X-Auth-Token': auth_token})
        self.rate_limiter = RateLimiter(max_requests=3, per_seconds=1.0)

        # Память карты (fog of war)
        self.world_memory = WorldMemory()

        # Загрузка настроек стратегии
        self.config = StrategyConfig()

        # Планировщик каскадов (для координации между бомберами)
        self.planned_cascade = None  # {'positions': [...], 'assigned': {bomber_id: pos}}

        # Настройка логирования арены
        self.arena_logs_dir = Path('arena_logs')
        self.arena_logs_dir.mkdir(exist_ok=True)
        self.current_round = None
        self.current_log_file = None

    def log_iteration(self, arena_data: Dict, actions: List[Dict], commands: List[Dict],
                       move_result: Optional[Dict], iteration: int):
        """Логирование итерации: состояние + действия + результат"""
        round_name = arena_data.get('round', 'unknown')

        # Если раунд изменился, создаем новый файл
        if round_name != self.current_round:
            self.current_round = round_name
            self.current_log_file = self.arena_logs_dir / f"{round_name}.jsonl"
            print(f"Logging to: {self.current_log_file}")

        # Формируем запись с полной информацией
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'iteration': iteration,
            'arena': arena_data,
            'actions': actions,  # Действия каждого бомбера с причиной
            'commands': commands,  # Отправленные команды
            'result': move_result  # Ответ сервера
        }

        with open(self.current_log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

    def _request_with_retry(self, method: str, endpoint: str, json_data: Dict = None,
                             max_retries: int = 3) -> Dict:
        """Выполнить запрос с retry при таймаутах"""
        last_error = None
        for attempt in range(max_retries):
            try:
                self.rate_limiter.wait()
                if method == 'GET':
                    response = self.session.get(f'{self.api_url}{endpoint}', timeout=10)
                else:
                    response = self.session.post(f'{self.api_url}{endpoint}', json=json_data, timeout=10)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.Timeout as e:
                last_error = e
                print(f"Timeout on {endpoint} (attempt {attempt + 1}/{max_retries})")
                time.sleep(0.5)
            except requests.exceptions.ConnectionError as e:
                last_error = e
                print(f"Connection error on {endpoint} (attempt {attempt + 1}/{max_retries})")
                time.sleep(1)
        raise last_error

    def get_arena(self) -> Dict:
        data = self._request_with_retry('GET', '/api/arena')
        with shared_data['lock']:
            shared_data['arena'] = data
        return data

    def move(self, commands: Dict) -> Dict:
        return self._request_with_retry('POST', '/api/move', commands)

    def buy_booster(self, booster_id: int) -> Dict:
        """Купить бустер по ID"""
        return self._request_with_retry('POST', '/api/booster', {'booster': booster_id})

    def try_buy_boosters(self, arena: Dict) -> None:
        """Попробовать купить бустеры если есть skill_points"""
        skill_points = arena.get('skill_points', 0)
        if skill_points <= 0:
            return

        # Текущие уровни бустеров
        boosters = arena.get('boosters', {})

        # Бустеры: ID, название, стоимость, макс уровень
        # 0: pockets (+1 бомба) - 1 поинт, без лимита
        # 1: bomb_range (+1 радиус) - 1 поинт, без лимита
        # 2: speed (+1 скорость) - 1 поинт, max 3
        # 3: view (+3 видимость) - 1 поинт, без лимита
        # 4: bombers (+1 юнит) - 2 поинта, без лимита
        # 5: armor (+1 броня) - 1 поинт, без лимита
        # 6: bomb_delay (-2 сек) - 1 поинт, max 3
        # 7: acrobatics (проходимость) - 2 поинта, max 3

        # Приоритет покупки (ID, стоимость, макс_уровень, название)
        priority = [
            (0, 1, 99, 'pockets'),      # +1 бомба - ВАЖНО
            (1, 1, 99, 'bomb_range'),   # +1 радиус - ВАЖНО
            (2, 1, 3, 'speed'),         # +1 скорость
            (5, 1, 99, 'armor'),        # +1 броня
            (6, 1, 3, 'bomb_delay'),    # -2 сек до взрыва
            (3, 1, 99, 'view'),         # +3 видимость
            (7, 2, 3, 'acrobatics'),    # проходимость
            (4, 2, 99, 'bombers'),      # +1 юнит - в последнюю очередь
        ]

        for booster_id, cost, max_level, name in priority:
            if skill_points < cost:
                continue

            current_level = boosters.get(name, 0)
            if current_level >= max_level:
                continue

            try:
                result = self.buy_booster(booster_id)
                if result.get('code', 0) == 0:
                    print(f">>> BOUGHT {name} (level {current_level + 1})")
                    skill_points -= cost
                else:
                    errors = result.get('errors', [])
                    print(f"Failed to buy {name}: {errors}")
            except Exception as e:
                print(f"Error buying {name}: {e}")

            # Покупаем по одному за итерацию чтобы не тратить rate limit
            break

    def get_bomb_radius(self, arena: Dict) -> int:
        """Получить радиус бомбы: базовый 1 + уровень bomb_range"""
        boosters = arena.get('boosters', {})
        return 1 + boosters.get('bomb_range', 0)

    def get_bomber_speed(self, arena: Dict) -> int:
        """Получить скорость бомбера: базовая 2 + уровень speed (max +3)"""
        boosters = arena.get('boosters', {})
        return 2 + min(boosters.get('speed', 0), 3)

    def get_bomb_timer(self, arena: Dict) -> float:
        """Получить время до взрыва бомбы: базовое 8 сек, -2 за уровень bomb_delay (max 3)"""
        boosters = arena.get('boosters', {})
        delay_level = min(boosters.get('bomb_delay', 0), 3)
        return 8.0 - (delay_level * 2.0)

    def get_neighbors(self, pos: Tuple[int, int]) -> List[Tuple[int, int]]:
        """4 соседние клетки"""
        x, y = pos
        return [(x+1, y), (x-1, y), (x, y+1), (x, y-1)]

    def is_walkable(self, pos: Tuple[int, int], walls: Set, obstacles: Set, bombs: Set) -> bool:
        """Можно ли пройти через клетку"""
        return pos not in walls and pos not in obstacles and pos not in bombs

    def count_exits(self, pos: Tuple[int, int], walls: Set, obstacles: Set, bombs: Set,
                    map_size: Tuple[int, int]) -> int:
        """Подсчёт свободных выходов из позиции"""
        count = 0
        for neighbor in self.get_neighbors(pos):
            if neighbor[0] < 0 or neighbor[0] >= map_size[0]:
                continue
            if neighbor[1] < 0 or neighbor[1] >= map_size[1]:
                continue
            if self.is_walkable(neighbor, walls, obstacles, bombs):
                count += 1
        return count

    def is_dead_end(self, pos: Tuple[int, int], walls: Set, obstacles: Set, bombs: Set,
                    map_size: Tuple[int, int], min_exits: int = 2) -> bool:
        """Проверка является ли позиция тупиком (менее min_exits выходов)"""
        return self.count_exits(pos, walls, obstacles, bombs, map_size) < min_exits

    def get_explosion_zone(self, pos: Tuple[int, int], walls: Set, obstacles: Set, radius: int = 1) -> Set[Tuple[int, int]]:
        """Получить зону взрыва от бомбы с учётом стен и препятствий"""
        danger = {pos}
        bx, by = pos
        for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
            for dist in range(1, radius + 1):
                nx, ny = bx + dx * dist, by + dy * dist
                if (nx, ny) in walls:
                    break  # Стена блокирует взрыв
                danger.add((nx, ny))
                if (nx, ny) in obstacles:
                    break  # Препятствие тоже блокирует (и уничтожается)
        return danger

    def get_bomb_danger_zones(self, arena: Dict) -> Set[Tuple[int, int]]:
        """Получить все опасные клетки от бомб"""
        danger = set()
        walls = set(tuple(w) for w in arena['arena'].get('walls', []))
        obstacles = set(tuple(o) for o in arena['arena'].get('obstacles', []))

        for bomb in arena['arena'].get('bombs', []):
            pos = tuple(bomb['pos'])
            radius = bomb.get('range', 1)
            danger |= self.get_explosion_zone(pos, walls, obstacles, radius)
        return danger

    def get_mob_danger_zones(self, arena: Dict) -> Set[Tuple[int, int]]:
        """Получить опасные клетки от мобов (моб + соседи)"""
        danger = set()
        for mob in arena.get('mobs', []):
            if mob.get('is_sleeping'):
                continue
            mx, my = mob['pos']
            danger.add((mx, my))
            # Соседние клетки тоже опасны (моб может туда пойти)
            for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
                danger.add((mx + dx, my + dy))
        return danger

    def bfs_path(self, start: Tuple[int, int], goal: Tuple[int, int],
                 walls: Set, obstacles: Set, bombs: Set,
                 danger_zones: Set = None, map_size: Tuple[int, int] = None) -> Optional[List[Tuple[int, int]]]:
        """BFS поиск пути"""
        if start == goal:
            return [start]

        queue = deque([(start, [start])])
        visited = {start}

        while queue:
            current, path = queue.popleft()
            if len(path) > 30:
                continue

            for neighbor in self.get_neighbors(current):
                if neighbor in visited:
                    continue

                # Проверка границ карты
                if map_size:
                    if neighbor[0] < 0 or neighbor[0] >= map_size[0]:
                        continue
                    if neighbor[1] < 0 or neighbor[1] >= map_size[1]:
                        continue

                if not self.is_walkable(neighbor, walls, obstacles, bombs):
                    continue

                # Избегаем опасных зон (кроме цели)
                if danger_zones and neighbor in danger_zones and neighbor != goal:
                    continue

                new_path = path + [neighbor]
                if neighbor == goal:
                    return new_path

                visited.add(neighbor)
                queue.append((neighbor, new_path))

        return None

    def find_safe_position(self, start: Tuple[int, int], arena: Dict,
                          walls: Set, obstacles: Set, bombs: Set,
                          danger_zones: Set) -> Optional[Tuple[int, int]]:
        """Найти ближайшую безопасную позицию"""
        map_size = tuple(arena['map_size'])
        queue = deque([start])
        visited = {start}

        while queue:
            current = queue.popleft()

            # Если позиция безопасна - возвращаем
            if current not in danger_zones:
                return current

            for neighbor in self.get_neighbors(current):
                if neighbor in visited:
                    continue
                if neighbor[0] < 0 or neighbor[0] >= map_size[0]:
                    continue
                if neighbor[1] < 0 or neighbor[1] >= map_size[1]:
                    continue
                if not self.is_walkable(neighbor, walls, obstacles, bombs):
                    continue

                visited.add(neighbor)
                queue.append(neighbor)

        return None

    def find_bomb_target(self, bomber_pos: Tuple[int, int], arena: Dict,
                        walls: Set, obstacles: Set, bombs: Set,
                        danger_zones: Set, exclude_positions: Set = None,
                        reserved_obstacles: Set = None) -> Optional[Tuple[Tuple[int, int], int, Set]]:
        """Найти лучшую позицию для бомбы. Возвращает (позиция, кол-во целей, set препятствий)"""
        map_size = tuple(arena['map_size'])
        bomb_radius = self.get_bomb_radius(arena)
        candidates = []

        if reserved_obstacles is None:
            reserved_obstacles = set()

        # Ищем в радиусе 10 клеток
        for dx in range(-10, 11):
            for dy in range(-10, 11):
                pos = (bomber_pos[0] + dx, bomber_pos[1] + dy)

                # Проверка границ
                if pos[0] < 0 or pos[0] >= map_size[0]:
                    continue
                if pos[1] < 0 or pos[1] >= map_size[1]:
                    continue

                # Позиция должна быть проходимой (или это текущая позиция)
                if pos != bomber_pos and not self.is_walkable(pos, walls, obstacles, bombs):
                    continue

                # Позиция не должна быть опасной
                if pos in danger_zones:
                    continue

                # Исключаем занятые другими бомберами позиции
                if exclude_positions and pos in exclude_positions:
                    continue

                # Считаем сколько целей взорвём (исключая уже зарезервированные)
                hit_obstacles = set()
                for ddx, ddy in [(1,0), (-1,0), (0,1), (0,-1)]:
                    for dist in range(1, bomb_radius + 1):
                        check = (pos[0] + ddx * dist, pos[1] + ddy * dist)
                        if check in walls:
                            break
                        if check in obstacles:
                            # Не считаем уже зарезервированные препятствия
                            if check not in reserved_obstacles:
                                hit_obstacles.add(check)
                            break

                # Подсчёт очков по правилам игры: 1+2+3+4=10 за 4 препятствия
                obs_count = len(hit_obstacles)
                score = sum(range(1, obs_count + 1))  # 1=1, 2=3, 3=6, 4=10

                # Бонус за мобов (+10 очков)
                for mob in arena.get('mobs', []):
                    mx, my = mob['pos']
                    if mx == pos[0] and abs(my - pos[1]) <= bomb_radius:
                        score += 10
                    elif my == pos[1] and abs(mx - pos[0]) <= bomb_radius:
                        score += 10

                # Бонус за врагов (+10 очков)
                for enemy in arena.get('enemies', []):
                    ex, ey = enemy['pos']
                    if ex == pos[0] and abs(ey - pos[1]) <= bomb_radius:
                        score += 10
                    elif ey == pos[1] and abs(ex - pos[0]) <= bomb_radius:
                        score += 10

                # Записываем все цели с очками
                if score >= 1:
                    # Приоритет ближним позициям
                    distance = abs(dx) + abs(dy)
                    candidates.append((pos, score, distance, hit_obstacles))

        if not candidates:
            return []

        # Сортируем: больше очков, меньше дистанция
        candidates.sort(key=lambda x: (-x[1], x[2]))
        return [(c[0], c[1], c[3]) for c in candidates]  # [(позиция, score, hit_obstacles), ...]

    def get_patrol_target(self, pos: Tuple[int, int], walls: Set, obstacles: Set,
                          bombs: Set, map_size: Tuple[int, int], danger: Set) -> Optional[Tuple[int, int]]:
        """Получить следующую точку патрулирования по краю карты (по часовой стрелке)"""
        x, y = pos
        # Края карты с учётом ограды в 1 блок
        min_edge, max_x, max_y = 1, map_size[0] - 2, map_size[1] - 2

        # Определяем на каком краю находимся и куда двигаться
        on_top = (y == min_edge)
        on_bottom = (y == max_y)
        on_left = (x == min_edge)
        on_right = (x == max_x)

        # Список целевых точек по приоритету (движение по часовой стрелке)
        targets = []

        if on_top and not on_right:
            # Верхний край - идём вправо
            for dx in range(1, 10):
                targets.append((min(x + dx, max_x), min_edge))
        elif on_right and not on_bottom:
            # Правый край - идём вниз
            for dy in range(1, 10):
                targets.append((max_x, min(y + dy, max_y)))
        elif on_bottom and not on_left:
            # Нижний край - идём влево
            for dx in range(1, 10):
                targets.append((max(x - dx, min_edge), max_y))
        elif on_left and not on_top:
            # Левый край - идём вверх
            for dy in range(1, 10):
                targets.append((min_edge, max(y - dy, min_edge)))
        else:
            # Не на краю - идём к ближайшему краю
            edges = [
                (min_edge, y),      # Левый край
                (max_x, y),         # Правый край
                (x, min_edge),      # Верхний край
                (x, max_y),         # Нижний край
            ]
            edges.sort(key=lambda e: abs(e[0] - x) + abs(e[1] - y))
            targets = edges

        # Находим первую проходимую точку
        for target in targets:
            if target == pos:
                continue
            if target in walls or target in obstacles or target in bombs:
                continue
            if target in danger:
                continue
            if self.is_walkable(target, walls, obstacles, bombs):
                return target

        return None

    def calculate_action(self, bomber: Dict, arena: Dict, reserved_targets: Set = None,
                         reserved_obstacles: Set = None, planned_bombs: Set = None) -> Tuple[Dict, Set]:
        """Рассчитать действие для бомбера. Возвращает (action, hit_obstacles)"""
        bomber_id = bomber['id']
        bomber_pos = tuple(bomber['pos'])
        bombs_available = bomber.get('bombs_available', 0)

        action = {'id': bomber_id, 'path': [], 'bombs': [], 'target': None, 'reason': 'IDLE', 'details': {}}
        hit_obstacles = set()  # Препятствия которые будут уничтожены этой бомбой

        if planned_bombs is None:
            planned_bombs = set()

        # Подготовка данных
        walls = set(tuple(w) for w in arena['arena'].get('walls', []))
        obstacles = set(tuple(o) for o in arena['arena'].get('obstacles', []))
        bombs = set(tuple(b['pos']) for b in arena['arena'].get('bombs', []))
        map_size = tuple(arena['map_size'])
        bomb_radius = self.get_bomb_radius(arena)
        bomber_speed = self.get_bomber_speed(arena)  # клеток/сек
        bomb_timer = self.get_bomb_timer(arena)      # сек до взрыва
        max_escape_steps = int(bomber_speed * bomb_timer)  # макс шагов для побега

        bomb_danger = self.get_bomb_danger_zones(arena)
        mob_danger = self.get_mob_danger_zones(arena)
        all_danger = bomb_danger | mob_danger

        # Добавляем danger zones от бомб которые планируют поставить другие бомберы
        for planned_bomb_pos in planned_bombs:
            all_danger |= self.get_explosion_zone(planned_bomb_pos, walls, obstacles, radius=bomb_radius)

        if reserved_targets is None:
            reserved_targets = set()
        if reserved_obstacles is None:
            reserved_obstacles = set()

        # 1. Если в опасности - бежать!
        if bomber_pos in all_danger:
            safe_pos = self.find_safe_position(bomber_pos, arena, walls, obstacles, bombs, all_danger)
            if safe_pos and safe_pos != bomber_pos:
                path = self.bfs_path(bomber_pos, safe_pos, walls, obstacles, bombs, set(), map_size)
                if path and len(path) > 1:
                    action['path'] = [list(p) for p in path[1:min(len(path), 11)]]
                    action['reason'] = 'ESCAPE'
                    action['details'] = {'from': list(bomber_pos), 'to': list(safe_pos),
                                         'in_bomb_danger': bomber_pos in bomb_danger,
                                         'in_mob_danger': bomber_pos in mob_danger}
                    print(f"  [{bomber_id[:8]}] ESCAPE to {safe_pos}")
                    return (action, hit_obstacles)

        # 2. Если есть бомбы - ищем цель с приоритетами:
        #    - Сначала 3+ препятствий (score >= 6)
        #    - Если нет хороших целей - разведка неизведанных областей
        #    - Если заблокирован - пробиваем путь (2 препятствия, потом 1)
        if bombs_available > 0:
            candidates = self.find_bomb_target(bomber_pos, arena, walls, obstacles, bombs,
                                               all_danger, reserved_targets, reserved_obstacles)

            # Разделяем кандидатов по приоритету
            high_value = [(t, s, h) for t, s, h in candidates if s >= 6]     # 3+ препятствий
            medium_value = [(t, s, h) for t, s, h in candidates if 3 <= s < 6]  # 2 препятствия
            low_value = [(t, s, h) for t, s, h in candidates if s < 3]       # 1 препятствие

            # Функция проверки и установки бомбы для кандидата
            def try_bomb_candidate(target_pos, score, hit_obs):
                nonlocal action, hit_obstacles

                # Вычисляем опасную зону после установки бомбы
                future_danger = all_danger.copy()
                future_danger |= self.get_explosion_zone(target_pos, walls, obstacles, radius=bomb_radius)

                # Если уже на позиции цели - ставим бомбу
                if target_pos == bomber_pos:
                    safe_pos = self.find_safe_position(bomber_pos, arena, walls, obstacles,
                                                       bombs | {bomber_pos}, future_danger)
                    if safe_pos and safe_pos != bomber_pos:
                        escape_path = self.bfs_path(bomber_pos, safe_pos, walls, obstacles,
                                                   bombs | {bomber_pos}, set(), map_size)
                        if escape_path and len(escape_path) > 1 and len(escape_path) - 1 <= max_escape_steps:
                            action['path'] = [list(p) for p in escape_path[1:30]]
                            action['bombs'] = [list(bomber_pos)]
                            action['target'] = target_pos
                            action['reason'] = 'BOMB_HERE'
                            action['details'] = {'bomb_pos': list(target_pos), 'score': score,
                                                 'escape_to': list(safe_pos), 'escape_steps': len(escape_path) - 1,
                                                 'max_steps': max_escape_steps}
                            hit_obstacles = hit_obs
                            print(f"  [{bomber_id[:8]}] BOMB HERE ({len(hit_obs)} obs, {score} pts), escape {len(escape_path)-1}/{max_escape_steps} steps")
                            return True
                    return False

                # Если цель рядом (1 клетка)
                dist_to_target = abs(target_pos[0] - bomber_pos[0]) + abs(target_pos[1] - bomber_pos[1])
                if dist_to_target == 1:
                    if self.is_dead_end(target_pos, walls, obstacles, bombs, map_size):
                        return False

                    safe_pos = self.find_safe_position(target_pos, arena, walls, obstacles,
                                                       bombs | {target_pos}, future_danger)
                    if safe_pos:
                        escape_path = self.bfs_path(target_pos, safe_pos, walls, obstacles,
                                                   bombs | {target_pos}, set(), map_size)
                        if escape_path and len(escape_path) > 1 and (1 + len(escape_path) - 1) <= max_escape_steps:
                            action['path'] = [list(target_pos)] + [list(p) for p in escape_path[1:29]]
                            action['bombs'] = [list(target_pos)]
                            action['target'] = target_pos
                            action['reason'] = 'BOMB_NEXT'
                            action['details'] = {'bomb_pos': list(target_pos), 'score': score,
                                                 'escape_to': list(safe_pos), 'escape_steps': len(escape_path),
                                                 'max_steps': max_escape_steps}
                            hit_obstacles = hit_obs
                            print(f"  [{bomber_id[:8]}] BOMB NEXT at {target_pos} ({len(hit_obs)} obs, {score} pts), escape {len(escape_path)}/{max_escape_steps} steps")
                            return True
                    return False

                # Строим путь к цели
                path = self.bfs_path(bomber_pos, target_pos, walls, obstacles, bombs, all_danger, map_size)
                max_approach_steps = max(3, max_escape_steps // 2)

                if path and len(path) > 1 and len(path) <= max_approach_steps + 1:
                    # Проверяем промежуточные позиции на тупики
                    for step_pos in path[1:]:
                        if self.is_dead_end(step_pos, walls, obstacles, bombs, map_size):
                            return False

                    safe_pos = self.find_safe_position(target_pos, arena, walls, obstacles,
                                                       bombs | {target_pos}, future_danger)
                    if safe_pos:
                        escape_path = self.bfs_path(target_pos, safe_pos, walls, obstacles,
                                                   bombs | {target_pos}, set(), map_size)
                        if escape_path and len(escape_path) > 1 and len(escape_path) - 1 <= max_escape_steps:
                            full_path = path[1:] + escape_path[1:]
                            action['path'] = [list(p) for p in full_path[:30]]
                            action['bombs'] = [list(target_pos)]
                            action['target'] = target_pos
                            action['reason'] = 'BOMB_TARGET'
                            action['details'] = {'bomb_pos': list(target_pos), 'score': score,
                                                 'escape_to': list(safe_pos), 'path_length': len(path),
                                                 'escape_steps': len(escape_path) - 1, 'max_steps': max_escape_steps}
                            hit_obstacles = hit_obs
                            print(f"  [{bomber_id[:8]}] BOMB at {target_pos} ({len(hit_obs)} obs, {score} pts), escape {len(escape_path)-1}/{max_escape_steps} steps")
                            return True

                return False

            # 1. Сначала пробуем high_value цели (3+ препятствий)
            for target_pos, score, hit_obs in high_value:
                if try_bomb_candidate(target_pos, score, hit_obs):
                    return (action, hit_obstacles)

            # 2. Если high_value не сработал - пробуем exploration
            exploration_targets = self.world_memory.find_exploration_targets(
                bomber_pos, walls, obstacles, bombs)
            for explore_target in exploration_targets:
                explore_path = self.bfs_path(bomber_pos, explore_target, walls, obstacles,
                                              bombs, all_danger, map_size)
                if explore_path and len(explore_path) > 1:
                    action['path'] = [list(p) for p in explore_path[1:min(len(explore_path), 11)]]
                    action['reason'] = 'EXPLORE'
                    action['details'] = {'target': list(explore_target),
                                         'explored_pct': len(self.world_memory.explored) * 100 //
                                                        max(1, map_size[0] * map_size[1])}
                    print(f"  [{bomber_id[:8]}] EXPLORE to {explore_target}")
                    return (action, hit_obstacles)

            # 3. Если exploration недоступен - пробуем medium + low (пробиваем путь)
            for target_pos, score, hit_obs in medium_value + low_value:
                if try_bomb_candidate(target_pos, score, hit_obs):
                    return (action, hit_obstacles)

        # 3. Нет бомб или нет целей - разведка или движение к препятствию
        # Сначала пробуем разведку
        exploration_targets = self.world_memory.find_exploration_targets(
            bomber_pos, walls, obstacles, bombs)

        for explore_target in exploration_targets:
            explore_path = self.bfs_path(bomber_pos, explore_target, walls, obstacles,
                                          bombs, all_danger, map_size)
            if explore_path and len(explore_path) > 1:
                action['path'] = [list(p) for p in explore_path[1:min(len(explore_path), 11)]]
                action['reason'] = 'EXPLORE'
                action['details'] = {'target': list(explore_target),
                                     'explored_pct': len(self.world_memory.explored) * 100 //
                                                    max(1, map_size[0] * map_size[1])}
                print(f"  [{bomber_id[:8]}] EXPLORE to {explore_target}")
                return (action, hit_obstacles)

        # Если разведка недоступна - идём к препятствию
        if obstacles:
            # Проверяем, есть ли препятствие рядом (1 клетка)
            adjacent_obstacle = None
            for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
                check_pos = (bomber_pos[0] + dx, bomber_pos[1] + dy)
                if check_pos in obstacles:
                    adjacent_obstacle = check_pos
                    break

            # Если нет бомб и уже рядом с препятствием - ждём
            if bombs_available == 0 and adjacent_obstacle:
                action['reason'] = 'WAIT_FOR_BOMB'
                action['details'] = {'pos': list(bomber_pos), 'adjacent_obstacle': list(adjacent_obstacle)}
                print(f"  [{bomber_id[:8]}] WAIT at {bomber_pos} (no bombs, obstacle at {adjacent_obstacle})")
                return (action, hit_obstacles)

            sorted_obstacles = sorted(obstacles,
                                      key=lambda o: abs(o[0]-bomber_pos[0]) + abs(o[1]-bomber_pos[1]))

            for obs in sorted_obstacles[:20]:
                # Ищем свободную клетку рядом с препятствием
                for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
                    near_pos = (obs[0] + dx, obs[1] + dy)

                    if near_pos in walls or near_pos in obstacles or near_pos in bombs:
                        continue
                    if near_pos in all_danger:
                        continue
                    if near_pos in reserved_targets:
                        continue
                    if near_pos[0] < 0 or near_pos[0] >= map_size[0]:
                        continue
                    if near_pos[1] < 0 or near_pos[1] >= map_size[1]:
                        continue
                    # Не заходим в тупик
                    if self.is_dead_end(near_pos, walls, obstacles, bombs, map_size):
                        continue

                    path = self.bfs_path(bomber_pos, near_pos, walls, obstacles, bombs, all_danger, map_size)
                    # Ограничиваем путь 10 шагами
                    if path and len(path) > 1 and len(path) <= 11:
                        # Проверяем промежуточные позиции на тупики
                        has_dead_end = False
                        for step_pos in path[1:-1]:  # Исключаем конечную (уже проверена)
                            if self.is_dead_end(step_pos, walls, obstacles, bombs, map_size):
                                has_dead_end = True
                                break

                        if not has_dead_end:
                            action['path'] = [list(p) for p in path[1:]]
                            action['target'] = near_pos
                            action['reason'] = 'MOVE_TO_OBSTACLE'
                            action['details'] = {'move_to': list(near_pos), 'obstacle': list(obs),
                                                 'path_length': len(path)}
                            print(f"  [{bomber_id[:8]}] MOVE to {near_pos} (obstacle {obs})")
                            return (action, hit_obstacles)

        # 5. Ничего не можем сделать
        action['details'] = {'pos': list(bomber_pos), 'bombs_available': bombs_available,
                            'obstacles_nearby': len([o for o in obstacles
                                                    if abs(o[0]-bomber_pos[0]) + abs(o[1]-bomber_pos[1]) < 5])}
        print(f"  [{bomber_id[:8]}] IDLE at {bomber_pos}")
        return (action, hit_obstacles)

    def run(self):
        print("Bot started! (Rate limit: 2 req/sec)")
        print("Visualizer: http://localhost:8888/arena")
        print("-" * 50)

        iteration = 0
        prev_score = 0
        prev_round = None

        while True:
            try:
                arena = self.get_arena()

                if arena.get('errors'):
                    print(f"Server errors: {arena['errors']}")

                # Проверка смены раунда - сброс памяти
                current_round = arena.get('round', 'unknown')
                if current_round != prev_round:
                    if prev_round is not None:
                        print(f">>> NEW ROUND: {prev_round} -> {current_round}, resetting memory")
                    self.world_memory.reset()
                    prev_round = current_round

                # Обновляем память карты
                self.world_memory.update(arena)

                # Получаем merged данные (видимое + запомненное)
                merged_arena = self.world_memory.get_merged_arena(arena)

                bombers = merged_arena.get('bombers', [])
                score = merged_arena.get('raw_score', 0)
                alive = sum(1 for b in bombers if b.get('alive'))

                if score != prev_score:
                    diff = score - prev_score
                    print(f">>> SCORE: {prev_score} -> {score} ({'+' if diff > 0 else ''}{diff})")
                    prev_score = score

                # Вывод статистики памяти
                mem = merged_arena.get('_memory', {})
                explored_pct = (mem.get('explored_cells', 0) / max(mem.get('total_cells', 1), 1)) * 100
                print(f"\n[{iteration}] Score: {score}, Alive: {alive}/{len(bombers)}, "
                      f"Explored: {explored_pct:.1f}%, Obstacles: {mem.get('remembered_obstacles', 0)}")

                # Собираем команды только для живых и готовых бомберов
                commands = []
                actions = []  # Все действия для логирования
                reserved_targets = set()  # Занятые позиции для бомб
                reserved_obstacles = set()  # Препятствия которые уже будут уничтожены
                planned_bombs = set()  # Позиции бомб которые планируют поставить

                for bomber in bombers:
                    if not bomber.get('alive'):
                        actions.append({'id': bomber['id'], 'reason': 'DEAD', 'details': {}})
                        continue
                    if not bomber.get('can_move', True):
                        actions.append({'id': bomber['id'], 'reason': 'MOVING', 'details': {}})
                        continue

                    action, hit_obstacles = self.calculate_action(bomber, merged_arena, reserved_targets,
                                                                   reserved_obstacles, planned_bombs)
                    actions.append(action)

                    # Резервируем цель, препятствия и бомбы
                    if action.get('target'):
                        reserved_targets.add(action['target'])
                    reserved_obstacles.update(hit_obstacles)
                    # Добавляем запланированные бомбы в danger
                    for bomb_pos in action.get('bombs', []):
                        planned_bombs.add(tuple(bomb_pos))

                    if action['path'] or action['bombs']:
                        # Убираем служебные поля перед отправкой
                        cmd = {'id': action['id'], 'path': action['path'], 'bombs': action['bombs']}
                        commands.append(cmd)

                # Отправляем команды
                move_result = None
                if commands:
                    move_result = self.move({'bombers': commands})
                    if move_result.get('errors'):
                        print(f"Move errors: {move_result['errors']}")

                # Покупаем бустеры если есть skill_points
                self.try_buy_boosters(arena)

                # Логируем итерацию (с merged данными)
                self.log_iteration(merged_arena, actions, commands, move_result, iteration)

                # Обновляем shared_data для визуализатора (с merged данными)
                with shared_data['lock']:
                    shared_data['arena'] = merged_arena
                    shared_data['iteration'] = iteration
                    shared_data['actions'] = actions

                iteration += 1

            except KeyboardInterrupt:
                print("\nBot stopped by user")
                break
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(1)


if __name__ == '__main__':
    import sys

    try:
        from config import TEST_SERVER, PROD_SERVER, AUTH_TOKEN

        if '--prod' in sys.argv:
            API_URL = PROD_SERVER
            print("Using PRODUCTION server")
        else:
            API_URL = TEST_SERVER
            print("Using TEST server")

    except ImportError:
        API_URL = 'https://games-test.datsteam.dev'
        AUTH_TOKEN = sys.argv[1] if len(sys.argv) > 1 else ''

        if not AUTH_TOKEN:
            print("Usage: python bot.py <token> [--prod]")
            sys.exit(1)

    # Запуск HTTP сервера для визуализатора
    start_data_server(8888)

    bot = BombermanBot(API_URL, AUTH_TOKEN)
    bot.run()
