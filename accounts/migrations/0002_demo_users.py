from django.contrib.auth.hashers import make_password
from django.db import migrations

DEMO_USERS = [
    ("director", "Director2026!", "director"),
    ("storekeeper", "Store2026!", "storekeeper"),
    ("manager", "Manager2026!", "manager"),
]


def create_demo_users(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for username, password, role in DEMO_USERS:
        # Never touch an existing account (e.g. a changed password) on re-run.
        if not User.objects.filter(username=username).exists():
            User.objects.create(username=username, password=make_password(password), role=role)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [migrations.RunPython(create_demo_users, migrations.RunPython.noop)]
