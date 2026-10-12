"""Start the local review app:  python -m app [--port 8765] [--no-browser]"""
import argparse
import threading
import webbrowser

from .server import create_app, SESSIONS_DIR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args()
    url = f'http://127.0.0.1:{a.port}/'
    print(f'Spheroid review app running at {url}  (sessions folder: {SESSIONS_DIR})')
    print('Press Ctrl+C to stop. Everything is saved after every action.')
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    create_app().run(host='127.0.0.1', port=a.port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
