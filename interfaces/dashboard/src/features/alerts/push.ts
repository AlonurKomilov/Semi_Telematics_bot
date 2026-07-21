/**
 * Web-push client helpers — enable/disable push on THIS device.
 *
 * Push consent is per BROWSER: enabling registers the service worker,
 * asks the browser's permission, subscribes with the server's stable
 * VAPID public key, and posts the subscription to the backend.  The
 * backend keeps one row per device, so a user can have push on the
 * laptop and off the phone.
 *
 * Permission is only ever requested from the explicit "Enable on this
 * device" click — never on page load (browsers punish unsolicited
 * prompts and users reflex-deny them).
 */
import { apiJSON } from '../../api/client';

const SW_PATH = '/push-sw.js';

export function isPushSupported(): boolean {
  return typeof navigator !== 'undefined'
    && 'serviceWorker' in navigator
    && typeof window !== 'undefined'
    && 'PushManager' in window
    && 'Notification' in window;
}

/** The browser needs the VAPID key as a raw Uint8Array.  Explicitly
 * ArrayBuffer-backed so it satisfies the DOM ``BufferSource`` type
 * (TS 5.7 distinguishes ``Uint8Array<ArrayBufferLike>``). */
export function urlBase64ToUint8Array(base64url: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64url.length % 4)) % 4);
  const base64 = (base64url + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  const reg = await navigator.serviceWorker.register(SW_PATH);
  await navigator.serviceWorker.ready;
  return reg;
}

/** This browser's current subscription endpoint (null when not enabled). */
export async function thisDeviceEndpoint(): Promise<string | null> {
  if (!isPushSupported()) return null;
  try {
    const reg = await navigator.serviceWorker.getRegistration(SW_PATH);
    const sub = reg && (await reg.pushManager.getSubscription());
    return sub?.endpoint ?? null;
  } catch {
    return null;
  }
}

/** Full enable flow. Throws with a user-presentable message on failure. */
export async function enablePush(): Promise<void> {
  if (!isPushSupported()) {
    throw new Error('This browser doesn’t support push notifications');
  }
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') {
    throw new Error('Notifications are blocked for this site — allow them in your browser settings');
  }
  const { public_key } = await apiJSON<{ public_key: string }>(
    '/notifications/push/vapid-key');
  const reg = await registration();
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(public_key),
  });
  const json = sub.toJSON();
  const res = await apiJSON<{ ok: boolean; error?: string }>(
    '/notifications/push/subscribe', {
      method: 'POST',
      body: {
        endpoint: sub.endpoint,
        keys: { p256dh: json.keys?.p256dh ?? '', auth: json.keys?.auth ?? '' },
      },
    });
  if (!res.ok) throw new Error('Could not save this device');
}

/** Disable on THIS device: browser unsubscribe + backend row removal. */
export async function disablePush(): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration(SW_PATH);
  const sub = reg && (await reg.pushManager.getSubscription());
  if (!sub) return;
  const endpoint = sub.endpoint;
  await sub.unsubscribe();
  await apiJSON('/notifications/push/unsubscribe', {
    method: 'POST', body: { endpoint },
  });
}
