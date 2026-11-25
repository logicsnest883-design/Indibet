from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/me/", consumers.NotifyConsumer.as_asgi()),
    path("ws/tmatch/<int:match_id>/", consumers.TMatchConsumer.as_asgi()),
]