from django.apps import AppConfig

from core.db_prefix import install_table_prefix

# Run while app configs are loaded (before model modules are imported) so the
# prefix reaches Django and third-party models as well as our own.
install_table_prefix()


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Core'
