from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/chess/<int:match_id>/", consumers.ChessConsumer.as_asgi()),
]