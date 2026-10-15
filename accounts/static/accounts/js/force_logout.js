// Row button in the user admin list (see AccountsUserAdmin.force_logout_button).
// The relative URL resolves against the user list page, so it works
// wherever the admin is mounted.
function accountsGetCookie(name) {
  const match = document.cookie.match('(^|;\\s*)' + name + '=([^;]*)');
  return match ? decodeURIComponent(match[2]) : null;
}

function accountsForceLogout(relativeUrl, username) {
  if (!confirm('Log out and deactivate ' + username + '? They will not be able to log back in until reactivated here.')) {
    return;
  }
  fetch(relativeUrl, {
    method: 'POST',
    headers: { 'X-CSRFToken': accountsGetCookie('csrftoken') },
  })
    .then((resp) => {
      if (!resp.ok) throw new Error('request failed');
      window.location.reload();
    })
    .catch(() => alert('Could not deactivate this user. Please try again.'));
}
