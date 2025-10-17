(function () {
    'use strict';

    function getCookie(name) {
        const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return v ? v.pop() : '';
    }

    function throttle(fn, ms) {
        let last = 0;
        let timer = null;
        return function (...args) {
            const now = Date.now();
            const rem = ms - (now - last);
            if (rem <= 0) {
                if (timer) {
                    clearTimeout(timer);
                    timer = null;
                }
                last = now;
                fn.apply(this, args);
            } else if (!timer) {
                timer = setTimeout(() => {
                    last = Date.now();
                    timer = null;
                    fn.apply(this, args);
                }, rem);
            }
        };
    }

    document.addEventListener('DOMContentLoaded', function () {
        try {
            const form = document.getElementById('test-form');
            if (!form) return;

            const clientToken = form.dataset.clientToken || '';
            const sessionId = form.dataset.sessionId || '';
            const heartbeatUrl = form.dataset.heartbeatUrl || '';
            const warnUrl = form.dataset.warnUrl || '';
            const remainingAttr = form.dataset.remainingSeconds;
            const remaining = (remainingAttr !== undefined && remainingAttr !== '') ? parseInt(remainingAttr, 10) : null;

            const csrftoken = getCookie('csrftoken');
            const warningEl = document.getElementById('warning-count');
            const timerEl = document.getElementById('timer');
            const submitBtn = document.getElementById('submit-button');

            function postJson(url, payload) {
                if (!url) return Promise.reject(new Error('no url'));
                try {
                    return fetch(url, {
                        method: "POST",
                        credentials: "same-origin",
                        headers: {
                            "Content-Type": "application/json",
                            "X-CSRFToken": csrftoken
                        },
                        body: JSON.stringify(payload),
                        keepalive: true
                    }).then(resp => resp.json().catch(() => ({ ok: resp.ok })));
                } catch (e) {
                    return Promise.reject(e);
                }
            }

            // --- Order lists (unchanged logic) ---
            (function initOrderLists() {
                const lists = Array.from(document.querySelectorAll('.order-list'));
                lists.forEach(list => {
                    try {
                        const inputId = list.dataset.orderInputId;
                        const input = inputId ? document.getElementById(inputId) : null;
                        if (!input) return;

                        let order = [];
                        const items = Array.from(list.querySelectorAll('li'));
                        items.forEach((item, index) => { item.dataset.relativeIndex = index + 1; });

                        items.forEach(item => {
                            item.addEventListener('click', () => {
                                const relIndex = parseInt(item.dataset.relativeIndex, 10);
                                if (isNaN(relIndex)) return;
                                if (order.includes(relIndex)) return;
                                order.push(relIndex);
                                const numberEl = item.querySelector('.order-number');
                                if (numberEl) {
                                    numberEl.textContent = order.length;
                                    numberEl.classList.remove('hidden');
                                }
                                try { input.value = JSON.stringify(order); } catch (e) { input.value = ''; }
                            });
                        });

                        if (!list.nextElementSibling || list.nextElementSibling.dataset?.resetBtn !== "true") {
                            const resetBtn = document.createElement('button');
                            resetBtn.type = 'button';
                            resetBtn.textContent = 'Сбросить порядок';
                            resetBtn.dataset.resetBtn = "true";
                            resetBtn.className = 'mt-2 px-3 py-1 bg-gray-200 rounded hover:bg-gray-300 text-sm';
                            resetBtn.addEventListener('click', () => {
                                order = [];
                                input.value = '';
                                list.querySelectorAll('.order-number').forEach(span => {
                                    span.classList.add('hidden');
                                    span.textContent = '';
                                });
                            });
                            list.after(resetBtn);
                        }
                    } catch (err) {
                        console.warn('order-list init failed', err);
                    }
                });
            })();

            // --- Timer ---
            let timerInterval = null;
            let remainingSeconds = Number.isFinite(remaining) ? remaining : null;
            if (remainingSeconds !== null) {
                function updateTimerDisplay() {
                    try {
                        if (remainingSeconds <= 0) {
                            clearInterval(timerInterval);
                            timerInterval = null;
                            triggerSubmit('timeout');
                            return;
                        }
                        const m = Math.floor(remainingSeconds / 60);
                        const s = remainingSeconds % 60;
                        if (timerEl) timerEl.textContent = `${m}:${String(s).padStart(2, '0')}`;
                        remainingSeconds--;
                    } catch (e) { console.warn('timer error', e); }
                }
                updateTimerDisplay();
                timerInterval = setInterval(updateTimerDisplay, 1000);
            }

            // --- Heartbeat ---
            let heartbeatTimer = null;
            const HEARTBEAT_INTERVAL = 10000;
            if (heartbeatUrl) {
                postJson(heartbeatUrl, { token: clientToken }).catch(() => { });
                heartbeatTimer = setInterval(() => {
                    postJson(heartbeatUrl, { token: clientToken }).catch(() => {
                        console.warn('heartbeat failed');
                    });
                }, HEARTBEAT_INTERVAL + 0.2 * Math.random() * HEARTBEAT_INTERVAL);
            }

            // --- Warn handling with dedupe ---
            let isSubmitting = false;
            let lastWarnTs = 0;
            const WARN_DEDUP_MS = 1100; // won't send warn more often than this (also used with throttle)
            const sendWarnThrottled = throttle(function (action) {
                if (!warnUrl) return;
                // avoid sending if user already submitting
                if (isSubmitting) return;
                postJson(warnUrl, { token: clientToken, action: action })
                    .then(data => {
                        if (warningEl && data) {
                            try {
                                const tab = Number(data.tab_switches || 0);
                                const maxw = data.max_warnings ?? 'N/A';
                                const outside = Number(data.time_outside_seconds || 0);
                                warningEl.textContent = `⚠️ Нарушения: ${tab} из ${maxw}, вне страницы: ${outside} сек`;
                            } catch (e) { /* ignore UI render errors */ }
                        }
                        if (data && data.submit) {
                            setTimeout(() => triggerSubmit('warn-submit'), 300);
                        }
                    }).catch(err => console.warn('sendWarn failed', err));
            }, 1500);

            function sendWarn(action) {
                try {
                    const now = Date.now();
                    if (now - lastWarnTs < WARN_DEDUP_MS) return; // dedupe close events
                    lastWarnTs = now;
                    sendWarnThrottled(action);
                } catch (e) { console.warn('sendWarn wrapper', e); }
            }

            // prefer visibilitychange, but keep blur/focus as fallback (deduped above)
            document.addEventListener('visibilitychange', function () {
                if (document.hidden) sendWarn('blur'); else sendWarn('focus');
            });
            window.addEventListener('blur', () => sendWarn('blur'));
            window.addEventListener('focus', () => sendWarn('focus'));

            // --- Submitting: markSubmitting WITHOUT disabling all inputs ---
            function markSubmitting() {
                if (isSubmitting) return;
                isSubmitting = true;
                // disable submit button after a microtask so browser collects fields
                setTimeout(() => {
                    try {
                        if (submitBtn) {
                            submitBtn.setAttribute('disabled', 'disabled');
                            submitBtn.classList.add('opacity-60', 'cursor-not-allowed');
                        }
                    } catch (e) { /* noop */ }
                }, 0);
            }

            // unified submit trigger
            function triggerSubmit(reason) {
                if (isSubmitting) return;
                // mark first (prevents warn-triggered submits)
                markSubmitting();
                try {
                    if (typeof form.requestSubmit === 'function') {
                        form.requestSubmit();
                    } else {
                        form.submit();
                    }
                } catch (err) {
                    console.warn('form submit failed', err);
                    // fallback: try sendBeacon with minimal info
                    try {
                        if (navigator.sendBeacon && form.action) {
                            const minimal = new FormData();
                            minimal.append('client_token', clientToken);
                            minimal.append('session_id', sessionId);
                            navigator.sendBeacon(form.action, minimal);
                        }
                    } catch (e) { console.warn('sendBeacon fallback failed', e); }
                }
            }

            // ensure submit mark before actual submit event completes
            form.addEventListener('submit', function (e) {
                if (isSubmitting) {
                    // allow browser to continue (prevent double handling)
                    return;
                }
                // mark but DON'T disable all inputs (we disable submit button in setTimeout)
                markSubmitting();
            }, { capture: true });

            // protect submit buttons click as well
            Array.from(form.querySelectorAll('button[type="submit"], input[type="submit"]')).forEach(btn => {
                btn.addEventListener('click', function () {
                    if (isSubmitting) return;
                    // mark quickly to avoid beforeunload prompt
                    markSubmitting();
                }, { passive: true });
            });

            // --- beforeunload: предупреждение только если не отправляем ---
            window.addEventListener('beforeunload', function (e) {
                try {
                    if (!isSubmitting) {
                        // современные браузеры игнорируют текст, но returnValue должен быть установлен
                        e.preventDefault();
                        e.returnValue = '';
                        return '';
                    }
                } catch (err) {
                    // ignore
                }
            }, { capture: true });

            // unload/pagehide
            function doUnloadSend() {
                try {
                    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
                    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
                    if (!isSubmitting && navigator.sendBeacon && form.action) {
                        try {
                            const minimal = new FormData();
                            minimal.append('client_token', clientToken);
                            minimal.append('session_id', sessionId);
                            navigator.sendBeacon(form.action, minimal);
                        } catch (err) { /* swallow */ }
                    }
                } catch (err) { /* swallow */ }
            }
            window.addEventListener('pagehide', doUnloadSend);
            window.addEventListener('unload', doUnloadSend);

            // global error handlers: don't break page
            window.addEventListener('error', function (ev) { try { console.warn('Captured error:', ev && ev.message); } catch (e) { } });
            window.addEventListener('unhandledrejection', function (ev) { try { console.warn('Unhandled promise rejection:', ev && ev.reason); } catch (e) { } });
        } catch (err) {
            console.warn('test-page init failed', err);
        }
    });
})();
