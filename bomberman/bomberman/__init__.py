"""
Bomberman Bot - бот для игры DatsJingleBang
"""

from .bot import BombermanBot
from .strategy_config import StrategyConfig
from .world_memory import WorldMemory
from .cascade import CascadeCalculator
from .api_client import BombermanAPIClient

__version__ = '2.8'

__all__ = [
    'BombermanBot',
    'StrategyConfig',
    'WorldMemory',
    'CascadeCalculator',
    'BombermanAPIClient',
]
