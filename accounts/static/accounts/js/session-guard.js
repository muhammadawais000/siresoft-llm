// Keeps a signed-in page honest about the account's state:
//  - every fetch() that comes back 401 (signed out, deactivated, or out of
//    access hours) goes to the sign-in page, where the reason is shown,
//    instead of each caller showing its own error;
//  - the account status endpoint is polled, and checked again when a
//    backgrounded tab is shown, so an admin turning an account off logs
//    the user out within seconds without them clicking anything.
// Settings come from the data-* attributes {% accounts_session_guard %}
// renders. Must load before the page's own scripts make any fetch calls.
(function () {
  const script = document.currentScript;
  const loginUrl = script.dataset.loginUrl;
  const statusUrl = script.dataset.statusUrl;
  const pollMs = parseInt(script.dataset.pollSeconds, 10) * 1000;

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const resp = await nativeFetch(...args);
    if (resp.status === 401) {
      window.location.href = loginUrl;
      // Never settles: the page is navigating away, and callers must not
      // go on to show an error in the meantime.
      return new Promise(() => {});
    }
    return resp;
  };

  const check = () => window.fetch(statusUrl).catch(() => {});
  if (pollMs > 0) setInterval(check, pollMs);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') check();
  });
})();
