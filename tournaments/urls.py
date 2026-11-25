from django.urls import path

from . import views

app_name = "tournaments"

urlpatterns = [
    path("", views.tournament_list, name="list"),
    path("my-match/", views.my_match, name="my_match"),
    path("match/<int:match_id>/", views.match_room, name="match_room"),
    path("<int:pk>/", views.tournament_detail, name="detail"),
    path("<int:pk>/bracket/", views.bracket_page, name="bracket"),
    path("<int:pk>/bracket.json", views.bracket_data, name="bracket_data"),
    path("<int:pk>/enrol/", views.enrol_view, name="enrol"),
    path("<int:pk>/withdraw/", views.withdraw_view, name="withdraw"),
]