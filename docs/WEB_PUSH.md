# Browser Web Push setup

The backend sends standard VAPID Web Push. A frontend must register a service worker and ask for notification permission **in response to a user action**. HTTPS is required in deployment; browsers treat localhost as a development exception.

## Generate server keys

With the project's Python environment active, run this once from the repository root:

```sh
python - <<'PY'
from base64 import urlsafe_b64encode
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

key = ec.generate_private_key(ec.SECP256R1())
path = Path('vapid-private.pem')
if path.exists():
    raise SystemExit('vapid-private.pem already exists; left unchanged')
path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
path.chmod(0o600)
public = key.public_key().public_bytes(serialization.Encoding.X962,
    serialization.PublicFormat.UncompressedPoint)
print('Public key:', urlsafe_b64encode(public).decode().rstrip('='))
PY
```

Set `VAPID_PRIVATE_KEY` to the absolute path of the PEM file **as seen by the worker**, `VAPID_PUBLIC_KEY` to the printed string, and `VAPID_SUBJECT` to a real contact such as `mailto:you@yourdomain.com`. Restart API/worker. For Compose, mount the PEM read-only into the worker and use its container path in the environment; the Docker build does not copy a root-level private key. Keep that file and `.env` out of Git and downloadable artifacts.

## Frontend integration

Serve this file from the frontend's root as `/service-worker.js`:

```js
self.addEventListener('push', event => {
  const data = event.data?.json() ?? {};
  event.waitUntil(self.registration.showNotification(
    `${data.demo ? '[DEMO] ' : ''}${data.title || 'Price alert'}`,
    {
      body: `${data.product}: ${data.currency} ${data.price} on ${data.vendor}`,
      // Navigate to your own product detail page, not an arbitrary push URL.
      data: { productId: data.productId },
      tag: `price-${data.productId}`
    }
  ));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const id = encodeURIComponent(event.notification.data.productId || '');
  event.waitUntil(clients.openWindow(`/products/${id}`));
});
```

In an explicit **Enable price notifications** button handler after login:

```js
async function enablePriceNotifications(apiBase, token) {
  const headers = { Authorization: `Bearer ${token}` };
  if (await Notification.requestPermission() !== 'granted') return;

  const registration = await navigator.serviceWorker.register('/service-worker.js');
  await navigator.serviceWorker.ready;
  const keyResponse = await fetch(`${apiBase}/push/public-key`, { headers });
  if (!keyResponse.ok) throw new Error('Push is not configured on the server');
  const { publicKey } = await keyResponse.json();
  const padded = publicKey.replace(/-/g, '+').replace(/_/g, '/')
    + '='.repeat((4 - publicKey.length % 4) % 4);
  const keyBytes = Uint8Array.from(atob(padded), c => c.charCodeAt(0));
  const subscription = await registration.pushManager.getSubscription()
    || await registration.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: keyBytes
    });
  const response = await fetch(`${apiBase}/push/subscriptions`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint: subscription.endpoint, keys: subscription.toJSON().keys })
  });
  if (!response.ok) throw new Error('Could not register push subscription');
  return (await response.json()).id; // Retain this ID to unregister later.
}
```

Create a price alert with `channel: "push"` after registration. To disable notifications, delete `/push/subscriptions/{id}` with that user's token, then call the browser subscription's `unsubscribe()` method.

The server currently allows HTTPS endpoints on `fcm.googleapis.com`, `updates.push.services.mozilla.com`, and `web.push.apple.com`. This deliberately limits server outbound targets. If your browser returns a different provider, review that provider before adding it to `PUSH_HOSTS` in `backend/app/schemas.py`. Some browsers, including certain mobile configurations, require an installed web app for push; confirm support in the frontend.

Expired provider subscriptions (HTTP 404/410) are removed. Reopen the frontend to register again. Unit tests cover transport invocation/expiry handling; actual browser delivery requires your keys, browser, and service worker.
