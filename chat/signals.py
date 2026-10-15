from chat.models import ChatSession


def delete_chat_history(sender, user, **kwargs):
    """accounts.signals.access_expired receiver: a user whose access ran
    out loses their chat history (connected in ChatConfig.ready)."""
    ChatSession.objects.filter(user=user).delete()
