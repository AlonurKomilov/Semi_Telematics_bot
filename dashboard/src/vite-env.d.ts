/// <reference types="vite/client" />

interface Window {
  L: typeof import('leaflet');
  __onTelegramAuth?: (user: import('./types').TelegramLoginData) => void;
  __TG_BOT_USERNAME?: string;
}
