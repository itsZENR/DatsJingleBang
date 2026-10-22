"""
Точка входа для запуска бота Bomberman
"""

import sys

from .bot import BombermanBot
from .visualizer import start_data_server


def main():
    """Главная функция запуска бота"""
    try:
        from .config import TEST_SERVER, PROD_SERVER, AUTH_TOKEN

        if '--prod' in sys.argv:
            API_URL = PROD_SERVER
            print("Using PRODUCTION server")
        else:
            API_URL = TEST_SERVER
            print("Using TEST server")

    except ImportError:
        API_URL = 'https://games-test.datsteam.dev'
        AUTH_TOKEN = sys.argv[1] if len(sys.argv) > 1 else ''

        if not AUTH_TOKEN:
            print("Usage: python -m bomberman.main <token> [--prod]")
            sys.exit(1)

    # Запуск HTTP сервера для визуализатора
    start_data_server(8888)

    # Создание и запуск бота
    bot = BombermanBot(API_URL, AUTH_TOKEN)
    bot.run()


if __name__ == '__main__':
    main()
