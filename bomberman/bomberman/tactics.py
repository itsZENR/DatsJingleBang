"""
Тактические решения: поиск целей для бомб, патрулирование
"""

from typing import Tuple, Set, Dict, Optional, List

from .pathfinding import is_walkable


def find_bomb_target(bomber_pos: Tuple[int, int], arena: Dict,
                    walls: Set, obstacles: Set, bombs: Set,
                    danger_zones: Set, bomb_radius: int,
                    exclude_positions: Set = None,
                    reserved_obstacles: Set = None) -> List[Tuple[Tuple[int, int], int, Set]]:
    """
    Найти лучшие позиции для бомбы.
    Возвращает список: [(позиция, score, set препятствий), ...]
    """
    map_size = tuple(arena['map_size'])
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
            if pos != bomber_pos and not is_walkable(pos, walls, obstacles, bombs):
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

            # Собираем зоны взрыва с учётом стен и препятствий
            explosion_cells = {pos}
            for ddx, ddy in [(1,0), (-1,0), (0,1), (0,-1)]:
                for dist in range(1, bomb_radius + 1):
                    cell = (pos[0] + ddx * dist, pos[1] + ddy * dist)
                    if cell in walls:
                        break
                    explosion_cells.add(cell)
                    if cell in obstacles:
                        break

            # Бонус за мобов (+20 очков)
            # Спящие мобы (is_sleeping=True) игнорируются - их нельзя взорвать
            for mob in arena.get('mobs', []):
                if mob.get('is_sleeping', False):
                    continue  # Спящего моба нельзя взорвать
                mx, my = mob['pos']
                if (mx, my) in explosion_cells:
                    score += 20

            # Бонус за врагов (+5 очков)
            for enemy in arena.get('enemies', []):
                ex, ey = enemy['pos']
                if (ex, ey) in explosion_cells:
                    score += 5

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


def get_patrol_target(pos: Tuple[int, int], walls: Set, obstacles: Set,
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
        if is_walkable(target, walls, obstacles, bombs):
            return target

    return None
