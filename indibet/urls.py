from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("wallet/", include("wallet.urls")),
    path("matches/", include("matches.urls")),
    path("tournaments/", include("tournaments.urls")),
    path("", include("core.urls")),
]