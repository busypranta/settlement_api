from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_user_allow_multiple_merchants'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='allow_settlement_transactions',
            field=models.BooleanField(default=False),
        ),
    ]
