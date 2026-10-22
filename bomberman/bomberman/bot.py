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

import time
import json
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Tuple, Set, List, Optional

from .strategy_config import StrategyConfig
from .world_memory import WorldMemory
from .cascade import CascadeCalculator
from .api_client import BombermanAPIClient
from .visualizer import shared_data
from .game_params import get_bomb_radius, get_bomber_speed, get_bomb_timer
from .pathfinding import bfs_path, is_dead_end
from .danger import get_explosion_zone, get_bomb_danger_zones, get_mob_danger_zones, find_safe_position
from .tactics import find_bomb_target, get_patrol_target


class BombermanBot:
    """Основной класс бота для игры Bomberman"""

    def __init__(self, api_url: str, auth_token: str):
        # API клиент
        self.api_client = BombermanAPIClient(api_url, auth_token)

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

        # Отслеживание времени раунда для skill_points
        self.round_start_time = None  # datetime начала текущего раунда (UTC)
        self.round_end_time = None    # datetime конца текущего раунда (UTC)
        self.round_info_initialized = False  # Флаг инициализации информации о раунде
        self.available_boosters = []  # Список доступных бустеров из API
        self.booster_state = {}  # Текущее состояние бустеров (уровни)
        self.last_skill_point_count = 0  # Последнее кол-во skill_points (для детекции новых)

    def _convert_state_to_levels(self, state: Dict) -> Dict:
        """Конвертировать абсолютные значения из /api/booster state в уровни апгрейдов"""
        levels = {}

        # bomb_delay: базовое 8000мс, каждый уровень -2000мс
        bomb_delay_ms = state.get('bomb_delay', 8000)
        if isinstance(bomb_delay_ms, (int, float)) and bomb_delay_ms < 8000:
            levels['bomb_delay'] = int((8000 - bomb_delay_ms) / 2000)

        # bomb_range: базовое 1
        bomb_range = state.get('bomb_range', 1)
        if isinstance(bomb_range, int) and bomb_range > 1:
            levels['bomb_range'] = bomb_range - 1

        # bombs -> bomb_count: базовое 1
        bombs = state.get('bombs', 1)
        if isinstance(bombs, int) and bombs > 1:
            levels['bomb_count'] = bombs - 1

        # speed: базовое 2
        speed = state.get('speed', 2)
        if isinstance(speed, int) and speed > 2:
            levels['speed'] = speed - 2

        # armor: базовое 0
        armor = state.get('armor', 0)
        if isinstance(armor, int) and armor > 0:
            levels['armor'] = armor

        # view -> vision: базовое 5, каждый уровень +3
        view = state.get('view', 5)
        if isinstance(view, int) and view > 5:
            levels['vision'] = int((view - 5) / 3)

        # bombers -> bomber_count: базовое 6
        bombers = state.get('bombers', 6)
        if isinstance(bombers, int) and bombers > 6:
            levels['bomber_count'] = bombers - 6

        # passability: на основе can_pass_bombs (каждый уровень даёт новую способность)
        passability = 0
        if state.get('can_pass_bombs'):
            passability += 1
        if state.get('can_pass_obstacles'):
            passability += 1
        if state.get('can_pass_walls'):
            passability += 1
        if passability > 0:
            levels['passability'] = passability

        return levels

    def _calculate_skill_points(self) -> int:
        """Рассчитать текущее кол-во skill points на основе времени раунда"""
        if not self.round_start_time:
            return 0
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        elapsed_seconds = (now_utc - self.round_start_time).total_seconds()
        # 1 поинт каждые 90 секунд (1.5 минуты), максимум 10
        return min(int(elapsed_seconds // 90), 10)

    def _get_remaining_time_seconds(self) -> float:
        """Получить оставшееся время раунда в секундах"""
        if not self.round_end_time:
            return float('inf')  # Неизвестно - считаем бесконечным
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        remaining = (self.round_end_time - now_utc).total_seconds()
        return max(0, remaining)

    def _initialize_round_info(self):
        """Инициализировать информацию о раунде: время начала и доступные бустеры (ОДИН РАЗ)"""
        if self.round_info_initialized:
            return

        print("Initializing round info from API...")

        try:
            # Запрос к /api/rounds для получения времени начала раунда
            rounds_data = self.api_client.get_rounds()
            rounds = rounds_data.get('rounds', [])

            # Ищем активный раунд
            active_round = None
            for round_info in rounds:
                if round_info.get('status') == 'active':
                    active_round = round_info
                    break

            if active_round:
                start_at_str = active_round.get('startAt')
                end_at_str = active_round.get('endAt')
                if start_at_str:
                    # Парсим время начала раунда (формат ISO 8601 в UTC)
                    round_start_utc = datetime.fromisoformat(start_at_str.replace('Z', '+00:00'))
                    self.round_start_time = round_start_utc.replace(tzinfo=None)
                    print(f"  Round start time from API (UTC): {self.round_start_time}")
                else:
                    print("  WARNING: No startAt in active round, using current time")
                    self.round_start_time = datetime.now(timezone.utc).replace(tzinfo=None)
                if end_at_str:
                    round_end_utc = datetime.fromisoformat(end_at_str.replace('Z', '+00:00'))
                    self.round_end_time = round_end_utc.replace(tzinfo=None)
                    print(f"  Round end time from API (UTC): {self.round_end_time}")
            else:
                print("  WARNING: No active round found, using current time")
                self.round_start_time = datetime.now(timezone.utc).replace(tzinfo=None)
                self.round_end_time = None

            # Запрос к /api/booster для получения доступных бустеров и текущего состояния
            booster_data = self.api_client.get_booster_info()
            self.available_boosters = booster_data.get('available', [])
            raw_state = booster_data.get('state', {})
            unspent_points = raw_state.get('points', 0)

            print(f"  Raw booster state from API: {raw_state}")
            print(f"  Unspent skill points: {unspent_points}")

            # Конвертируем абсолютные значения в уровни апгрейдов
            self.booster_state = self._convert_state_to_levels(raw_state)

            print(f"  Available boosters: {len(self.available_boosters)} types")
            for b in self.available_boosters:
                print(f"    - {b}")
            print(f"  Converted upgrade levels: {self.booster_state}")

            # Тратим все накопленные поинты сразу при старте
            if unspent_points > 0:
                print(f"  Spending {unspent_points} unspent skill points...")
                arena_for_purchase = {'boosters': self.booster_state.copy()}
                for _ in range(unspent_points):
                    self.api_client.try_buy_boosters(arena_for_purchase)
                    self.booster_state = arena_for_purchase.get('boosters', {}).copy()

            # Запоминаем текущее кол-во поинтов (по времени) для детекции новых
            self.last_skill_point_count = self._calculate_skill_points()
            print(f"  Current skill points by time: {self.last_skill_point_count}")

            self.round_info_initialized = True

        except Exception as e:
            print(f"  ERROR initializing round info: {e}")
            # Fallback на текущее время (UTC)
            if self.round_start_time is None:
                self.round_start_time = datetime.now(timezone.utc).replace(tzinfo=None)
            self.round_info_initialized = True  # Помечаем как инициализированное чтобы не повторять

    def _load_round_start_time(self) -> Optional[datetime]:
        """Загрузить время начала раунда из лога (если существует)"""
        if not self.current_log_file or not self.current_log_file.exists():
            return None

        try:
            with open(self.current_log_file, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                if first_line:
                    entry = json.loads(first_line)
                    timestamp_str = entry.get('timestamp')
                    if timestamp_str:
                        return datetime.fromisoformat(timestamp_str)
        except Exception:
            pass
        return None

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

    def calculate_action(self, bomber: Dict, arena: Dict, reserved_targets: Set = None,
                         reserved_obstacles: Set = None, planned_bombs: Set = None,
                         remaining_time: float = float('inf'), alive_count: int = 999) -> Tuple[Dict, Set]:
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
        bomb_radius = get_bomb_radius(arena)
        bomber_speed = get_bomber_speed(arena)  # клеток/сек
        bomb_timer = get_bomb_timer(arena)      # сек до взрыва
        max_escape_steps = int(bomber_speed * bomb_timer)  # макс шагов для побега

        bomb_danger = get_bomb_danger_zones(arena)
        mob_danger = get_mob_danger_zones(arena)
        all_danger = bomb_danger | mob_danger

        # Добавляем danger zones от бомб которые планируют поставить другие бомберы
        for planned_bomb_pos in planned_bombs:
            all_danger |= get_explosion_zone(planned_bomb_pos, walls, obstacles, radius=bomb_radius)

        if reserved_targets is None:
            reserved_targets = set()
        if reserved_obstacles is None:
            reserved_obstacles = set()

        # 0. SUICIDE: если осталось < 5 минут и один бомбер - самоубийство для респавна
        if remaining_time < 300 and alive_count == 1 and bombs_available > 0:
            print(f"  [{bomber_id[:8]}] SUICIDE (remaining: {remaining_time:.0f}s, alive: {alive_count})")
            action['bombs'] = [list(bomber_pos)]
            action['reason'] = 'SUICIDE'
            action['details'] = {'remaining_time': remaining_time, 'alive_count': alive_count}
            return (action, hit_obstacles)

        # 1. Если в опасности - бежать!
        if bomber_pos in all_danger:
            safe_pos = find_safe_position(bomber_pos, arena, walls, obstacles, bombs, all_danger)
            if safe_pos and safe_pos != bomber_pos:
                path = bfs_path(bomber_pos, safe_pos, walls, obstacles, bombs, set(), map_size)
                if path and len(path) > 1:
                    action['path'] = [list(p) for p in path[1:min(len(path), 11)]]
                    action['reason'] = 'ESCAPE'
                    action['details'] = {'from': list(bomber_pos), 'to': list(safe_pos),
                                         'in_bomb_danger': bomber_pos in bomb_danger,
                                         'in_mob_danger': bomber_pos in mob_danger}
                    print(f"  [{bomber_id[:8]}] ESCAPE to {safe_pos}")
                    return (action, hit_obstacles)

        # 1.5. HUNT_MOB: охота на мобов (высокий приоритет - 20 очков за моба)
        active_mobs = [m for m in arena.get('mobs', []) if not m.get('is_sleeping', False)]
        if active_mobs:
            # Находим ближайшего моба
            def mob_distance(m):
                mx, my = m['pos']
                return abs(mx - bomber_pos[0]) + abs(my - bomber_pos[1])

            active_mobs.sort(key=mob_distance)
            nearest_mob = active_mobs[0]
            mob_pos = tuple(nearest_mob['pos'])
            dist_to_mob = mob_distance(nearest_mob)

            # Если моб в радиусе бомбы и есть бомба - атакуем!
            if bombs_available > 0 and dist_to_mob <= bomb_radius:
                # Проверяем можем ли поставить бомбу и убежать
                future_danger = all_danger.copy()
                future_danger |= get_explosion_zone(bomber_pos, walls, obstacles, radius=bomb_radius)

                safe_pos = find_safe_position(bomber_pos, arena, walls, obstacles,
                                              bombs | {bomber_pos}, future_danger)
                if safe_pos and safe_pos != bomber_pos:
                    escape_path = bfs_path(bomber_pos, safe_pos, walls, obstacles,
                                           bombs | {bomber_pos}, future_danger, map_size)
                    if escape_path and len(escape_path) > 1 and len(escape_path) - 1 <= max_escape_steps:
                        action['path'] = [list(p) for p in escape_path[1:30]]
                        action['bombs'] = [list(bomber_pos)]
                        action['reason'] = 'HUNT_MOB'
                        action['details'] = {'mob_pos': list(mob_pos), 'dist': dist_to_mob,
                                            'escape_to': list(safe_pos)}
                        print(f"  [{bomber_id[:8]}] HUNT_MOB at {bomber_pos}, mob at {mob_pos} (dist={dist_to_mob})")
                        return (action, hit_obstacles)

            # Если моб далеко - идём к нему (на расстояние атаки)
            if dist_to_mob > bomb_radius and dist_to_mob <= 15:
                # Ищем позицию рядом с мобом для атаки
                attack_positions = []
                for dx, dy in [(1,0), (-1,0), (0,1), (0,-1), (2,0), (-2,0), (0,2), (0,-2)]:
                    attack_pos = (mob_pos[0] + dx, mob_pos[1] + dy)
                    if attack_pos in walls or attack_pos in obstacles or attack_pos in bombs:
                        continue
                    if attack_pos in all_danger:
                        continue
                    attack_positions.append(attack_pos)

                for attack_pos in attack_positions:
                    path_to_mob = bfs_path(bomber_pos, attack_pos, walls, obstacles, bombs, all_danger, map_size)
                    if path_to_mob and len(path_to_mob) > 1 and len(path_to_mob) <= 12:
                        action['path'] = [list(p) for p in path_to_mob[1:min(len(path_to_mob), 11)]]
                        action['reason'] = 'APPROACH_MOB'
                        action['details'] = {'mob_pos': list(mob_pos), 'target': list(attack_pos),
                                            'dist': dist_to_mob}
                        print(f"  [{bomber_id[:8]}] APPROACH_MOB to {attack_pos}, mob at {mob_pos}")
                        return (action, hit_obstacles)

        # 2. Если есть бомбы - ищем цель с приоритетами:
        #    - Сначала 3+ препятствий (score >= 6)
        #    - Если нет хороших целей - разведка неизведанных областей
        #    - Если заблокирован - пробиваем путь (2 препятствия, потом 1)
        if bombs_available > 0:
            candidates = find_bomb_target(bomber_pos, arena, walls, obstacles, bombs,
                                          all_danger, bomb_radius, reserved_targets, reserved_obstacles)

            # Разделяем кандидатов по приоритету
            high_value = [(t, s, h) for t, s, h in candidates if s >= 6]     # 3+ препятствий
            medium_value = [(t, s, h) for t, s, h in candidates if 3 <= s < 6]  # 2 препятствия
            low_value = [(t, s, h) for t, s, h in candidates if s < 3]       # 1 препятствие

            # Функция проверки и установки бомбы для кандидата
            def try_bomb_candidate(target_pos, score, hit_obs):
                nonlocal action, hit_obstacles

                # Вычисляем опасную зону после установки бомбы
                future_danger = all_danger.copy()
                future_danger |= get_explosion_zone(target_pos, walls, obstacles, radius=bomb_radius)

                # Если уже на позиции цели - ставим бомбу
                if target_pos == bomber_pos:
                    safe_pos = find_safe_position(bomber_pos, arena, walls, obstacles,
                                                   bombs | {bomber_pos}, future_danger)
                    if safe_pos and safe_pos != bomber_pos:
                        escape_path = bfs_path(bomber_pos, safe_pos, walls, obstacles,
                                               bombs | {bomber_pos}, future_danger, map_size)
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
                    if is_dead_end(target_pos, walls, obstacles, bombs, map_size):
                        return False

                    safe_pos = find_safe_position(target_pos, arena, walls, obstacles,
                                                   bombs | {target_pos}, future_danger)
                    if safe_pos:
                        escape_path = bfs_path(target_pos, safe_pos, walls, obstacles,
                                               bombs | {target_pos}, future_danger, map_size)
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
                path = bfs_path(bomber_pos, target_pos, walls, obstacles, bombs, all_danger, map_size)
                max_approach_steps = max(3, max_escape_steps // 2)

                if path and len(path) > 1 and len(path) <= max_approach_steps + 1:
                    # Проверяем промежуточные позиции на тупики
                    for step_pos in path[1:]:
                        if is_dead_end(step_pos, walls, obstacles, bombs, map_size):
                            return False

                    safe_pos = find_safe_position(target_pos, arena, walls, obstacles,
                                                   bombs | {target_pos}, future_danger)
                    if safe_pos:
                        escape_path = bfs_path(target_pos, safe_pos, walls, obstacles,
                                               bombs | {target_pos}, future_danger, map_size)
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
                explore_path = bfs_path(bomber_pos, explore_target, walls, obstacles,
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
            explore_path = bfs_path(bomber_pos, explore_target, walls, obstacles,
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

            # Если есть бомба и рядом препятствие - ставим бомбу и убегаем!
            if bombs_available > 0 and adjacent_obstacle:
                # Ищем куда убежать (любая свободная клетка)
                for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
                    escape_pos = (bomber_pos[0] + dx, bomber_pos[1] + dy)
                    if escape_pos in walls or escape_pos in obstacles or escape_pos in bombs:
                        continue
                    if escape_pos in all_danger:
                        continue
                    # Нашли куда бежать - ставим бомбу!
                    action['path'] = [list(escape_pos)]
                    action['bombs'] = [list(bomber_pos)]
                    action['reason'] = 'BOMB_DESPERATION'
                    action['details'] = {'bomb_pos': list(bomber_pos), 'obstacle': list(adjacent_obstacle),
                                        'escape_to': list(escape_pos)}
                    print(f"  [{bomber_id[:8]}] BOMB DESPERATION at {bomber_pos}, escape to {escape_pos}")
                    return (action, hit_obstacles)

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
                    if is_dead_end(near_pos, walls, obstacles, bombs, map_size):
                        continue

                    path = bfs_path(bomber_pos, near_pos, walls, obstacles, bombs, all_danger, map_size)
                    # Ограничиваем путь 10 шагами
                    if path and len(path) > 1 and len(path) <= 11:
                        # Проверяем промежуточные позиции на тупики
                        has_dead_end = False
                        for step_pos in path[1:-1]:  # Исключаем конечную (уже проверена)
                            if is_dead_end(step_pos, walls, obstacles, bombs, map_size):
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

        # 5. Fallback: двигаться к любой свободной клетке (не стоять на месте!)
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            neighbor = (bomber_pos[0] + dx, bomber_pos[1] + dy)
            if neighbor[0] < 0 or neighbor[0] >= map_size[0]:
                continue
            if neighbor[1] < 0 or neighbor[1] >= map_size[1]:
                continue
            if neighbor in walls or neighbor in obstacles or neighbor in bombs:
                continue
            if neighbor in all_danger:
                continue
            if neighbor in reserved_targets:
                continue
            # Нашли свободную клетку - идём туда
            action['path'] = [list(neighbor)]
            action['reason'] = 'WANDER'
            action['details'] = {'to': list(neighbor)}
            print(f"  [{bomber_id[:8]}] WANDER to {neighbor}")
            return (action, hit_obstacles)

        # 6. Совсем ничего не можем сделать
        action['details'] = {'pos': list(bomber_pos), 'bombs_available': bombs_available,
                            'obstacles_nearby': len([o for o in obstacles
                                                    if abs(o[0]-bomber_pos[0]) + abs(o[1]-bomber_pos[1]) < 5])}
        print(f"  [{bomber_id[:8]}] IDLE at {bomber_pos}")
        return (action, hit_obstacles)

    def run(self):
        """Главный цикл бота"""
        print("Bot started! (Rate limit: 3 req/sec)")
        print("Visualizer: http://localhost:8888/arena")
        print("-" * 50)

        iteration = 0
        prev_score = 0
        prev_round = None

        while True:
            try:
                arena = self.api_client.get_arena()

                if arena.get('errors'):
                    print(f"Server errors: {arena['errors']}")

                # Проверка смены раунда - сброс памяти
                current_round = arena.get('round', 'unknown')
                if current_round != prev_round:
                    if prev_round is not None:
                        print(f">>> NEW ROUND: {prev_round} -> {current_round}, resetting memory")
                    self.world_memory.reset()
                    # Сброс флага инициализации для нового раунда
                    self.round_info_initialized = False
                    self.last_skill_point_count = 0  # Сбрасываем счетчик поинтов
                    # Инициализируем информацию о раунде из API
                    self._initialize_round_info()
                    prev_round = current_round

                # Инициализация информации о раунде при первом запуске
                if not self.round_info_initialized:
                    self._initialize_round_info()

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
                remaining_time = self._get_remaining_time_seconds()
                remaining_min = int(remaining_time // 60)
                remaining_sec = int(remaining_time % 60)
                print(f"\n[{iteration}] Score: {score}, Alive: {alive}/{len(bombers)}, "
                      f"Time: {remaining_min}:{remaining_sec:02d}, "
                      f"Explored: {explored_pct:.1f}%")

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

                    action, hit_obstacles = self.calculate_action(
                        bomber, merged_arena, reserved_targets, reserved_obstacles, planned_bombs,
                        remaining_time=remaining_time, alive_count=alive
                    )
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
                    move_result = self.api_client.move({'bombers': commands})
                    if move_result.get('errors'):
                        print(f"Move errors: {move_result['errors']}")

                # Добавляем текущее состояние бустеров в merged_arena
                merged_arena['boosters'] = self.booster_state.copy() if self.booster_state else {}

                # Проверяем появление нового skill point (каждые 90 сек, макс 10)
                current_skill_points = self._calculate_skill_points()
                if current_skill_points > self.last_skill_point_count:
                    new_points = current_skill_points - self.last_skill_point_count
                    print(f">>> New skill point(s) detected: +{new_points} (total: {current_skill_points})")
                    self.last_skill_point_count = current_skill_points
                    # Покупаем на все новые поинты
                    for _ in range(new_points):
                        self.api_client.try_buy_boosters(merged_arena)
                        self.booster_state = merged_arena.get('boosters', {}).copy()

                # Логируем итерацию (с merged данными)
                self.log_iteration(merged_arena, actions, commands, move_result, iteration)

                # Обновляем shared_data для визуализатора (с merged данными)
                with shared_data['lock']:
                    shared_data['arena'] = merged_arena
                    shared_data['iteration'] = iteration
                    shared_data['actions'] = actions
                    shared_data['round_start_time'] = self.round_start_time.isoformat() if self.round_start_time else None

                iteration += 1

            except KeyboardInterrupt:
                print("\nBot stopped by user")
                break
            except requests.exceptions.Timeout:
                # Skip iteration on timeout - no sleep, continue immediately
                iteration += 1
                continue
            except requests.exceptions.ConnectionError as e:
                # Connection error - brief message, continue immediately
                print(f"Connection error (iteration {iteration})")
                iteration += 1
                continue
            except Exception as e:
                print(f"Error: {e}")
                # No sleep - continue immediately
                iteration += 1
                continue
