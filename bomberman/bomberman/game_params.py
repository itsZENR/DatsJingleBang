"""
Параметры и константы игры
"""

from typing import Dict


def get_bomb_radius(arena: Dict) -> int:
    """Получить радиус бомбы: базовый 1 + уровень bomb_range"""
    boosters = arena.get('boosters', {})
    return 1 + boosters.get('bomb_range', 0)


def get_bomber_speed(arena: Dict) -> int:
    """Получить скорость бомбера: базовая 2 + уровень speed (max +3)"""
    boosters = arena.get('boosters', {})
    return 2 + min(boosters.get('speed', 0), 3)


def get_bomb_timer(arena: Dict) -> float:
    """Получить время до взрыва бомбы: базовое 8 сек, -2 за уровень bomb_delay (max 3)"""
    boosters = arena.get('boosters', {})
    delay_level = min(boosters.get('bomb_delay', 0), 3)
    return 8.0 - (delay_level * 2.0)
