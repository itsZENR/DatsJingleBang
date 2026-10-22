"""
Расчет опасных зон от бомб и мобов
"""

from collections import deque
from typing import Tuple, Set, Dict, Optional

from .pathfinding import get_neighbors, is_walkable


def get_explosion_zone(pos: Tuple[int, int], walls: Set, obstacles: Set, radius: int = 1) -> Set[Tuple[int, int]]:
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


def get_bomb_danger_zones(arena: Dict) -> Set[Tuple[int, int]]:
    """Получить все опасные клетки от бомб"""
    danger = set()
    walls = set(tuple(w) for w in arena['arena'].get('walls', []))
    obstacles = set(tuple(o) for o in arena['arena'].get('obstacles', []))

    for bomb in arena['arena'].get('bombs', []):
        pos = tuple(bomb['pos'])
        radius = bomb.get('range', 1)
        danger |= get_explosion_zone(pos, walls, obstacles, radius)
    return danger


def get_mob_danger_zones(arena: Dict) -> Set[Tuple[int, int]]:
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


def find_safe_position(start: Tuple[int, int], arena: Dict,
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

        for neighbor in get_neighbors(current):
            if neighbor in visited:
                continue
            if neighbor[0] < 0 or neighbor[0] >= map_size[0]:
                continue
            if neighbor[1] < 0 or neighbor[1] >= map_size[1]:
                continue
            if not is_walkable(neighbor, walls, obstacles, bombs):
                continue

            visited.add(neighbor)
            queue.append(neighbor)

    return None
