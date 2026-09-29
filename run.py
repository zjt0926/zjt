"""CampusClaw application entry point.

Run locally for development:
    python run.py

The canonical production runtime is gunicorn inside Docker Compose
(see docker-compose.yml). The WSGI application is exposed as
``app:create_app()`` so gunicorn can load it with ``app:app`` after
the module-level call below.
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
