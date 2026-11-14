from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/checkers/<int:match_id>/", consumers.CheckersConsumer.as_asgi()),
]