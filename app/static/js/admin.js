/**
 * Apex University Library - Administrator Portal Controller
 * Manages institutional circulation desk (checkout/return), catalog inspection,
 * academic calendar CRUD, and demand/stockout analytics.
 */

(function () {
  const API_BASE = window.LIBRARY_CONFIG?.apiBase || "/api";
  let adminKey = sessionStorage.getItem("apex_admin_key") || window.LIBRARY_CONFIG?.defaultAdminKey || "library-admin-secret-key-2026";

  // Tab State
  let currentTab = "tab-overview";
  let statsData = null;
  let inventoryCache = [];
  let calendarCache = [];

  // DOM Cache
  const dom = {};

  function init() {
    cacheDom();
    bindEvents();
    switchTab("tab-overview");
    setupAdminKey();
    updateOperationalMode();
  }

  async function updateOperationalMode() {
    const badge = document.getElementById("operational-mode-badge");
    const text = document.getElementById("operational-mode-text");
    const footer = document.getElementById("operational-mode-footer");
    const sysStatus = document.getElementById("operational-mode-system-status");

    if (window.LIBRARY_CONFIG && window.LIBRARY_CONFIG.isDemoMode) {
      if (badge) {
        badge.textContent = "Demo Prototype";
        badge.style.background = "";
      }
      if (text) text.textContent = "Operating on local simulated holdings and operational transaction data. Local mode — zero AWS cloud resources billed.";
      if (footer) footer.textContent = "Operating in Local Operational Mode (No AWS Billable Resources)";
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/health`);
      if (res.ok) {
        const data = await res.json();
        const provider = data.cloud_provider || data.environment?.provider || (data.environment?.aws_region ? "AWS" : "DigitalOcean");
        const region = data.environment?.region || data.environment?.aws_region || "blr1";
        const isDO = provider.toLowerCase().includes("digitalocean") || provider.toLowerCase().includes("do");
        if (badge) {
          if (isDO) {
            badge.textContent = `DigitalOcean (${region.toUpperCase()})`;
            badge.style.background = "#0080FF";
            badge.style.color = "#ffffff";
          } else {
            badge.textContent = `${provider} (${region})`;
            badge.style.background = "#059669";
            badge.style.color = "#ffffff";
          }
        }
        if (text) {
          if (isDO) {
            text.textContent = `Connected to live DigitalOcean production node in region ${region}. Real-time catalog & academic calendar engine.`;
          } else {
            text.textContent = `Connected to live ${provider} in Region ${region}. Live transactions update in real time.`;
          }
        }
        if (footer) footer.textContent = `Operating on ${provider} Cloud (${region})`;
        if (sysStatus) {
          if (isDO) {
            sysStatus.innerHTML = `Environment: <strong>DigitalOcean Cloud (${region.toUpperCase()})</strong><br>Service: <strong>App Platform / Droplet Container</strong><br>Policy: First-Come, First-Served (FCFS)`;
          } else {
            sysStatus.innerHTML = `Environment: <strong>${provider} Cloud Production (${region})</strong><br>Integration: <strong>Active</strong><br>Policy: First-Come, First-Served (FCFS)`;
          }
        }
      }
    } catch (_) {}
  }

  function cacheDom() {
    dom.adminKeyInput = document.getElementById("admin-key-input");
    dom.navItems = document.querySelectorAll(".admin-nav-item");
    dom.tabPanes = document.querySelectorAll(".admin-tab-pane");

    // Overview KPIs
    dom.kpiTitles = document.getElementById("kpi-titles");
    dom.kpiTotalCopies = document.getElementById("kpi-total-copies");
    dom.kpiAvailable = document.getElementById("kpi-available");
    dom.kpiOnLoan = document.getElementById("kpi-on-loan");
    dom.kpiLowStock = document.getElementById("kpi-low-stock");
    dom.kpiOutOfStock = document.getElementById("kpi-out-of-stock");
    dom.overviewLowStockTable = document.getElementById("overview-low-stock-table");
    dom.overviewRecentTxTable = document.getElementById("overview-recent-tx-table");

    // Circulation Desk
    dom.checkoutForm = document.getElementById("checkout-form");
    dom.checkoutBookId = document.getElementById("checkout-book-id");
    dom.checkoutStudentId = document.getElementById("checkout-student-id");
    dom.checkoutNotes = document.getElementById("checkout-notes");
    dom.checkoutFeedback = document.getElementById("checkout-feedback");

    dom.returnForm = document.getElementById("return-form");
    dom.returnBookId = document.getElementById("return-book-id");
    dom.returnStudentId = document.getElementById("return-student-id");
    dom.returnNotes = document.getElementById("return-notes");
    dom.returnFeedback = document.getElementById("return-feedback");

    // Inventory Tab
    dom.invSearch = document.getElementById("inv-search");
    dom.invDeptFilter = document.getElementById("inv-dept-filter");
    dom.invTableBody = document.getElementById("inv-table-body");
    dom.invStats = document.getElementById("inv-stats");

    // Calendar Tab
    dom.calTableBody = document.getElementById("admin-cal-table-body");
    dom.addExamForm = document.getElementById("add-exam-form");
    dom.calFeedback = document.getElementById("cal-feedback");

    // Analytics Tab
    dom.analyticsTableBody = document.getElementById("analytics-table-body");

    // Audit Log Tab
    dom.auditTableBody = document.getElementById("audit-table-body");
  }

  function bindEvents() {
    // Navigation items
    dom.navItems.forEach(item => {
      item.addEventListener("click", (e) => {
        e.preventDefault();
        const target = item.getAttribute("data-tab");
        if (target) switchTab(target);
      });
    });

    // Key update listener
    if (dom.adminKeyInput) {
      dom.adminKeyInput.value = adminKey;
      dom.adminKeyInput.addEventListener("change", (e) => {
        adminKey = e.target.value.trim();
        sessionStorage.setItem("apex_admin_key", adminKey);
        loadCurrentTabData();
      });
    }

    // Circulation forms
    if (dom.checkoutForm) dom.checkoutForm.addEventListener("submit", handleCheckoutSubmit);
    if (dom.returnForm) dom.returnForm.addEventListener("submit", handleReturnSubmit);

    // Inventory search/filter
    if (dom.invSearch) dom.invSearch.addEventListener("input", debounce(loadInventoryTable, 300));
    if (dom.invDeptFilter) dom.invDeptFilter.addEventListener("change", loadInventoryTable);

    // Add Exam form
    if (dom.addExamForm) dom.addExamForm.addEventListener("submit", handleAddExamSubmit);
  }

  function setupAdminKey() {
    if (dom.adminKeyInput) {
      dom.adminKeyInput.value = adminKey;
    }
  }

  function getHeaders() {
    return {
      "Content-Type": "application/json",
      "X-Admin-Key": adminKey,
      "Authorization": `Bearer ${adminKey}`
    };
  }

  // --- Tab Switching ---
  function switchTab(tabId) {
    currentTab = tabId;
    dom.navItems.forEach(item => {
      item.classList.toggle("active", item.getAttribute("data-tab") === tabId);
    });
    dom.tabPanes.forEach(pane => {
      pane.classList.toggle("active", pane.id === tabId);
    });
    loadCurrentTabData();
  }

  function loadCurrentTabData() {
    switch (currentTab) {
      case "tab-overview":
        loadOverviewStats();
        break;
      case "tab-circ":
        // pre-load recent feedback or clear
        break;
      case "tab-inventory":
        loadInventoryTable();
        break;
      case "tab-calendar":
        loadAdminCalendar();
        break;
      case "tab-analytics":
        loadDemandAnalytics();
        break;
      case "tab-audit":
        loadAuditLog();
        break;
    }
  }

  // --- 1. Admin Overview ---
  async function loadOverviewStats() {
    try {
      const res = await fetch(`${API_BASE}/admin/stats`, { headers: getHeaders() });
      if (!res.ok) {
        showAuthWarning();
        return;
      }
      statsData = await res.json();

      dom.kpiTitles.textContent = statsData.total_titles.toLocaleString();
      dom.kpiTotalCopies.textContent = statsData.total_copies.toLocaleString();
      dom.kpiAvailable.textContent = statsData.available_copies.toLocaleString();
      dom.kpiOnLoan.textContent = statsData.on_loan_copies.toLocaleString();
      dom.kpiLowStock.textContent = statsData.low_stock_count.toLocaleString();
      dom.kpiOutOfStock.textContent = statsData.out_of_stock_count.toLocaleString();

      // Render Low Stock Attention Table
      const lowStockSample = (statsData.critical_sample || []).concat(statsData.low_stock_sample || []).slice(0, 6);
      if (lowStockSample.length === 0) {
        dom.overviewLowStockTable.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">All titles currently maintain healthy inventory buffers.</td></tr>`;
      } else {
        dom.overviewLowStockTable.innerHTML = lowStockSample.map(b => `
          <tr>
            <td><strong>${escapeHtml(b.book_id)}</strong></td>
            <td>${escapeHtml(b.title)}</td>
            <td>${escapeHtml(b.department)}</td>
            <td><strong style="color:${b.available_copies === 0 ? 'var(--status-critical)' : 'var(--status-high)'}">${b.available_copies}</strong> / ${b.total_copies}</td>
            <td>
              <span class="badge ${b.available_copies === 0 ? 'badge-critical' : 'badge-high'}">
                ${b.available_copies === 0 ? 'Exhausted' : 'Low Stock'}
              </span>
            </td>
          </tr>
        `).join("");
      }

      // Render Recent Transactions Feed
      const recentTx = statsData.recent_transactions || [];
      if (recentTx.length === 0) {
        dom.overviewRecentTxTable.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">No circulation transactions recorded yet in this session.</td></tr>`;
      } else {
        dom.overviewRecentTxTable.innerHTML = recentTx.slice(0, 5).map(tx => `
          <tr>
            <td style="font-family:var(--font-mono); font-size:0.8rem;">${escapeHtml(tx.transaction_id)}</td>
            <td>
              <span class="badge ${tx.transaction_type === 'CHECKOUT' ? 'badge-critical' : 'badge-safe'}">
                ${escapeHtml(tx.transaction_type)}
              </span>
            </td>
            <td><strong>${escapeHtml(tx.book_id)}</strong></td>
            <td>${escapeHtml(tx.student_id || 'Staff Portal')}</td>
            <td>${new Date(tx.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</td>
          </tr>
        `).join("");
      }
    } catch (err) {
      console.error("Overview stats error:", err);
    }
  }

  // --- 2. Circulation Desk Operations ---
  async function handleCheckoutSubmit(e) {
    e.preventDefault();
    const bookId = dom.checkoutBookId.value.trim().toUpperCase();
    const studentId = dom.checkoutStudentId.value.trim() || "STUDENT_DESK";
    const notes = dom.checkoutNotes.value.trim() || "Staff desk checkout";
    const idemKey = `desk-co-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`;

    if (!bookId) return;
    dom.checkoutFeedback.innerHTML = `<div class="alert alert-info">Processing atomic checkout...</div>`;

    try {
      const res = await fetch(`${API_BASE}/transactions/checkout`, {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({
          book_id: bookId,
          idempotency_key: idemKey,
          student_id: studentId,
          notes: notes
        })
      });

      const data = await res.json();
      if (res.ok) {
        dom.checkoutFeedback.innerHTML = `
          <div class="alert alert-success">
            <strong>✓ Checkout Completed:</strong> 1 copy of book <strong>${escapeHtml(bookId)}</strong> checked out to ${escapeHtml(studentId)}.<br>
            <span style="font-size:0.8rem;">Transaction ID: ${escapeHtml(data.data?.transaction_id || '')} &bull; Copies remaining: <strong>${data.data?.available_copies_after ?? 'N/A'}</strong></span>
          </div>
        `;
        dom.checkoutBookId.value = "";
        dom.checkoutNotes.value = "";
        loadOverviewStats();
      } else {
        dom.checkoutFeedback.innerHTML = `
          <div class="alert alert-danger">
            <strong>✗ Checkout Failed:</strong> ${escapeHtml(data.error || 'Inventory error')}
          </div>
        `;
      }
    } catch (err) {
      dom.checkoutFeedback.innerHTML = `
        <div class="alert alert-danger">
          <strong>Network Error:</strong> ${escapeHtml(err.message)}
        </div>
      `;
    }
  }

  async function handleReturnSubmit(e) {
    e.preventDefault();
    const bookId = dom.returnBookId.value.trim().toUpperCase();
    const studentId = dom.returnStudentId.value.trim() || "STUDENT_DESK";
    const notes = dom.returnNotes.value.trim() || "Staff desk return";
    const idemKey = `desk-ret-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`;

    if (!bookId) return;
    dom.returnFeedback.innerHTML = `<div class="alert alert-info">Processing atomic return...</div>`;

    try {
      const res = await fetch(`${API_BASE}/transactions/return`, {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({
          book_id: bookId,
          idempotency_key: idemKey,
          student_id: studentId,
          notes: notes
        })
      });

      const data = await res.json();
      if (res.ok) {
        dom.returnFeedback.innerHTML = `
          <div class="alert alert-success">
            <strong>✓ Return Completed:</strong> 1 copy of book <strong>${escapeHtml(bookId)}</strong> returned to physical holdings.<br>
            <span style="font-size:0.8rem;">Transaction ID: ${escapeHtml(data.data?.transaction_id || '')} &bull; Available copies recovered: <strong>${data.data?.available_copies_after ?? 'N/A'}</strong></span>
          </div>
        `;
        dom.returnBookId.value = "";
        dom.returnNotes.value = "";
        loadOverviewStats();
      } else {
        dom.returnFeedback.innerHTML = `
          <div class="alert alert-danger">
            <strong>✗ Return Failed:</strong> ${escapeHtml(data.error || 'Invalid return action')}
          </div>
        `;
      }
    } catch (err) {
      dom.returnFeedback.innerHTML = `
        <div class="alert alert-danger">
          <strong>Network Error:</strong> ${escapeHtml(err.message)}
        </div>
      `;
    }
  }

  // --- 3. Inventory Management ---
  async function loadInventoryTable() {
    const q = (dom.invSearch?.value || "").trim();
    const dept = dom.invDeptFilter?.value || "";

    dom.invTableBody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:24px; color:var(--text-muted);">Loading inventory catalog records...</td></tr>`;

    let url = `${API_BASE}/books?limit=25`;
    if (q) url += `&q=${encodeURIComponent(q)}`;
    if (dept) url += `&department=${encodeURIComponent(dept)}`;

    try {
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      const books = data.books || [];

      dom.invStats.textContent = `Showing ${books.length} of ${data.total_matches} catalog titles`;

      if (!books.length) {
        dom.invTableBody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:24px; color:var(--text-muted);">No book inventory records found.</td></tr>`;
        return;
      }

      dom.invTableBody.innerHTML = books.map(b => {
        let badge = "badge-safe";
        let label = "Available";
        if (b.available_copies === 0) {
          badge = "badge-critical";
          label = "Out of Stock";
        } else if (b.available_copies <= 2) {
          badge = "badge-high";
          label = "Low Buffer";
        }

        return `
          <tr>
            <td><strong>${escapeHtml(b.book_id)}</strong></td>
            <td>${escapeHtml(b.title)}</td>
            <td>${escapeHtml(b.author)}</td>
            <td>${escapeHtml(b.department)}</td>
            <td>${escapeHtml(b.edition || '1st Edition')}</td>
            <td>${b.total_copies}</td>
            <td><strong style="color:${b.available_copies === 0 ? 'var(--status-critical)' : 'var(--text-primary)'}">${b.available_copies}</strong></td>
            <td><span class="badge ${badge}">${label}</span></td>
          </tr>
        `;
      }).join("");
    } catch (err) {
      dom.invTableBody.innerHTML = `<tr><td colspan="8" style="color:var(--status-critical); padding:16px;">Failed to load inventory: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  // --- 4. Academic Calendar CRUD ---
  async function loadAdminCalendar() {
    dom.calTableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:20px; color:var(--text-muted);">Loading official academic calendar entries...</td></tr>`;

    try {
      const res = await fetch(`${API_BASE}/admin/calendar`, { headers: getHeaders() });
      if (!res.ok) {
        dom.calTableBody.innerHTML = `<tr><td colspan="7" style="color:red; text-align:center; padding:16px;">Authentication Error: Invalid Admin Secret Key.</td></tr>`;
        return;
      }
      const data = await res.json();
      calendarCache = data.entries || [];

      if (!calendarCache.length) {
        dom.calTableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:16px; color:var(--text-muted);">No calendar entries found.</td></tr>`;
        return;
      }

      dom.calTableBody.innerHTML = calendarCache.map(e => `
        <tr>
          <td><strong style="font-family:var(--font-mono); font-size:0.85rem;">${escapeHtml(e.calendar_id)}</strong></td>
          <td>${escapeHtml(e.academic_year)} (${escapeHtml(e.semester)})</td>
          <td><strong>${escapeHtml(e.exam_type)}</strong></td>
          <td>${escapeHtml(e.start_date)} &rarr; ${escapeHtml(e.end_date)}</td>
          <td>
            <span class="badge ${e.status === 'scheduled' ? 'badge-safe' : (e.status === 'completed' ? 'badge-info' : 'badge-critical')}">
              ${escapeHtml(e.status)}
            </span>
          </td>
          <td><span style="font-size:0.8rem; color:var(--text-muted);">${escapeHtml(e.notes || '—')}</span></td>
          <td>
            ${e.status === 'scheduled' ? `
              <button class="btn btn-danger btn-sm" onclick="window.adminCancelExam('${escapeHtml(e.calendar_id)}')">
                Cancel
              </button>
            ` : '<span style="color:var(--text-muted); font-size:0.8rem;">Archived</span>'}
          </td>
        </tr>
      `).join("");
    } catch (err) {
      dom.calTableBody.innerHTML = `<tr><td colspan="7" style="color:red; padding:16px;">Error loading calendar: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  async function handleAddExamSubmit(e) {
    e.preventDefault();
    const payload = {
      academic_year: document.getElementById("new-cal-year").value.trim(),
      semester: document.getElementById("new-cal-sem").value,
      exam_type: document.getElementById("new-cal-type").value,
      start_date: document.getElementById("new-cal-start").value,
      end_date: document.getElementById("new-cal-end").value,
      status: document.getElementById("new-cal-status").value,
      notes: document.getElementById("new-cal-notes").value.trim()
    };

    if (payload.end_date < payload.start_date) {
      dom.calFeedback.innerHTML = `<div class="alert alert-danger">Validation Error: End date cannot be earlier than start date.</div>`;
      return;
    }

    dom.calFeedback.innerHTML = `<div class="alert alert-info">Scheduling official exam...</div>`;

    try {
      const res = await fetch(`${API_BASE}/admin/calendar`, {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        dom.calFeedback.innerHTML = `<div class="alert alert-success">✓ Exam scheduled successfully. Dynamic countdowns and student forecasts updated immediately.</div>`;
        document.getElementById("new-cal-notes").value = "";
        loadAdminCalendar();
      } else {
        dom.calFeedback.innerHTML = `<div class="alert alert-danger">✗ Failed to schedule exam: ${escapeHtml(data.error || 'Server error')}</div>`;
      }
    } catch (err) {
      dom.calFeedback.innerHTML = `<div class="alert alert-danger">Error: ${escapeHtml(err.message)}</div>`;
    }
  }

  window.adminCancelExam = async function (calId) {
    if (!confirm(`Are you sure you want to cancel authoritative exam entry ${calId}?`)) return;
    try {
      const res = await fetch(`${API_BASE}/admin/calendar/${encodeURIComponent(calId)}`, {
        method: "DELETE",
        headers: getHeaders()
      });
      if (res.ok) {
        loadAdminCalendar();
      } else {
        alert("Failed to deactivate calendar entry.");
      }
    } catch (err) {
      alert(err.message);
    }
  };

  // --- 5. Demand & Shortage Analytics ---
  async function loadDemandAnalytics() {
    dom.analyticsTableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:24px; color:var(--text-muted);">Evaluating high-risk titles across linear regression and borrowing velocity models...</td></tr>`;

    try {
      // Pull low inventory titles
      const candidateBooks = ["B16469", "B08510", "B00037", "B01926"];
      const predictions = [];

      for (const bid of candidateBooks) {
        try {
          const r = await fetch(`${API_BASE}/predict/${bid}`);
          if (r.ok) {
            predictions.push(await r.json());
          }
        } catch (_) {}
      }

      if (!predictions.length) {
        dom.analyticsTableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:16px;">No analytics data available.</td></tr>`;
        return;
      }

      dom.analyticsTableBody.innerHTML = predictions.map(p => {
        const inv = p.inventory || {};
        const dem = p.demand_forecast || {};
        const risk = p.stockout_assessment || {};

        let badge = "badge-safe";
        if (risk.risk_level === "CRITICAL") badge = "badge-critical";
        else if (risk.risk_level === "HIGH") badge = "badge-high";
        else if (risk.risk_level === "MODERATE") badge = "badge-moderate";

        return `
          <tr>
            <td><strong>${escapeHtml(p.book_id)}</strong></td>
            <td>${escapeHtml(p.title)}</td>
            <td>${escapeHtml(p.department)}</td>
            <td><strong>${inv.available_copies}</strong> / ${inv.total_copies}</td>
            <td>${(dem.predicted_next_7d_demand ?? 0).toFixed(2)} checkouts</td>
            <td><strong>${(dem.daily_burn_rate ?? 0).toFixed(2)}</strong> /day</td>
            <td>
              <span class="badge ${badge}">${escapeHtml(risk.risk_level)}</span>
              <div style="font-size:0.75rem; color:var(--text-muted); margin-top:3px;">
                ${risk.estimated_days_to_stockout !== null ? `${risk.estimated_days_to_stockout} days to stockout` : 'Adequate buffer'}
              </div>
            </td>
          </tr>
        `;
      }).join("");
    } catch (err) {
      dom.analyticsTableBody.innerHTML = `<tr><td colspan="7" style="color:red; padding:16px;">Failed to load analytics: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  // --- 6. Circulation Audit Trail ---
  async function loadAuditLog() {
    dom.auditTableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:20px; color:var(--text-muted);">Loading circulation audit events...</td></tr>`;

    try {
      const res = await fetch(`${API_BASE}/admin/transactions?limit=50`, { headers: getHeaders() });
      if (!res.ok) {
        dom.auditTableBody.innerHTML = `<tr><td colspan="7" style="color:red; text-align:center;">Authorization error.</td></tr>`;
        return;
      }
      const data = await res.json();
      const txs = data.transactions || [];

      if (!txs.length) {
        dom.auditTableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:16px; color:var(--text-muted);">No audit transactions found.</td></tr>`;
        return;
      }

      dom.auditTableBody.innerHTML = txs.map(t => `
        <tr>
          <td style="font-family:var(--font-mono); font-size:0.82rem;">${escapeHtml(t.transaction_id)}</td>
          <td>${new Date(t.timestamp).toLocaleString()}</td>
          <td>
            <span class="badge ${t.transaction_type === 'CHECKOUT' ? 'badge-critical' : 'badge-safe'}">
              ${escapeHtml(t.transaction_type)}
            </span>
          </td>
          <td><strong>${escapeHtml(t.book_id)}</strong></td>
          <td>${escapeHtml(t.student_id || 'Desk Staff')}</td>
          <td>${t.available_after ?? '—'} copies</td>
          <td><span style="font-size:0.78rem; color:var(--text-muted);">${escapeHtml(t.notes || '—')}</span></td>
        </tr>
      `).join("");
    } catch (err) {
      dom.auditTableBody.innerHTML = `<tr><td colspan="7" style="color:red; padding:16px;">Audit log error: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  function showAuthWarning() {
    dom.kpiTitles.textContent = "—";
    dom.kpiTotalCopies.textContent = "—";
    dom.kpiAvailable.textContent = "—";
    dom.kpiOnLoan.textContent = "—";
    dom.kpiLowStock.textContent = "—";
    dom.kpiOutOfStock.textContent = "—";
    dom.overviewLowStockTable.innerHTML = `<tr><td colspan="5" style="color:var(--status-critical); text-align:center; padding:16px;">Authentication Required: Please enter the Administrator Key in the sidebar.</td></tr>`;
    dom.overviewRecentTxTable.innerHTML = `<tr><td colspan="5" style="color:var(--status-critical); text-align:center; padding:16px;">Authentication Required.</td></tr>`;
  }

  function debounce(func, wait) {
    let timeout;
    return function (...args) {
      clearTimeout(timeout);
      timeout = setTimeout(() => func.apply(this, args), wait);
    };
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  window.addEventListener("DOMContentLoaded", init);
})();
