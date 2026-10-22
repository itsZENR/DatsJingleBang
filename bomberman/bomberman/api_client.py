"""
API клиент для взаимодействия с сервером игры
"""

import requests
from typing import Dict

from .rate_limiter import RateLimiter


class BombermanAPIClient:
    """Клиент для взаимодействия с API игры Bomberman"""

    def __init__(self, api_url: str, auth_token: str):
        self.api_url = api_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'X-Auth-Token': auth_token})
        self.rate_limiter = RateLimiter(max_requests=3, per_seconds=1.0)

    def _request_with_retry(self, method: str, endpoint: str, json_data: Dict = None,
                             max_retries: int = 1, timeout: int = 2) -> Dict:
        """Выполнить запрос с retry при таймаутах (по умолчанию: 1 retry, 2 сек timeout)"""
        last_error = None
        for attempt in range(max_retries):
            try:
                self.rate_limiter.wait()
                if method == 'GET':
                    response = self.session.get(f'{self.api_url}{endpoint}', timeout=timeout)
                else:
                    response = self.session.post(f'{self.api_url}{endpoint}', json=json_data, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < max_retries - 1:
                    print(f"Timeout on {endpoint} (attempt {attempt + 1}/{max_retries})")
            except requests.exceptions.ConnectionError as e:
                last_error = e
                if attempt < max_retries - 1:
                    print(f"Connection error on {endpoint} (attempt {attempt + 1}/{max_retries})")
        raise last_error

    def get_arena(self) -> Dict:
        """Получить текущее состояние арены"""
        return self._request_with_retry('GET', '/api/arena')

    def get_rounds(self) -> Dict:
        """Получить расписание раундов"""
        return self._request_with_retry('GET', '/api/rounds')

    def get_booster_info(self) -> Dict:
        """Получить информацию о доступных бустерах и текущем состоянии"""
        # Используем очень короткий timeout чтобы не блокировать основной цикл
        try:
            self.rate_limiter.wait()
            response = self.session.get(f'{self.api_url}/api/booster', timeout=1)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            # Возвращаем пустой результат при ошибке
            return {'available': [], 'state': {}}

    def move(self, commands: Dict) -> Dict:
        """Отправить команды движения"""
        return self._request_with_retry('POST', '/api/move', commands)

    def buy_booster(self, booster_type: str) -> Dict:
        """Купить бустер по типу (строка)"""
        # Используем очень короткий timeout для покупки бустеров чтобы не блокировать основной цикл
        try:
            self.rate_limiter.wait()
            response = self.session.post(f'{self.api_url}/api/booster',
                                        json={'booster': booster_type},
                                        timeout=1)  # Очень короткий timeout - 1 сек
            response.raise_for_status()
            return response.json()
        except Exception as e:
            # Возвращаем ошибку вместо exception чтобы не ломать цикл
            return {'code': 1, 'errors': [str(e)]}

    def try_buy_boosters(self, arena: Dict) -> bool:
        """
        Попробовать купить один бустер (вызывается когда известно что есть поинт)

        ВАЖНО: Все апгрейды применяются КО ВСЕМ бомберам одновременно!
        Стратегия покупки на 10 очков v3.0:
        1. bomb_delay до 1 (1 очко) - скорость взрыва
        2. bomb_count до 1 (1 очко) - кол-во бомб
        3. bomb_delay до 3 (2 очка) - скорость взрыва
        4. passability до 1 (2 очка) - прыжок через бомбы
        5. bomb_range до 2 (2 очка) - радиус взрыва
        6. speed до 2 (2 очка) - скорость передвижения

        Возвращает True если покупка успешна
        """
        # Текущее состояние бустеров (уровни)
        boosters = arena.get('boosters', {})

        # Стратегия покупки: (тип, целевой_уровень, название)
        purchase_targets = [
            ('bomb_delay', 1, 'Bomb Delay'),
            ('bomb_count', 1, 'Bomb Count'),
            ('bomb_delay', 3, 'Bomb Delay'),
            ('passability', 1, 'Passability'),
            ('bomb_range', 2, 'Bomb Range'),
            ('speed', 2, 'Speed'),
            # После основной стратегии
            ('bomb_range', 999, 'Bomb Range'),
            ('bomb_count', 999, 'Bomb Count'),
            ('speed', 3, 'Speed'),
            ('passability', 3, 'Passability'),
            ('armor', 999, 'Armor'),
        ]

        # Ищем первый апгрейд, где текущий уровень < целевого
        for booster_type, target_level, display_name in purchase_targets:
            current_level = boosters.get(booster_type, 0)
            if current_level >= target_level:
                continue

            # Делаем попытку покупки
            result = self.buy_booster(booster_type)

            if result.get('code', 0) == 0:
                new_level = current_level + 1
                print(f">>> BOUGHT {display_name} -> Level {new_level}")
                # Обновляем локальное состояние бустеров
                boosters[booster_type] = new_level
                arena['boosters'] = boosters
                return True
            else:
                errors = result.get('errors', [])
                print(f">>> FAILED to buy {booster_type}: {errors}")
                continue

        return False
