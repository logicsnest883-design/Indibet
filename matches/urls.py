from django.urls import path

from . import views

app_name = "matches"

urlpatterns = [
    path("", views.lobby, name="lobby"),
    path("<int:match_id>/", views.board, name="board"),
]