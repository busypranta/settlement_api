import os

os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-with-sufficient-length')
os.environ.setdefault('PORTAL_ORACLE_DSN', 'unused')
os.environ.setdefault('PORTAL_ORACLE_USER', 'unused')
os.environ.setdefault('PORTAL_ORACLE_PASSWORD', 'unused')
os.environ.setdefault('SOURCE_ORACLE_DSN', 'unused')
os.environ.setdefault('SOURCE_ORACLE_USER', 'unused')
os.environ.setdefault('SOURCE_ORACLE_PASSWORD', 'unused')

from .settings import *

DEBUG = True
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
MIDDLEWARE = [
    middleware for middleware in MIDDLEWARE
    if middleware != 'whitenoise.middleware.WhiteNoiseMiddleware'
]
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
    'source': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}
