/* Daily AI News — 캘린더 동작
   window.__SITE__ = { current, latest, assetPrefix, calendarUrl } 사용 */
(function () {
  const SITE = window.__SITE__ || {};
  const navC = document.getElementById('navC');
  const pillC = document.getElementById('datePillC');
  const pop = document.getElementById('calendarPop');
  if (!navC || !pillC || !pop) return;

  // 드롭다운 열고 닫기
  pillC.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = !navC.classList.contains('open');
    navC.classList.toggle('open', willOpen);
    pillC.classList.toggle('open', willOpen);
    if (willOpen && !pop.dataset.loaded) {
      initCalendar();
    }
  });
  document.addEventListener('click', (e) => {
    if (!navC.contains(e.target)) {
      navC.classList.remove('open');
      pillC.classList.remove('open');
    }
  });

  // 키보드 이동: ← →
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'ArrowLeft') {
      const prev = document.querySelector('.nav-c .arrow[aria-label="이전 날짜"]');
      if (prev && prev.tagName === 'A') prev.click();
    } else if (e.key === 'ArrowRight') {
      const next = document.querySelector('.nav-c .arrow[aria-label="다음 날짜"]');
      if (next && next.tagName === 'A') next.click();
    }
  });

  // ---- 캘린더 렌더 ----
  // 데이터는 페이지에 인라인으로 들어와 있다 (window.__SITE__.calendar)
  const calendarData = SITE.calendar || { days: {}, latest: null, earliest: null };
  let viewYear, viewMonth;  // month: 1-12

  function initCalendar() {
    const [yy, mm] = SITE.current.split('-').map(Number);
    viewYear = yy; viewMonth = mm;
    renderCalendar();
    pop.dataset.loaded = '1';
  }

  function renderCalendar() {
    const days = calendarData.days || {};
    const firstDay = new Date(viewYear, viewMonth - 1, 1);
    const lastDay = new Date(viewYear, viewMonth, 0);
    const dim = lastDay.getDate();
    const startDow = firstDay.getDay();  // 0=Sun

    // 이전 달 보기 채우기
    const prevLastDay = new Date(viewYear, viewMonth - 1, 0).getDate();

    let html = '<div class="cal-head">';
    html += '<button id="calPrev" aria-label="이전 달">‹</button>';
    html += `<span class="month-title">${viewYear}년 ${viewMonth}월</span>`;
    html += '<button id="calNext" aria-label="다음 달">›</button>';
    html += '</div><div class="cal-grid">';
    for (const d of ['일','월','화','수','목','금','토']) {
      html += `<span class="dow">${d}</span>`;
    }

    // 이전 달 잔여
    for (let i = startDow - 1; i >= 0; i--) {
      html += `<span class="day muted">${prevLastDay - i}</span>`;
    }
    // 이번 달
    for (let d = 1; d <= dim; d++) {
      const ds = `${viewYear}-${String(viewMonth).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
      const has = days[ds] && days[ds] > 0;
      const isActive = ds === SITE.current;
      const cls = ['day'];
      if (has) cls.push('has-data');
      if (isActive) cls.push('active');
      if (has) {
        html += `<a class="${cls.join(' ')}" href="${SITE.assetPrefix}${ds}/">${d}</a>`;
      } else {
        html += `<span class="${cls.join(' ')}">${d}</span>`;
      }
    }
    // 다음 달 채움 (총 칸 6주 = 42)
    const used = startDow + dim;
    const remaining = (7 - (used % 7)) % 7;
    for (let i = 1; i <= remaining; i++) {
      html += `<span class="day muted">${i}</span>`;
    }
    html += '</div>';
    pop.innerHTML = html;

    document.getElementById('calPrev').addEventListener('click', () => shiftMonth(-1));
    document.getElementById('calNext').addEventListener('click', () => shiftMonth(1));
  }

  function shiftMonth(delta) {
    viewMonth += delta;
    if (viewMonth < 1) { viewMonth = 12; viewYear -= 1; }
    if (viewMonth > 12) { viewMonth = 1; viewYear += 1; }
    renderCalendar();
  }
})();
