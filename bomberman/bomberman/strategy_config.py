"""
Загрузка и управление настройками стратегии из JSON
"""

import json
from pathlib import Path


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
