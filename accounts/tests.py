from django.test import TestCase
from django.urls import reverse

from .forms import normalize_phone
from .models import User

PASSWORD = "Zq7!kLm29xP"


def signup_data(username, phone, dob="1990-01-01"):
    return {
        "username": username,
        "phone": phone,
        "date_of_birth": dob,
        "password1": PASSWORD,
        "password2": PASSWORD,
    }


class PhoneNumbers(TestCase):
    def test_different_spellings_become_one_number(self):
        for raw in ("0977123456", "0977 123 456", "+260977123456", "260977123456", "00260-977-123-456"):
            self.assertEqual(normalize_phone(raw), "260977123456")

    def test_rubbish_is_rejected(self):
        for raw in ("", "abc", "12345"):
            self.assertIsNone(normalize_phone(raw))


class Referrals(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password=PASSWORD, phone="260971111111")
        self.code = self.alice.get_referral_code()

    def test_code_is_stable_and_unique(self):
        self.assertEqual(len(self.code), 8)
        self.assertEqual(self.alice.get_referral_code(), self.code)
        bob = User.objects.create_user("bob", password=PASSWORD)
        self.assertNotEqual(bob.get_referral_code(), self.code)

    def test_signup_through_a_link_counts_as_a_referral(self):
        self.client.get(reverse("accounts:signup") + f"?ref={self.code}")
        response = self.client.post(reverse("accounts:signup"), signup_data("carol", "0972222222"))
        self.assertEqual(response.status_code, 302)
        carol = User.objects.get(username="carol")
        self.assertEqual(carol.referred_by, self.alice)
        self.assertEqual(carol.phone, "260972222222")
        self.assertEqual(self.alice.referrals.count(), 1)

    def test_signup_without_a_link_has_no_referrer(self):
        self.client.post(reverse("accounts:signup"), signup_data("dave", "0973333333"))
        self.assertIsNone(User.objects.get(username="dave").referred_by)

    def test_unknown_code_is_ignored(self):
        self.client.get(reverse("accounts:signup") + "?ref=NOSUCHCD")
        self.client.post(reverse("accounts:signup"), signup_data("erin", "0974444444"))
        self.assertIsNone(User.objects.get(username="erin").referred_by)

    def test_same_phone_cannot_sign_up_twice(self):
        self.client.get(reverse("accounts:signup") + f"?ref={self.code}")
        self.client.post(reverse("accounts:signup"), signup_data("carol", "0972222222"))
        self.client.logout()
        self.client.get(reverse("accounts:signup") + f"?ref={self.code}")
        response = self.client.post(reverse("accounts:signup"), signup_data("carol2", "+260 972 222 222"))
        self.assertEqual(response.status_code, 200)  # the form is shown again with an error
        self.assertFalse(User.objects.filter(username="carol2").exists())
        self.assertEqual(self.alice.referrals.count(), 1)