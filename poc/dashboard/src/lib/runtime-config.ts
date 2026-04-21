const pageProtocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const host = window.location.hostname;

export const BACKEND_PORT = import.meta.env.VITE_BACKEND_PORT || '8001';
export const CV_PORT = import.meta.env.VITE_CV_PORT || '8000';
export const DASHBOARD_PORT = import.meta.env.VITE_DASHBOARD_PORT || '5173';

export const BACKEND_BASE = `${pageProtocol}//${host}:${BACKEND_PORT}`;
export const CV_BASE = `${pageProtocol}//${host}:${CV_PORT}`;
export const BACKEND_WS_BASE = `${wsProtocol}//${host}:${BACKEND_PORT}`;
