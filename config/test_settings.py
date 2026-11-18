import os

os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-with-sufficient-length')
os.environ.setdefault('PORTAL_ORACLE_DSN', 'unused')
os.environ.setdefault('PORTAL_ORACLE_USER', 'unused')
os.environ.setdefault('PORTAL_ORACLE_PASSWORD', 'unused')
# The toolbar is dev-only; keep it out of test runs even when .env has DEBUG=True.
os.environ['ENABLE_DEBUG_TOOLBAR'] = 'False'

from .settings import *

DEBUG = True
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
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
}
