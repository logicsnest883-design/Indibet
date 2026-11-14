from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


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

    def clean_date_of_birth(self):
        dob = self.cleaned_data["date_of_birth"]
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 18:
            raise forms.ValidationError("You must be 18 or older to join IndiBet.")
        return dob