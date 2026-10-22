"""
Память карты с учётом тумана войны (fog of war)
"""

from typing import List, Tuple, Dict, Set


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
        vision_level = boosters.get('vision', 0)
        return self.base_view_radius + (vision_level * 3)

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
