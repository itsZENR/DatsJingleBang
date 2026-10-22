"""
HTTP сервер для визуализации данных арены
"""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path


# Данные для визуализатора
shared_data = {'arena': None, 'iteration': 0, 'actions': [], 'round_start_time': None, 'lock': threading.Lock()}


class DataHandler(BaseHTTPRequestHandler):
    logs_dir = Path(__file__).parent / 'arena_logs'

    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == '/arena':
            with shared_data['lock']:
                data = shared_data['arena'] or {}
                data = dict(data)
                data['_iteration'] = shared_data['iteration']
                data['_actions'] = shared_data['actions']
                data['_round_start_time'] = shared_data['round_start_time']
            self.send_json(data)

        elif self.path == '/logs':
            # Список файлов логов
            files = []
            if self.logs_dir.exists():
                for f in sorted(self.logs_dir.glob('*.jsonl'), key=lambda x: x.stat().st_mtime, reverse=True):
                    files.append({
                        'name': f.name,
                        'size': f.stat().st_size,
                        'modified': f.stat().st_mtime
                    })
            self.send_json(files)

        elif self.path.startswith('/logs/'):
            # Содержимое конкретного файла
            filename = self.path[6:]  # Убираем '/logs/'
            # Защита от path traversal
            if '..' in filename or '/' in filename or '\\' in filename:
                self.send_json({'error': 'Invalid filename'}, 400)
                return
            filepath = self.logs_dir / filename
            if filepath.exists() and filepath.suffix == '.jsonl':
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(filepath.read_bytes())
            else:
                self.send_json({'error': 'File not found'}, 404)

        elif self.path.startswith('/round-start/'):
            # Получить время начала раунда из первой записи лога
            round_name = self.path[13:]  # Убираем '/round-start/'
            # Защита от path traversal
            if '..' in round_name or '/' in round_name or '\\' in round_name:
                self.send_json({'error': 'Invalid round name'}, 400)
                return

            filepath = self.logs_dir / f"{round_name}.jsonl"
            if filepath.exists() and filepath.suffix == '.jsonl':
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        first_line = f.readline()
                        if first_line:
                            first_entry = json.loads(first_line)
                            # Поддержка обоих форматов логов
                            if 'timestamp' in first_entry:
                                self.send_json({'start_time': first_entry['timestamp']})
                            else:
                                self.send_json({'error': 'No timestamp in log'}, 404)
                        else:
                            self.send_json({'error': 'Empty log file'}, 404)
                except Exception as e:
                    self.send_json({'error': str(e)}, 500)
            else:
                self.send_json({'error': 'Round not found'}, 404)

        else:
            self.send_json({'error': 'Not found'}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()


def start_data_server(port=8888):
    """Запустить HTTP сервер для визуализатора"""
    server = HTTPServer(('localhost', port), DataHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
