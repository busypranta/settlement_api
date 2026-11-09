"""Apply ``DB_TABLE_PREFIX`` to every managed model and its migrations.

Django and third-party migrations hard-code their table names, so setting
``db_table`` on our own models is not enough to namespace the whole project in
a shared database. ``Options._prepare`` runs while every model class is being
built, including models rendered from migration files, which makes it a single
place where the prefix can be applied consistently to both runtime queries and
schema operations.
"""

from django.conf import settings
from django.db import connection
from django.db.backends.utils import truncate_name
from django.db.models.options import Options

_original_prepare = Options._prepare


def _prefixed_prepare(self, model):
    _original_prepare(self, model)

    prefix = getattr(settings, 'DB_TABLE_PREFIX', '') or ''
    if prefix and self.managed and '.' not in self.db_table:
        if not self.db_table.startswith(prefix):
            self.db_table = f'{prefix}{self.db_table}'
        # Oracle (and some other backends) truncate identifiers when they are
        # quoted. Truncate now with the same helper so model metadata matches
        # the real table name.
        self.db_table = truncate_name(
            self.db_table, connection.ops.max_name_length()
        )


def install_table_prefix():
    """Install the prefix patch once, before any models are imported."""
    if getattr(Options, '_db_table_prefix_installed', False):
        return
    Options._prepare = _prefixed_prepare
    Options._db_table_prefix_installed = True
