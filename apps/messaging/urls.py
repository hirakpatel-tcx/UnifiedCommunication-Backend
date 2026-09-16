from django.urls import path

from apps.messaging.views import (
    ConversationListView,
    ConversationMarkReadView,
    ConversationMessagesView,
    MessageMediaView,
    SendMessageView,
)

urlpatterns = [
    path("conversations/", ConversationListView.as_view(), name="messaging-conversation-list"),
    path(
        "conversations/<uuid:conversation_id>/messages/",
        ConversationMessagesView.as_view(),
        name="messaging-conversation-messages",
    ),
    path(
        "conversations/<uuid:conversation_id>/read/",
        ConversationMarkReadView.as_view(),
        name="messaging-conversation-read",
    ),
    path("messages/send/", SendMessageView.as_view(), name="messaging-send"),
    path("media/<uuid:media_id>/", MessageMediaView.as_view(), name="messaging-media"),
]
