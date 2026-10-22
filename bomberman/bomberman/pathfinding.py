"""
Алгоритмы поиска пути и навигации
"""

from collections import deque
from typing import List, Tuple, Set, Optional


def get_neighbors(pos: Tuple[int, int]) -> List[Tuple[int, int]]:
    """4 соседние клетки"""
    x, y = pos
    return [(x+1, y), (x-1, y), (x, y+1), (x, y-1)]


def is_walkable(pos: Tuple[int, int], walls: Set, obstacles: Set, bombs: Set) -> bool:
    """Можно ли пройти через клетку"""
    return pos not in walls and pos not in obstacles and pos not in bombs


def count_exits(pos: Tuple[int, int], walls: Set, obstacles: Set, bombs: Set,
                map_size: Tuple[int, int]) -> int:
    """Подсчёт свободных выходов из позиции"""
    count = 0
    for neighbor in get_neighbors(pos):
        if neighbor[0] < 0 or neighbor[0] >= map_size[0]:
            continue
        if neighbor[1] < 0 or neighbor[1] >= map_size[1]:
            continue
        if is_walkable(neighbor, walls, obstacles, bombs):
            count += 1
    return count


def is_dead_end(pos: Tuple[int, int], walls: Set, obstacles: Set, bombs: Set,
                map_size: Tuple[int, int], min_exits: int = 2) -> bool:
    """Проверка является ли позиция тупиком (менее min_exits выходов)"""
    return count_exits(pos, walls, obstacles, bombs, map_size) < min_exits


def bfs_path(start: Tuple[int, int], goal: Tuple[int, int],
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

        for neighbor in get_neighbors(current):
            if neighbor in visited:
                continue

            # Проверка границ карты
            if map_size:
                if neighbor[0] < 0 or neighbor[0] >= map_size[0]:
                    continue
                if neighbor[1] < 0 or neighbor[1] >= map_size[1]:
                    continue

            if not is_walkable(neighbor, walls, obstacles, bombs):
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
