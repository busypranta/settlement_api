from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken


def revoke_user_refresh_tokens(user):
    for token in OutstandingToken.objects.filter(user=user).iterator():
        BlacklistedToken.objects.get_or_create(token=token)
