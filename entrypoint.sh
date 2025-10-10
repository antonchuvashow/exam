#!/bin/bash

# Только для web-контейнера создаём superuser
if [ "$RUN_WEB" = "1" ]; then
    echo "Collect static files"
    python manage.py collectstatic --noinput

    echo "Apply database migrations"
    python manage.py migrate

    echo "Create superuser"
    python manage.py createsuperuser --noinput --username "$DJANGO_SUPERUSER_USERNAME" --email "$DJANGO_SUPERUSER_EMAIL" || true

    echo "Run Django server"
    python manage.py runserver 0.0.0.0:8000
else
    echo "Run Tailwind watcher"
    python manage.py tailwind start
fi
