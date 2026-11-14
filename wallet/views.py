from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Wallet


@login_required
def wallet_page(request):
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    transactions = wallet.transactions.all()[:20]
    context = {
        "wallet": wallet,
        "total": wallet.available + wallet.held,
        "transactions": transactions,
    }
    return render(request, "wallet/wallet.html", context)