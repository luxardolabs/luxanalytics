// LuxAnalytics Portal JavaScript

// Alpine.js transition noise suppression
window.addEventListener('unhandledrejection', function(e) {
    if (e.reason && e.reason.isFromCancelledTransition) {
        e.preventDefault();
    }
});

// Handle HTMX errors - redirect on 401
document.body.addEventListener('htmx:responseError', function(event) {
    if (event.detail.xhr.status === 401) {
        window.location.href = '/login';
        return;
    }
    console.error('HTMX Error:', event.detail);
});

// Close modal utility
window.closeModal = function(containerId) {
    containerId = containerId || 'panel-container';
    var container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = '';
    }
};

// Toast system (dispatched from Alpine.js)
window.showToast = function(message, type) {
    type = type || 'info';
    window.dispatchEvent(new CustomEvent('showtoast', {
        detail: { message: message, type: type }
    }));
};

// Pause HTMX polling when tab is hidden
document.addEventListener('visibilitychange', function() {
    if (document.hidden) {
        document.querySelectorAll('[hx-trigger*="every"]').forEach(function(el) {
            el.setAttribute('data-hx-trigger-paused', el.getAttribute('hx-trigger'));
            el.removeAttribute('hx-trigger');
        });
    } else {
        document.querySelectorAll('[data-hx-trigger-paused]').forEach(function(el) {
            el.setAttribute('hx-trigger', el.getAttribute('data-hx-trigger-paused'));
            el.removeAttribute('data-hx-trigger-paused');
            htmx.process(el);
        });
    }
});
