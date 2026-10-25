import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


_BD_MOBILE_PATTERN = re.compile(r'(?:\+8801[3-9]\d{8}|01[3-9]\d{8})\Z')


def normalize_bd_mobile(value):
    mobile = re.sub(r'[\s-]', '', value or '')
    if not _BD_MOBILE_PATTERN.fullmatch(mobile):
        raise ValidationError(
            _('Enter a valid Bangladesh mobile number, such as 01XXXXXXXXX or +8801XXXXXXXXX.'),
            code='invalid_mobile',
        )
    if mobile.startswith('01'):
        return '+880' + mobile[1:]
    return mobile


def validate_bd_mobile(value):
    normalize_bd_mobile(value)


class MaximumLengthValidator:
    def __init__(self, max_length=16):
        self.max_length = max_length

    def validate(self, password, user=None):
        if len(password) > self.max_length:
            raise ValidationError(
                _('This password is too long. It must contain no more than %(max_length)s characters.'),
                code='password_too_long',
                params={'max_length': self.max_length},
            )

    def get_help_text(self):
        return _('Your password must contain no more than %(max_length)s characters.') % {
            'max_length': self.max_length,
        }
