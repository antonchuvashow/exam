#!/bin/bash

# Collect static files
echo "Collect static files"
python manage.py collectstatic --noinput

python manage.py migrate
python manage.py createsuperuser --noinput --username admin --email admin@example.com

python manage.py runserver 0.0.0.0:8000