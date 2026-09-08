"""
config/celery.py
Celery application bootstrap.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("unifiedcomm")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
