// YT Music Scrobbler - Legal Page Helper

document.addEventListener('DOMContentLoaded', () => {
    const yearEl = document.getElementById('year') || document.getElementById('footer-year');
    if (yearEl) yearEl.textContent = new Date().getFullYear();

    const lastUpdatedEl = document.getElementById('last-updated-year');
    if (lastUpdatedEl) {
        const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
        const now = new Date();
        lastUpdatedEl.textContent = `${months[now.getMonth()]} ${now.getFullYear()}`;
    }
});

function toggleTheme() {
    const isLight = document.body.getAttribute('data-theme') === 'light';
    document.body.setAttribute('data-theme', isLight ? 'dark' : 'light');
    const icon = document.getElementById('theme-icon');
    if (icon) {
        icon.innerHTML = isLight
            ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
            : '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
    }
    localStorage.setItem('theme', isLight ? 'dark' : 'light');
}

if (localStorage.getItem('theme') === 'light') {
    document.body.setAttribute('data-theme', 'light');
    const icon = document.getElementById('theme-icon');
    if (icon) {
        icon.innerHTML = '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
    }
}
