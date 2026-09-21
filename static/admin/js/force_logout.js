function getCookie(name) {
  const match = document.cookie.match('(^|;\\s*)' + name + '=([^;]*)');
  return match ? decodeURIComponent(match[2]) : null;
}

function siresoftForceLogout(userId, username) {
  if (!confirm('Log out and deactivate ' + username + '? They will not be able to log back in until reactivated here.')) {
    return;
  }
  fetch('/admin/auth/user/' + userId + '/force-logout/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCookie('csrftoken') },
  })
    .then((resp) => {
      if (!resp.ok) throw new Error('request failed');
      window.location.reload();
    })
    .catch(() => alert('Could not deactivate this user. Please try again.'));
}
