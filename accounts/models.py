import secrets

from django.contrib.auth.models import AbstractUser
from django.db import models

# No 0/O or 1/I, so codes are easy to read out and type
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


class User(AbstractUser):
    phone = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    referral_code = models.CharField(
        max_length=12, unique=True, null=True, blank=True, editable=False
    )
    referred_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referrals",
    )

    def __str__(self):
        return self.username

    def get_referral_code(self):
        """Give the user a personal referral code the first time it is needed."""
        if not self.referral_code:
            while True:
                code = _new_code()
                if not User.objects.filter(referral_code=code).exists():
                    break
            self.referral_code = code
            self.save(update_fields=["referral_code"])
        return self.referral_code