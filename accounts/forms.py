from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q

from .models import User


def normalize_phone(raw):
    """Turn what people type into one standard form, like 0977 123 456 -> 260977123456.
    Returns None if it does not look like a phone number."""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "260" + digits[1:]
    if not 11 <= len(digits) <= 15:
        return None
    return digits


class SignUpForm(UserCreationForm):
    phone = forms.CharField(
        max_length=20,
        help_text="Your Mobile Money number, e.g. 0977123456",
    )
    date_of_birth = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "phone", "date_of_birth")

    def clean_phone(self):
        raw = self.cleaned_data["phone"]
        phone = normalize_phone(raw)
        if not phone:
            raise forms.ValidationError("Enter a valid phone number.")
        if User.objects.filter(Q(phone=phone) | Q(phone=raw.strip())).exists():
            raise forms.ValidationError("This phone number is already registered.")
        return phone

    def clean_date_of_birth(self):
        dob = self.cleaned_data["date_of_birth"]
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 18:
            raise forms.ValidationError("You must be 18 or older to join IndiBet.")
        return dob