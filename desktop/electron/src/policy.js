'use strict';
// Deny-by-default network policy (pure functions; used by main.js session hooks).
const { URL } = require('node:url');

function parse(u) {
  try { return new URL(u); } catch { return null; }
}

// Strict check: exact protocol, exact hostname 127.0.0.1, exact port, no credentials.
function isAllowedUrl(rawUrl, port) {
  if (typeof rawUrl !== 'string') return false;
  const u = parse(rawUrl);
  if (!u) return false;
  const p = String(port);
  if (u.protocol === 'data:' || u.protocol === 'blob:' ) {
    if (u.protocol === 'blob:') {
      // blob:http://127.0.0.1:P/uuid — origin must be the app origin
      const inner = parse(rawUrl.slice(5));
      return !!inner && inner.protocol === 'http:' && inner.hostname === '127.0.0.1' && inner.port === p && !inner.username && !inner.password;
    }
    return true;
  }
  if (u.protocol !== 'http:' && u.protocol !== 'ws:') return false;
  if (u.username || u.password) return false;
  return u.hostname === '127.0.0.1' && u.port === p;
}

function isAppOrigin(rawUrl, port) {
  const u = parse(rawUrl);
  return !!u && u.protocol === 'http:' && u.hostname === '127.0.0.1' && u.port === String(port) && !u.username && !u.password;
}

// Navigation (will-navigate etc.) must stay on the app origin; data: is allowed only for the splash.
function isAllowedNavigation(rawUrl, port) {
  return isAppOrigin(rawUrl, port);
}

module.exports = { isAllowedUrl, isAppOrigin, isAllowedNavigation };
