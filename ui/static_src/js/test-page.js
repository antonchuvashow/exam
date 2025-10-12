// static/js/test-page.js
(function () {
  'use strict';

  // helper: read cookie
  function getCookie(name) {
    const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return v ? v.pop() : '';
  }

  // запускаем после загрузки DOM
  document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('test-form');
    if (!form) return;

    // данные из data-attributes
    const clientToken = form.dataset.clientToken || '';
    const sessionId = form.dataset.sessionId || '';
    const heartbeatUrl = form.dataset.heartbeatUrl || '';
    const warnUrl = form.dataset.warnUrl || '';
    const remainingAttr = form.dataset.remainingSeconds;
    const remaining = (remainingAttr !== undefined && remainingAttr !== '') ? parseInt(remainingAttr, 10) : null;

    const csrftoken = getCookie('csrftoken');
    const warningEl = document.getElementById('warning-count');
    const timerEl = document.getElementById('timer');

    function postJson(url, payload) {
      if (!url) return Promise.reject(new Error('no url'));
      return fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrftoken
        },
        body: JSON.stringify(payload)
      }).then(r => r.json());
    }

    // --- ORDER lists: универсальная логика для всех .order-list ---
    const lists = Array.from(document.querySelectorAll('.order-list'));
    lists.forEach(list => {
      try {
        const inputId = list.dataset.orderInputId;
        const input = inputId ? document.getElementById(inputId) : null;
        if (!input) return;

        let order = [];

        const items = Array.from(list.querySelectorAll('li'));
        items.forEach((item, index) => {
          item.dataset.relativeIndex = index + 1;
        });

        items.forEach(item => {
          item.addEventListener('click', () => {
            const relIndex = parseInt(item.dataset.relativeIndex, 10);
            if (order.includes(relIndex)) return;

            order.push(relIndex);

            const numberEl = item.querySelector('.order-number');
            if (numberEl) {
              numberEl.textContent = order.length;
              numberEl.classList.remove('hidden');
            }

            input.value = JSON.stringify(order);
          });
        });

        // reset button (создаём после списка)
        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        resetBtn.textContent = 'Сбросить порядок';
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
      } catch (err) {
        console.warn('order-list init failed', err);
      }
    });

    // --- Timer (если есть remaining) ---
    let timerInterval = null;
    let remainingSeconds = Number.isFinite(remaining) ? remaining : null;
    if (remainingSeconds !== null) {
      function updateTimerDisplay() {
        if (remainingSeconds <= 0) {
          clearInterval(timerInterval);
          try { form.submit(); } catch (e) { /* ignore */ }
          return;
        }
        const m = Math.floor(remainingSeconds / 60);
        const s = remainingSeconds % 60;
        if (timerEl) timerEl.textContent = `${m}:${String(s).padStart(2, '0')}`;
        remainingSeconds--;
      }
      updateTimerDisplay();
      timerInterval = setInterval(updateTimerDisplay, 1000);
    }

    // --- Heartbeat / Warn ---
    let heartbeatTimer = null;
    if (heartbeatUrl) {
      heartbeatTimer = setInterval(() => {
        postJson(heartbeatUrl, { token: clientToken }).catch(() => {
          console.warn('heartbeat failed');
        });
      }, 10000);
    }

    function sendWarn(action) {
      if (!warnUrl) return;
      postJson(warnUrl, { token: clientToken, action: action })
        .then(data => {
          if (warningEl && data) {
            warningEl.textContent = `⚠️ Нарушения: ${data.tab_switches} из ${data.max_warnings ?? 'N/A'}, вне страницы: ${data.time_outside_seconds ?? 0} сек`;
          }
          if (data && data.submit) {
            setTimeout(() => { try { form.submit(); } catch (e) {} }, 300);
          }
        })
        .catch(err => console.warn('sendWarn failed', err));
    }

    // blur/focus
    window.addEventListener('blur', () => sendWarn('blur'));
    window.addEventListener('focus', () => sendWarn('focus'));

    // beforeunload: показываем стандартное предупреждение и пытаемся отправить данные
    window.addEventListener('beforeunload', function (e) {
      e.preventDefault();

      try {
        const fd = new FormData(form);
        if (navigator.sendBeacon) {
          navigator.sendBeacon(form.action, fd);
        } else {
          form.submit();
        }
      } catch (err) {
        try { form.submit(); } catch (ignored) {}
      }
    });

    // cleanup при unload
    window.addEventListener('unload', function () {
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      if (timerInterval) clearInterval(timerInterval);
    });
  });
})();
