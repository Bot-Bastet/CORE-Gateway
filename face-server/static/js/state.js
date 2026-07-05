// === global var declarations ===
let apiToken = localStorage.getItem('bastet_api_token') || window._bastet_token || '';
let currentUser = window._bastet_user || null;
let activeTab = localStorage.getItem('bastetActiveTab') || 'dashboard';
let telemetryInterval = null;
let updateInterval = null;
let accountsCached = {};
let activeFolderName = null;
let facesCached = [];
let appWs = null;
window.activeStreams = { 1: false, 2: false };
window.localViewing = { 1: false, 2: false };
window.userClosedStream = { 1: false, 2: false };
let peerConnections = { 1: null, 2: null };
let streamingState = { 1: "idle", 2: "idle" };  // idle|requesting|connecting|active|error
window.manualJointControlActive = false;

// SLAM / Map variables
window.slamGrid = null;
window.slamPath = [];
window.slamPoints = [];
window.robotPose = {x: 0, y: 0, theta: 0};

// === theme + cookie functions ===
// ─── THEME ──────────────────────────────────────────────────────────────
function getCookie(name) {
    const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return v ? v.pop() : null;
}
function setCookie(name, value, days) {
    const d = new Date();
    d.setTime(d.getTime() + days * 86400000);
    document.cookie = name + '=' + value + ';expires=' + d.toUTCString() + ';path=/;SameSite=Lax';
}
function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const isDark = theme === 'dark';
    document.querySelectorAll('#theme-icon-dark, #theme-icon-dark-m').forEach(el => el.style.display = isDark ? '' : 'none');
    document.querySelectorAll('#theme-icon-light, #theme-icon-light-m').forEach(el => el.style.display = isDark ? 'none' : '');
}
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    setCookie('bastet_theme', next, 365);
    applyTheme(next);
}
(function initTheme() {
    const saved = getCookie('bastet_theme');
    applyTheme(saved || 'dark');
})();
