/**
 * CHRONOS — ATTENDANCE & ANALYTICS CLIENT CONTROLLER
 * Vanilla JavaScript implementation communicating directly with FastAPI backend
 */

const STATE = {
  employees: [],
  employeesMap: {},
  attendanceLogs: [],
  attendanceTotal: 0,
  attendancePage: 1,
  attendancePageSize: 20,
  activeTab: 'punch',
  selectedEmpCode: '',
  selectedMonth: '2026-07',
  selectedTrendDept: 'Engineering',
  currentRegularizeRecord: null,
};

// --- INITIALIZATION ---
document.addEventListener('DOMContentLoaded', async () => {
  initClock();
  initTabs();
  initEventListeners();
  await checkHealth();
  await loadEmployees();
  await loadAttendanceLogs();
  await loadAnalytics();
});

// --- LIVE IST CLOCK ---
function initClock() {
  const clockEl = document.getElementById('liveIstTime');
  function update() {
    const now = new Date();
    // UTC time + 5.5 hours for IST
    const utcMs = now.getTime() + (now.getTimezoneOffset() * 60000);
    const istDate = new Date(utcMs + (5.5 * 3600000));
    const hours = String(istDate.getHours()).padStart(2, '0');
    const minutes = String(istDate.getMinutes()).padStart(2, '0');
    const seconds = String(istDate.getSeconds()).padStart(2, '0');
    if (clockEl) {
      clockEl.textContent = `${hours}:${minutes}:${seconds} IST`;
    }
  }
  update();
  setInterval(update, 1000);
}

// --- SYSTEM HEALTH CHECK ---
async function checkHealth() {
  try {
    const res = await fetch('/health');
    const data = await res.json();
    const indicator = document.getElementById('healthIndicator');
    const statusText = document.getElementById('healthStatusText');
    if (res.ok && data.status === 'ok') {
      indicator.className = 'health-indicator online';
      statusText.textContent = 'MongoDB Online';
    } else {
      indicator.className = 'health-indicator offline';
      statusText.textContent = 'DB Degraded';
    }
  } catch (err) {
    const indicator = document.getElementById('healthIndicator');
    const statusText = document.getElementById('healthStatusText');
    if (indicator) indicator.className = 'health-indicator offline';
    if (statusText) statusText.textContent = 'DB Disconnected';
  }
}

// --- TAB SWITCHING ---
function initTabs() {
  const navBtns = document.querySelectorAll('.nav-btn');
  navBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetTab = btn.getAttribute('data-tab');
      switchTab(targetTab);
    });
  });
}

function switchTab(tabId) {
  STATE.activeTab = tabId;
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
  });
  document.querySelectorAll('.tab-pane').forEach(pane => {
    pane.classList.toggle('active', pane.id === `pane-${tabId}`);
  });

  if (tabId === 'attendance') {
    loadAttendanceLogs();
  } else if (tabId === 'analytics') {
    loadAnalytics();
  } else if (tabId === 'employees') {
    loadEmployeesTable();
  }
}

// --- EVENT LISTENERS ---
function initEventListeners() {
  // Punch employee selection
  const empSelect = document.getElementById('punchEmpCode');
  empSelect.addEventListener('change', (e) => {
    onSelectEmployee(e.target.value);
  });

  // Timestamp mode toggle
  const modeSelect = document.getElementById('punchModeToggle');
  const customTsGroup = document.getElementById('customTsGroup');
  modeSelect.addEventListener('change', (e) => {
    customTsGroup.classList.toggle('hidden', e.target.value !== 'custom');
  });

  // Punch In & Out buttons
  document.getElementById('btnPunchIn').addEventListener('click', handlePunchIn);
  document.getElementById('btnPunchOut').addEventListener('click', handlePunchOut);
  document.getElementById('refreshFeedBtn').addEventListener('click', () => loadAttendanceLogs(true));

  // Attendance filter & pagination
  document.getElementById('applyFilterBtn').addEventListener('click', () => {
    STATE.attendancePage = 1;
    loadAttendanceLogs();
  });
  document.getElementById('resetFiltersBtn').addEventListener('click', resetFilters);
  document.getElementById('prevPageBtn').addEventListener('click', () => {
    if (STATE.attendancePage > 1) {
      STATE.attendancePage--;
      loadAttendanceLogs();
    }
  });
  document.getElementById('nextPageBtn').addEventListener('click', () => {
    if (STATE.attendancePage * STATE.attendancePageSize < STATE.attendanceTotal) {
      STATE.attendancePage++;
      loadAttendanceLogs();
    }
  });

  // Analytics controls
  document.getElementById('refreshAnalyticsBtn').addEventListener('click', () => {
    STATE.selectedMonth = document.getElementById('analyticsMonth').value || '2026-07';
    loadAnalytics();
  });
  document.getElementById('trendDeptPicker').addEventListener('change', (e) => {
    STATE.selectedTrendDept = e.target.value;
    loadTrendChart();
  });
  document.getElementById('drilldownEmpCode').addEventListener('change', (e) => {
    loadEmployeeMonthlyDrilldown(e.target.value);
  });

  // Modals
  document.getElementById('closeRegularizeModalBtn').addEventListener('click', closeRegularizeModal);
  document.getElementById('cancelRegBtn').addEventListener('click', closeRegularizeModal);
  document.getElementById('regularizeForm').addEventListener('submit', handleRegularizeSubmit);

  document.getElementById('openNewEmpModalBtn').addEventListener('click', openAddEmpModal);
  document.getElementById('closeAddEmpModalBtn').addEventListener('click', closeAddEmpModal);
  document.getElementById('cancelAddEmpBtn').addEventListener('click', closeAddEmpModal);
  document.getElementById('addEmpForm').addEventListener('submit', handleAddEmpSubmit);

  // Status change in regularization modal toggles time inputs
  document.getElementById('regStatus').addEventListener('change', (e) => {
    const isAbsOrLeave = ['ABSENT', 'LEAVE'].includes(e.target.value);
    document.getElementById('regTimeInputsRow').style.opacity = isAbsOrLeave ? '0.3' : '1';
    document.getElementById('regPunchInTime').disabled = isAbsOrLeave;
    document.getElementById('regPunchOutTime').disabled = isAbsOrLeave;
  });

  // Explain Query
  document.getElementById('runExplainBtn').addEventListener('click', runExplainQuery);
  document.getElementById('copyExplainJsonBtn').addEventListener('click', () => {
    const json = document.getElementById('explainJsonPre').textContent;
    navigator.clipboard.writeText(json);
    showToast('Explain JSON copied to clipboard!', 'info');
  });
}

// --- EMPLOYEE MANAGEMENT ---
async function loadEmployees() {
  try {
    const res = await fetch('/employees?page=1&page_size=100');
    if (!res.ok) throw new Error('Failed to load employees');
    const data = await res.json();
    STATE.employees = data.items || [];
    STATE.employeesMap = {};
    STATE.employees.forEach(e => {
      STATE.employeesMap[e.emp_code] = e;
    });

    // Populate dropdowns
    const punchSelect = document.getElementById('punchEmpCode');
    const drilldownSelect = document.getElementById('drilldownEmpCode');

    punchSelect.innerHTML = '<option value="">-- Choose Employee --</option>';
    drilldownSelect.innerHTML = '';

    STATE.employees.forEach(e => {
      const opt = document.createElement('option');
      opt.value = e.emp_code;
      opt.textContent = `${e.emp_code} — ${e.name} (${e.department})`;
      punchSelect.appendChild(opt);

      const opt2 = opt.cloneNode(true);
      drilldownSelect.appendChild(opt2);
    });

    if (STATE.employees.length > 0) {
      STATE.selectedEmpCode = STATE.employees[0].emp_code;
      drilldownSelect.value = STATE.selectedEmpCode;
      loadEmployeeMonthlyDrilldown(STATE.selectedEmpCode);
    }

    document.getElementById('headcountStat').textContent = STATE.employees.length;
  } catch (err) {
    showToast('Error loading employees: ' + err.message, 'error');
  }
}

function onSelectEmployee(empCode) {
  const emp = STATE.employeesMap[empCode];
  const avatar = document.getElementById('empAvatar');
  const name = document.getElementById('empName');
  const meta = document.getElementById('empMeta');
  const shiftTag = document.getElementById('empShiftTag');

  if (!emp) {
    avatar.textContent = '--';
    name.textContent = 'Select an employee';
    meta.textContent = 'Department & Shift will appear here';
    shiftTag.textContent = '--:-- to --:--';
    return;
  }

  const initials = emp.name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
  avatar.textContent = initials;
  name.textContent = emp.name;
  meta.textContent = `${emp.department} • Joined: ${emp.joined_on}`;

  const isOvernight = emp.shift_end <= emp.shift_start;
  shiftTag.textContent = `${emp.shift_start} - ${emp.shift_end} ${isOvernight ? '🌙 (Overnight)' : '☀️'}`;
}

function loadEmployeesTable() {
  const tbody = document.getElementById('employeesTableBody');
  if (!STATE.employees || STATE.employees.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center py-4">No employees registered.</td></tr>';
    return;
  }

  tbody.innerHTML = STATE.employees.map(e => {
    const isOvernight = e.shift_end <= e.shift_start;
    return `
      <tr>
        <td><strong>${e.emp_code}</strong></td>
        <td>${e.name}</td>
        <td><code>${e.email}</code></td>
        <td><span class="badge badge-cyan">${e.department}</span></td>
        <td>${e.shift_start} – ${e.shift_end}</td>
        <td>${isOvernight ? '<span class="badge badge-amber">Overnight</span>' : '<span class="badge badge-emerald">Standard</span>'}</td>
        <td>${e.joined_on}</td>
        <td><code>${e.created_at}</code></td>
      </tr>
    `;
  }).join('');
}

// --- PUNCH ACTIONS ---
async function handlePunchIn() {
  const empCode = document.getElementById('punchEmpCode').value;
  if (!empCode) {
    showToast('Please select an employee first.', 'error');
    return;
  }

  const status = document.getElementById('punchStatus').value;
  const mode = document.getElementById('punchModeToggle').value;
  let punchedAt = null;

  if (mode === 'custom') {
    const customVal = document.getElementById('customDateTimeInput').value;
    if (!customVal) {
      showToast('Please select a custom date & time.', 'error');
      return;
    }
    // Convert local IST input to UTC epoch ms
    punchedAt = new Date(customVal).getTime();
  }

  try {
    const payload = { emp_code: empCode, status };
    if (punchedAt) payload.punched_at = punchedAt;

    const res = await fetch('/attendance/punch-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Punch in failed');
    }

    showToast(`Punch IN Recorded! [${data.emp_code}] Date: ${data.date} (Late: ${data.late_minutes}m)`, 'success');
    await loadAttendanceLogs(true);
    await loadAnalytics();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function handlePunchOut() {
  const empCode = document.getElementById('punchEmpCode').value;
  if (!empCode) {
    showToast('Please select an employee first.', 'error');
    return;
  }

  const mode = document.getElementById('punchModeToggle').value;
  let punchedAt = null;

  if (mode === 'custom') {
    const customVal = document.getElementById('customDateTimeInput').value;
    if (!customVal) {
      showToast('Please select a custom date & time.', 'error');
      return;
    }
    punchedAt = new Date(customVal).getTime();
  }

  try {
    const payload = { emp_code: empCode };
    if (punchedAt) payload.punched_at = punchedAt;

    const res = await fetch('/attendance/punch-out', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Punch out failed');
    }

    showToast(`Punch OUT Confirmed! Work Hours: ${data.work_hours}h | Overtime: ${data.overtime_minutes}m`, 'success');
    await loadAttendanceLogs(true);
    await loadAnalytics();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// --- ATTENDANCE LOGS ---
async function loadAttendanceLogs(refreshOnlyFeed = false) {
  const empCode = document.getElementById('filterEmpCode').value.trim();
  const dateFrom = document.getElementById('filterDateFrom').value;
  const dateTo = document.getElementById('filterDateTo').value;
  const status = document.getElementById('filterStatus').value;

  const params = new URLSearchParams({
    page: STATE.attendancePage,
    page_size: STATE.attendancePageSize
  });

  if (empCode) params.append('emp_code', empCode);
  if (dateFrom) params.append('date_from', dateFrom);
  if (dateTo) params.append('date_to', dateTo);
  if (status) params.append('status', status);

  try {
    const res = await fetch(`/attendance?${params.toString()}`);
    if (!res.ok) throw new Error('Failed to fetch attendance logs');
    const data = await res.json();
    STATE.attendanceLogs = data.items || [];
    STATE.attendanceTotal = data.total || 0;

    renderPunchFeed();
    if (!refreshOnlyFeed) {
      renderAttendanceTable();
    }
  } catch (err) {
    showToast('Error loading attendance logs: ' + err.message, 'error');
  }
}

function renderPunchFeed() {
  const feedList = document.getElementById('punchFeedList');
  if (!feedList) return;

  if (STATE.attendanceLogs.length === 0) {
    feedList.innerHTML = '<div class="text-center py-4 text-muted">No attendance logs found.</div>';
    return;
  }

  // Display top 6 recent items
  const recent = STATE.attendanceLogs.slice(0, 6);
  feedList.innerHTML = recent.map(r => {
    const emp = STATE.employeesMap[r.emp_code] || { name: r.emp_code, department: 'General' };
    const pinStr = r.punch_in ? formatTimeIst(r.punch_in) : '--:--';
    const poutStr = r.punch_out ? formatTimeIst(r.punch_out) : 'In Progress';
    const statusClass = r.status === 'PRESENT' ? 'badge-emerald' : (r.status === 'WFH' ? 'badge-cyan' : 'badge-amber');

    return `
      <div class="punch-feed-item">
        <div class="feed-emp-details">
          <span class="badge ${statusClass}">${r.status}</span>
          <div>
            <div style="font-weight: 600;">${emp.name} <small style="color:var(--text-muted);">(${r.emp_code})</small></div>
            <div style="font-size:0.75rem; color:var(--text-muted);">${r.date}</div>
          </div>
        </div>
        <div class="feed-time-col">
          <div style="color:var(--cyan);">${pinStr} → ${poutStr}</div>
          <div style="font-size:0.72rem; color:var(--text-subtle);">
            ${r.work_hours !== null ? `${r.work_hours} hrs` : 'Open Shift'}
            ${r.late_minutes > 0 ? ` • <span style="color:var(--rose);">${r.late_minutes}m late</span>` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function renderAttendanceTable() {
  const tbody = document.getElementById('attendanceTableBody');
  if (STATE.attendanceLogs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="11" class="text-center py-4 text-muted">No records match the active criteria.</td></tr>';
    return;
  }

  tbody.innerHTML = STATE.attendanceLogs.map((r, idx) => {
    const emp = STATE.employeesMap[r.emp_code] || { name: r.emp_code, department: 'General' };
    const pinStr = r.punch_in ? formatTimeIst(r.punch_in) : '—';
    const poutStr = r.punch_out ? formatTimeIst(r.punch_out) : '<span style="color:var(--cyan);">Open</span>';
    const hasHistory = r.history && r.history.length > 0;
    const statusClass = r.status === 'PRESENT' ? 'badge-emerald' : (r.status === 'WFH' ? 'badge-cyan' : (['ABSENT', 'LEAVE'].includes(r.status) ? 'badge-rose' : 'badge-amber'));

    return `
      <tr>
        <td><strong>${r.date}</strong></td>
        <td>
          <div style="font-weight:600;">${emp.name}</div>
          <small style="color:var(--text-muted);">${r.emp_code} • ${emp.department}</small>
        </td>
        <td><span class="badge ${statusClass}">${r.status}</span></td>
        <td><code>${pinStr}</code></td>
        <td><code>${poutStr}</code></td>
        <td>${r.work_hours !== null ? `<strong>${r.work_hours}</strong> h` : '—'}</td>
        <td>${r.late_minutes > 0 ? `<span style="color:var(--rose); font-weight:600;">+${r.late_minutes} m</span>` : '0 m'}</td>
        <td>${r.overtime_minutes > 0 ? `<span style="color:var(--emerald); font-weight:600;">+${r.overtime_minutes} m</span>` : '0 m'}</td>
        <td>${r.half_day ? '<span class="badge badge-amber">Half Day</span>' : '<span style="color:var(--text-subtle);">No</span>'}</td>
        <td>
          ${hasHistory ? `
            <button class="btn btn-sm btn-ghost" onclick="toggleAuditHistory(${idx})" style="font-size:0.75rem;">
              📜 ${r.history.length} edit(s)
            </button>
            <div id="auditBox-${idx}" class="audit-history-box" style="display:none;">
              ${r.history.map(h => `
                <div class="audit-entry">
                  <div class="audit-meta"><span>By: ${h.by}</span> <span>${formatDateTimeIst(h.at)}</span></div>
                  <div class="audit-reason">"${h.reason}"</div>
                  <div class="audit-changes">Changes: ${JSON.stringify(h.changes)}</div>
                </div>
              `).join('')}
            </div>
          ` : '<span style="color:var(--text-subtle); font-size:0.75rem;">Original</span>'}
        </td>
        <td>
          <button class="btn btn-sm btn-outline" onclick="openRegularizeModal('${r.emp_code}', '${r.date}')">
            Regularize
          </button>
        </td>
      </tr>
    `;
  }).join('');

  // Update pagination info
  const start = (STATE.attendancePage - 1) * STATE.attendancePageSize + 1;
  const end = Math.min(STATE.attendancePage * STATE.attendancePageSize, STATE.attendanceTotal);
  document.getElementById('paginationInfo').textContent = `Showing ${STATE.attendanceTotal > 0 ? start : 0} to ${end} of ${STATE.attendanceTotal} records`;
  document.getElementById('currentPageBadge').textContent = `Page ${STATE.attendancePage}`;
  document.getElementById('prevPageBtn').disabled = STATE.attendancePage <= 1;
  document.getElementById('nextPageBtn').disabled = STATE.attendancePage * STATE.attendancePageSize >= STATE.attendanceTotal;
}

window.toggleAuditHistory = function(idx) {
  const el = document.getElementById(`auditBox-${idx}`);
  if (el) {
    el.style.display = el.style.display === 'none' ? 'block' : 'none';
  }
};

function resetFilters() {
  document.getElementById('filterEmpCode').value = '';
  document.getElementById('filterDateFrom').value = '';
  document.getElementById('filterDateTo').value = '';
  document.getElementById('filterStatus').value = '';
  STATE.attendancePage = 1;
  loadAttendanceLogs();
}

// --- REGULARIZE MODAL ---
window.openRegularizeModal = function(empCode, date) {
  const record = STATE.attendanceLogs.find(r => r.emp_code === empCode && r.date === date);
  if (!record) return;

  STATE.currentRegularizeRecord = record;
  const emp = STATE.employeesMap[empCode] || { name: empCode };

  document.getElementById('regEmpCode').value = empCode;
  document.getElementById('regDate').value = date;
  document.getElementById('regStatus').value = record.status;
  document.getElementById('regReason').value = '';
  document.getElementById('regBy').value = 'hr.admin';

  document.getElementById('regRecordMeta').innerHTML = `
    <span><strong>${emp.name}</strong> (${empCode})</span>
    <span>Date: <strong>${date}</strong></span>
  `;

  // Set time inputs in HH:MM:SS
  const inInput = document.getElementById('regPunchInTime');
  const outInput = document.getElementById('regPunchOutTime');

  inInput.value = record.punch_in ? getIstTimeOnly(record.punch_in) : '';
  outInput.value = record.punch_out ? getIstTimeOnly(record.punch_out) : '';

  const isAbsOrLeave = ['ABSENT', 'LEAVE'].includes(record.status);
  inInput.disabled = isAbsOrLeave;
  outInput.disabled = isAbsOrLeave;

  document.getElementById('regularizeModal').classList.remove('hidden');
};

function closeRegularizeModal() {
  document.getElementById('regularizeModal').classList.add('hidden');
  STATE.currentRegularizeRecord = null;
}

async function handleRegularizeSubmit(e) {
  e.preventDefault();
  const empCode = document.getElementById('regEmpCode').value;
  const date = document.getElementById('regDate').value;
  const status = document.getElementById('regStatus').value;
  const reason = document.getElementById('regReason').value.trim();
  const regBy = document.getElementById('regBy').value.trim();

  const isAbsOrLeave = ['ABSENT', 'LEAVE'].includes(status);
  let punchInMs = null;
  let punchOutMs = null;

  if (!isAbsOrLeave) {
    const inTime = document.getElementById('regPunchInTime').value;
    const outTime = document.getElementById('regPunchOutTime').value;

    if (inTime) {
      punchInMs = parseIstDateTimeToMs(date, inTime);
    }
    if (outTime) {
      punchOutMs = parseIstDateTimeToMs(date, outTime);
    }
  }

  const payload = {
    status,
    reason,
    regularized_by: regBy
  };
  if (punchInMs !== null) payload.punch_in = punchInMs;
  if (punchOutMs !== null) payload.punch_out = punchOutMs;

  try {
    const res = await fetch(`/attendance/${empCode}/${date}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Regularization failed');
    }

    showToast('Attendance successfully regularized and audit trail recorded!', 'success');
    closeRegularizeModal();
    await loadAttendanceLogs();
    await loadAnalytics();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// --- ADD EMPLOYEE MODAL ---
function openAddEmpModal() {
  document.getElementById('addEmpModal').classList.remove('hidden');
}

function closeAddEmpModal() {
  document.getElementById('addEmpModal').classList.add('hidden');
}

async function handleAddEmpSubmit(e) {
  e.preventDefault();
  const payload = {
    emp_code: document.getElementById('newEmpCode').value.trim(),
    name: document.getElementById('newEmpName').value.trim(),
    email: document.getElementById('newEmpEmail').value.trim(),
    department: document.getElementById('newEmpDept').value.trim(),
    shift_start: document.getElementById('newShiftStart').value.trim(),
    shift_end: document.getElementById('newShiftEnd').value.trim(),
    joined_on: document.getElementById('newJoinedOn').value.trim(),
  };

  try {
    const res = await fetch('/employees', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Could not create employee');
    }

    showToast(`Employee ${data.name} (${data.emp_code}) created!`, 'success');
    closeAddEmpModal();
    document.getElementById('addEmpForm').reset();
    await loadEmployees();
    loadEmployeesTable();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// --- ANALYTICS DASHBOARD ---
async function loadAnalytics() {
  const month = STATE.selectedMonth;
  const deptScope = document.getElementById('analyticsDeptSelect').value;

  await Promise.all([
    loadDepartmentSummary(month, deptScope),
    loadLateLeaderboard(month),
    loadTrendChart(),
    loadEmployeeMonthlyDrilldown(STATE.selectedEmpCode || (STATE.employees[0] ? STATE.employees[0].emp_code : 'EMP0001'))
  ]);
}

async function loadDepartmentSummary(month, deptScope) {
  const grid = document.getElementById('deptSummaryGrid');
  try {
    let url = `/analytics/departments/summary?month=${month}`;
    if (deptScope) url += `&department=${encodeURIComponent(deptScope)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to load dept summary');
    const data = await res.json();

    if (!data.items || data.items.length === 0) {
      grid.innerHTML = '<div class="text-center py-4 text-muted">No department records for this period.</div>';
      return;
    }

    grid.innerHTML = data.items.map(d => `
      <div class="dept-card">
        <div class="dept-card-header">
          <span class="dept-card-title">${d.department}</span>
          <span class="badge badge-cyan">${d.headcount} Headcount</span>
        </div>
        <div class="dept-metrics-grid">
          <div class="dept-metric-item">
            <span class="dept-metric-val">${d.present_days}</span>
            <span class="dept-metric-lbl">Present Days</span>
          </div>
          <div class="dept-metric-item">
            <span class="dept-metric-val">${d.avg_work_hours !== null ? d.avg_work_hours + 'h' : '—'}</span>
            <span class="dept-metric-lbl">Avg Work Hours</span>
          </div>
          <div class="dept-metric-item">
            <span class="dept-metric-val" style="color:var(--rose);">${d.late_count}</span>
            <span class="dept-metric-lbl">Late Count (${d.total_late_minutes}m)</span>
          </div>
          <div class="dept-metric-item">
            <span class="dept-metric-val" style="color:var(--amber);">${d.leave_count}</span>
            <span class="dept-metric-lbl">Leave Days</span>
          </div>
        </div>
      </div>
    `).join('');

    // Update top hero stats
    const totalLateIncidents = data.items.reduce((acc, it) => acc + it.late_count, 0);
    document.getElementById('lateIncidentsStat').textContent = totalLateIncidents;

  } catch (err) {
    grid.innerHTML = `<div class="text-center py-4 text-rose">Error: ${err.message}</div>`;
  }
}

async function loadLateLeaderboard(month) {
  const list = document.getElementById('lateLeaderboardList');
  try {
    const res = await fetch(`/analytics/leaderboard/late?month=${month}&limit=10`);
    if (!res.ok) throw new Error('Failed to load leaderboard');
    const data = await res.json();

    if (!data.items || data.items.length === 0) {
      list.innerHTML = '<div class="text-center py-4 text-muted">No late-comers recorded for this month! 🎉</div>';
      return;
    }

    list.innerHTML = data.items.map(item => {
      let rankClass = 'rank-other';
      if (item.rank === 1) rankClass = 'rank-1';
      else if (item.rank === 2) rankClass = 'rank-2';
      else if (item.rank === 3) rankClass = 'rank-3';

      return `
        <div class="leaderboard-row">
          <div class="rank-badge ${rankClass}">#${item.rank}</div>
          <div class="leaderboard-emp-info">
            <div class="leaderboard-emp-name">${item.name} <small style="color:var(--text-muted);">(${item.emp_code})</small></div>
            <div class="leaderboard-emp-dept">${item.department} • ${item.late_count} late shift(s)</div>
          </div>
          <div class="leaderboard-late-val">
            ${item.total_late_minutes} mins
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    list.innerHTML = `<div class="text-center py-4 text-rose">${err.message}</div>`;
  }
}

async function loadTrendChart() {
  const dept = STATE.selectedTrendDept;
  const month = STATE.selectedMonth;
  // Compute from / to for the month
  const [year, mon] = month.split('-').map(Number);
  const fromDate = `${month}-01`;
  const lastDay = new Date(year, mon, 0).getDate();
  const toDate = `${month}-${String(lastDay).padStart(2, '0')}`;

  try {
    const res = await fetch(`/analytics/departments/${encodeURIComponent(dept)}/trend?from=${fromDate}&to=${toDate}`);
    if (!res.ok) throw new Error('Failed to load department trend');
    const data = await res.json();
    renderSvgTrend(data.items || []);
  } catch (err) {
    console.error('Trend load error', err);
  }
}

function renderSvgTrend(items) {
  const svg = document.getElementById('trendSvgChart');
  if (!svg || items.length === 0) return;

  const width = 800;
  const height = 300;
  const padding = { top: 30, right: 30, bottom: 40, left: 50 };

  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  // Max attendance rate is 1.0 (100%)
  const maxY = 1.0;
  const numDays = items.length;
  const stepX = chartW / Math.max(numDays - 1, 1);

  let svgContent = '';

  // Background Grid Lines
  for (let i = 0; i <= 4; i++) {
    const yVal = (maxY / 4) * i;
    const yPos = padding.top + chartH - (yVal / maxY) * chartH;
    svgContent += `
      <line x1="${padding.left}" y1="${yPos}" x2="${width - padding.right}" y2="${yPos}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="3 3"/>
      <text x="${padding.left - 10}" y="${yPos + 4}" fill="#64748b" font-size="11" text-anchor="end">${Math.round(yVal * 100)}%</text>
    `;
  }

  // Draw Weekend background strips
  items.forEach((item, idx) => {
    if (!item.is_working_day) {
      const x = padding.left + idx * stepX - stepX / 2;
      svgContent += `
        <rect x="${x}" y="${padding.top}" width="${stepX}" height="${chartH}" fill="rgba(255,255,255,0.02)"/>
      `;
    }
  });

  // Calculate points for Daily Attendance Rate
  const ratePoints = [];
  const maPoints = [];

  items.forEach((item, idx) => {
    const x = padding.left + idx * stepX;
    if (item.attendance_rate !== null) {
      const y = padding.top + chartH - (item.attendance_rate / maxY) * chartH;
      ratePoints.push({ x, y, item });
    }
    if (item.moving_avg_7d !== null) {
      const yMA = padding.top + chartH - (item.moving_avg_7d / maxY) * chartH;
      maPoints.push({ x, y: yMA, item });
    }
  });

  // Render Daily Rate Line & Points
  if (ratePoints.length > 1) {
    const pathD = ratePoints.map((pt, i) => (i === 0 ? `M ${pt.x} ${pt.y}` : `L ${pt.x} ${pt.y}`)).join(' ');
    // Area fill
    const areaD = `${pathD} L ${ratePoints[ratePoints.length - 1].x} ${padding.top + chartH} L ${ratePoints[0].x} ${padding.top + chartH} Z`;
    svgContent += `
      <path d="${areaD}" fill="url(#rateGradient)" opacity="0.25"/>
      <path d="${pathD}" fill="none" stroke="var(--cyan)" stroke-width="2.5"/>
    `;
  }

  // Render 7-Day Moving Average Line
  if (maPoints.length > 1) {
    const maPathD = maPoints.map((pt, i) => (i === 0 ? `M ${pt.x} ${pt.y}` : `L ${pt.x} ${pt.y}`)).join(' ');
    svgContent += `
      <path d="${maPathD}" fill="none" stroke="var(--emerald)" stroke-width="3" stroke-linecap="round"/>
    `;
  }

  // Draw Data Points with Tooltips
  ratePoints.forEach(pt => {
    const pct = Math.round(pt.item.attendance_rate * 100);
    svgContent += `
      <circle cx="${pt.x}" cy="${pt.y}" r="4.5" fill="var(--bg-deep)" stroke="var(--cyan)" stroke-width="2">
        <title>${pt.item.date}: ${pct}% (Present: ${pt.item.present_count}/${pt.item.headcount})</title>
      </circle>
    `;
  });

  // X-Axis Date Labels (Every 3-4 days to avoid clutter)
  items.forEach((item, idx) => {
    if (idx % 3 === 0 || idx === items.length - 1) {
      const x = padding.left + idx * stepX;
      const dayLabel = item.date.substring(8);
      svgContent += `
        <text x="${x}" y="${height - 12}" fill="#64748b" font-size="11" text-anchor="middle">${dayLabel}</text>
      `;
    }
  });

  svg.innerHTML = `
    <defs>
      <linearGradient id="rateGradient" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--cyan)" stop-opacity="0.8"/>
        <stop offset="100%" stop-color="var(--cyan)" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${svgContent}
  `;
}

async function loadEmployeeMonthlyDrilldown(empCode) {
  if (!empCode) return;
  const month = STATE.selectedMonth;

  try {
    const res = await fetch(`/analytics/employees/${empCode}/monthly?month=${month}`);
    if (!res.ok) throw new Error('Failed to load employee drilldown');
    const data = await res.json();

    const attPct = data.attendance_pct !== null ? `${data.attendance_pct}%` : 'N/A';
    document.getElementById('ddAttPct').textContent = attPct;
    document.getElementById('ddAttProgress').style.width = data.attendance_pct !== null ? `${Math.min(data.attendance_pct, 100)}%` : '0%';

    document.getElementById('ddWorkingDays').textContent = data.working_days;
    document.getElementById('ddPresentDays').textContent = data.present_days;
    document.getElementById('ddLeaveDays').textContent = data.leave_days;
    document.getElementById('ddLateInfo').textContent = `${data.late_count} (${data.total_late_minutes}m)`;
    document.getElementById('ddOtMinutes').textContent = `${data.total_overtime_minutes} min`;

    // Also update top stat card if it's the first employee
    document.getElementById('attRateStat').textContent = attPct;
    document.getElementById('overtimeStat').textContent = `${data.total_overtime_minutes} min`;
  } catch (err) {
    console.error('Drilldown error', err);
  }
}

// --- MONGODB EXPLAIN INSPECTOR ---
async function runExplainQuery() {
  const ep = document.getElementById('explainEndpointSelect').value;
  const dept = document.getElementById('explainDeptInput').value.trim();
  const month = document.getElementById('explainMonthInput').value;

  const params = new URLSearchParams();
  if (month) params.append('month', month);
  if (dept) params.append('department', dept);
  if (ep === 'employee_monthly') {
    params.append('emp_code', STATE.selectedEmpCode || 'EMP0001');
  }
  if (ep === 'department_trend') {
    params.append('from', `${month}-01`);
    params.append('to', `${month}-15`);
  }

  const pre = document.getElementById('explainJsonPre');
  pre.textContent = 'Executing explain on MongoDB...';

  try {
    const res = await fetch(`/admin/explain/${ep}?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Explain failed');

    pre.textContent = JSON.stringify(data, null, 2);

    document.getElementById('expCollection').textContent = data.collection || 'attendance_logs';

    // Parse out winning plan details if available
    let winningStage = 'AGGREGATION';
    let indexUsed = 'Compound Key / Natural';

    if (data.explain && data.explain.stages) {
      winningStage = `$cursor (${data.explain.stages.length} stages)`;
    } else if (data.explain && data.explain.queryPlanner) {
      winningStage = data.explain.queryPlanner.winningPlan ? data.explain.queryPlanner.winningPlan.stage : 'COLLSCAN';
      if (data.explain.queryPlanner.winningPlan && data.explain.queryPlanner.winningPlan.inputStage) {
        indexUsed = data.explain.queryPlanner.winningPlan.inputStage.indexName || 'COLLSCAN';
      }
    }

    document.getElementById('expStage').textContent = winningStage;
    document.getElementById('expIndexName').textContent = indexUsed;

    showToast(`Query plan extracted for ${ep}!`, 'success');
  } catch (err) {
    pre.textContent = `Error: ${err.message}`;
    showToast(err.message, 'error');
  }
}

// --- TOAST NOTIFICATIONS ---
function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <div>${message}</div>
  `;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// --- UTILITY DATE / TIME HELPERS ---
function formatTimeIst(ms) {
  if (!ms) return '—';
  const dt = new Date(ms);
  // Extract IST (UTC + 5:30)
  const utc = dt.getTime() + (dt.getTimezoneOffset() * 60000);
  const ist = new Date(utc + (5.5 * 3600000));
  return ist.toTimeString().substring(0, 8);
}

function formatDateTimeIst(ms) {
  if (!ms) return '—';
  const dt = new Date(ms);
  const utc = dt.getTime() + (dt.getTimezoneOffset() * 60000);
  const ist = new Date(utc + (5.5 * 3600000));
  return `${ist.toISOString().substring(0, 10)} ${ist.toTimeString().substring(0, 8)} IST`;
}

function getIstTimeOnly(ms) {
  if (!ms) return '';
  const dt = new Date(ms);
  const utc = dt.getTime() + (dt.getTimezoneOffset() * 60000);
  const ist = new Date(utc + (5.5 * 3600000));
  return ist.toTimeString().substring(0, 8);
}

function parseIstDateTimeToMs(dateStr, timeStr) {
  // YYYY-MM-DD and HH:MM(:SS) in IST
  const [year, month, day] = dateStr.split('-').map(Number);
  const timeParts = timeStr.split(':').map(Number);
  const hour = timeParts[0];
  const minute = timeParts[1];
  const second = timeParts[2] || 0;

  // Convert IST components to UTC: UTC = IST - 5h 30m
  const istDateAsUtc = Date.UTC(year, month - 1, day, hour, minute, second);
  return istDateAsUtc - (5.5 * 3600000);
}
