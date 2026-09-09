from django.urls import path

from apps.messaging.views import ConversationListView, ConversationMessagesView, SendMessageView

urlpatterns = [
    path("conversations/", ConversationListView.as_view(), name="messaging-conversation-list"),
    path(
        "conversations/<uuid:conversation_id>/messages/",
        ConversationMessagesView.as_view(),
        name="messaging-conversation-messages",
    ),
    path("messages/send/", SendMessageView.as_view(), name="messaging-send"),
]
