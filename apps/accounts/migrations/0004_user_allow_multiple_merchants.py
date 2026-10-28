from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_alter_user_mobile_alter_user_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='allow_multiple_merchants',
            field=models.BooleanField(default=False),
        ),
    ]
