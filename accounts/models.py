from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    DIRECTOR = "director", _("Руководитель")
    STOREKEEPER = "storekeeper", _("Кладовщик")
    MANAGER = "manager", _("Менеджер")


class User(AbstractUser):
    # Blank role = no permissions at all (e.g. a createsuperuser account).
    role = models.CharField(max_length=20, choices=Role.choices, blank=True, default="")
