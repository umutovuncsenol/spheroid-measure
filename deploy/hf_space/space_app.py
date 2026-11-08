"""Start Spheroid Review inside a Hugging Face Space that uses the (free) Gradio SDK.

The Space runs this file with Python and expects a web server on port 7860. We do not need Gradio
itself: this replaces the process with gunicorn serving our Flask app (one worker, several threads,
because sessions, the detection job and the model live in that process's memory).
"""
import os

home = os.path.expanduser('~')
data = '/data' if os.path.isdir('/data') and os.access('/data', os.W_OK) else home   # /data = persistent storage, if bought
os.environ.setdefault('SPHEROID_SESSIONS', os.path.join(data, 'sessions'))
os.environ.setdefault('HF_HOME', os.path.join(data, 'hf-cache'))
os.environ.setdefault('SPHEROID_PRELOAD_MODEL', '1')
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.makedirs(os.environ['SPHEROID_SESSIONS'], exist_ok=True)

port = os.environ.get('PORT', '7860')
os.execvp('gunicorn', ['gunicorn', '--workers', '1', '--threads', '8', '--timeout', '900',
                       '--bind', f'0.0.0.0:{port}', 'app.wsgi:app'])
