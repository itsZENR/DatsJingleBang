"""
Расчёт каскадных (цепных) взрывов
"""

from typing import List, Tuple, Dict, Set


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
