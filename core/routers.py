from django.core.exceptions import PermissionDenied


class SourceReadOnlyRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == 'source':
            return 'source'
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == 'source':
            raise PermissionDenied('Writes to source models are not allowed')
        return None

    def allow_relation(self, obj1, obj2, **hints):
        source_labels = {obj1._meta.app_label, obj2._meta.app_label}
        if 'source' in source_labels:
            return source_labels == {'source'}
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == 'source':
            return False
        if db == 'source':
            return False
        return db == 'default'
