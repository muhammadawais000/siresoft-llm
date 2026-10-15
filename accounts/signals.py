from django.dispatch import Signal

# Sent (sender=the user model, user=<user>) right after a time-limited
# account's access runs out and it is deactivated. The accounts app
# doesn't know what data a project keeps per user; connect a receiver to
# clean it up, e.g. delete the user's chat history.
access_expired = Signal()
