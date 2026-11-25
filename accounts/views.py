from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import SignUpForm
from .models import User


def _referrer_from_session(request):
    code = request.session.get("ref")
    if not code:
        return None
    return User.objects.filter(referral_code=code).first()


def signup(request):
    if request.user.is_authenticated:
        return redirect("core:home")

    code = request.GET.get("ref", "").strip().upper()
    if code:
        request.session["ref"] = code  # remember it while the person fills in the form

    referrer = _referrer_from_session(request)

    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            if referrer:
                user.referred_by = referrer
            user.save()
            request.session.pop("ref", None)
            login(request, user)
            return redirect("core:home")
    else:
        form = SignUpForm()

    return render(request, "accounts/signup.html", {"form": form, "referrer": referrer})


@login_required
def referrals(request):
    code = request.user.get_referral_code()
    link = request.build_absolute_uri(reverse("accounts:signup")) + "?ref=" + code
    people = request.user.referrals.order_by("-date_joined")
    context = {
        "code": code,
        "link": link,
        "people": people,
        "count": people.count(),
    }
    return render(request, "accounts/referrals.html", context)