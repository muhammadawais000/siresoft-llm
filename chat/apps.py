from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'chat'

    def ready(self):
        from accounts.signals import access_expired

        from chat.signals import delete_chat_history

        access_expired.connect(delete_chat_history, dispatch_uid="chat_delete_history_on_access_expired")
