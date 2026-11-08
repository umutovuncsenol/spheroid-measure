"""Entry point for running the app as an online website behind a production server:

    gunicorn --workers 1 --threads 8 --timeout 900 --bind 0.0.0.0:7860 app.wsgi:app

Use exactly ONE worker process: sessions, the detection job and the loaded model live in that
process's memory (threads are fine). Environment variables:

    SPHEROID_SESSIONS        folder for sessions (point it at persistent storage, e.g. /data/sessions)
    SPHEROID_PASSWORD        if set, the site asks for this password
    SPHEROID_PRELOAD_MODEL   1 = load the detection model in the background at start-up, so the first
                             "Detect" does not wait for the ~1 GB download
    HF_TOKEN                 only needed if the model repository on Hugging Face is private
"""
import os
import threading

from .server import create_app

app = create_app()


def _preload():
    try:
        from .segmentation import get_segmenter
        get_segmenter('cellpose').prepare()
    except Exception as e:     # detection will report the same error when it is used
        print(f'[startup] model preload failed: {e}', flush=True)


if os.environ.get('SPHEROID_PRELOAD_MODEL') == '1':
    threading.Thread(target=_preload, daemon=True).start()
