import getpass
import sys

from django.contrib.auth import get_user_model, password_validation
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError


class Command(BaseCommand):
    help = 'Create a SUPERADMIN without passing its password as a command-line argument'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument('--email', default='')
        parser.add_argument(
            '--password-stdin',
            action='store_true',
            help='Read the password from stdin instead of prompting interactively',
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        username = options['username']
        if user_model.objects.filter(username=username).exists():
            raise CommandError(f'User {username} already exists')

        if options['password_stdin']:
            password = sys.stdin.readline().rstrip('\r\n')
        else:
            password = getpass.getpass('Password: ')
            confirmation = getpass.getpass('Confirm password: ')
            if password != confirmation:
                raise CommandError('Passwords do not match')
        if not password:
            raise CommandError('Password cannot be empty')
        try:
            password_validation.validate_password(password)
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc

        user_model.objects.create_superuser(
            username=username,
            email=options['email'],
            password=password,
        )
        self.stdout.write(self.style.SUCCESS(f'SUPERADMIN {username} created'))
