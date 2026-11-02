/**
 * Apex University Library - Student Portal Frontend Controller
 * Handles catalog search, title/author autocomplete, category filtering,
 * pagination, and multi-tiered book availability & stockout forecasting.
 */

(function () {
  const API_BASE = window.LIBRARY_CONFIG?.apiBase || "/api";
  
  // State
  let state = {
    searchQuery: "",
    selectedDept: "",
    availabilityFilter: "all",
    currentPage: 1,
    pageSize: 12,
    totalMatches: 0,
    currentBooks: [],
    departments: [],
    upcomingExam: null,
    selectedBookId: null,
    debounceTimer: null,
    acSelectedIndex: -1,
    currentStudentId: localStorage.getItem("apex_student_id") || "STU-2026-042",
    activeLoans: [],
    currentView: "catalog",
    studentProfiles: {
      "STU-2026-042": { name: "Alex Chen", dept: "Computer Science" },
      "STU-2026-108": { name: "Priya Sharma", dept: "Mechanical Engineering" },
      "STU-2026-215": { name: "Rohan Das", dept: "Electrical Engineering" },
    },
  };

  // DOM Elements
  const dom = {};

  function init() {
    cacheDom();
    bindEvents();
    syncStudentProfile();
    updateOperationalMode();
    loadUpcomingCalendar();
    loadDepartments();
    loadCatalog();
    loadActiveLoans();
  }

  async function updateOperationalMode() {
    const badge = document.getElementById("operational-mode-badge");
    const text = document.getElementById("operational-mode-text");
    const footer = document.getElementById("operational-mode-footer");

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
            text.textContent = `Connected to live DigitalOcean production node in region ${region}. Live transactions & predictive availability active.`;
          } else {
            text.textContent = `Connected to live ${provider} in Region ${region}. Live transactions update in real time.`;
          }
        }
        if (footer) footer.textContent = `Operating on ${provider} Cloud (${region})`;
      }
    } catch (_) {}
  }

  function cacheDom() {
    dom.searchInput = document.getElementById("search-input");
    dom.searchClearBtn = document.getElementById("search-clear-btn");
    dom.searchForm = document.getElementById("search-form");
    dom.acMenu = document.getElementById("autocomplete-menu");
    dom.categoryChips = document.getElementById("category-chips");
    dom.deptFilter = document.getElementById("dept-filter");
    dom.availFilter = document.getElementById("avail-filter");
    dom.bookGrid = document.getElementById("book-grid");
    dom.toolbarStats = document.getElementById("toolbar-stats");
    dom.prevPageBtn = document.getElementById("prev-page-btn");
    dom.nextPageBtn = document.getElementById("next-page-btn");
    dom.pageIndicator = document.getElementById("page-indicator");
    dom.examBanner = document.getElementById("exam-banner");
    dom.examName = document.getElementById("exam-name");
    dom.examDates = document.getElementById("exam-dates");
    dom.examBadge = document.getElementById("exam-badge");
    
    // Nav & Borrower Profile DOM
    dom.navCatalogBtn = document.getElementById("nav-catalog-btn");
    dom.navBorrowedBtn = document.getElementById("nav-borrowed-btn");
    dom.borrowedCountPill = document.getElementById("borrowed-count-pill");
    dom.studentSelect = document.getElementById("student-identity-select");
    dom.catalogViewWrapper = document.getElementById("catalog-view-wrapper");
    dom.borrowedViewSection = document.getElementById("borrowed-view-section");
    dom.borrowerNameDisplay = document.getElementById("borrower-name-display");
    dom.activeLoanCountDisplay = document.getElementById("active-loan-count-display");
    dom.borrowedLoansGrid = document.getElementById("borrowed-loans-grid");
    dom.borrowedFeedbackArea = document.getElementById("borrowed-feedback-area");

    // Modal elements
    dom.detailModal = document.getElementById("book-detail-modal");
    dom.modalCloseBtn = document.getElementById("modal-close-btn");
    dom.modalBody = document.getElementById("detail-modal-body");
  }

  function bindEvents() {
    // Autocomplete input listener
    dom.searchInput.addEventListener("input", handleSearchInput);
    dom.searchInput.addEventListener("keydown", handleAutocompleteKeydown);
    dom.searchClearBtn.addEventListener("click", handleClearSearch);
    dom.searchForm.addEventListener("submit", handleSearchSubmit);

    // Filter controls
    dom.deptFilter.addEventListener("change", (e) => {
      state.selectedDept = e.target.value;
      state.currentPage = 1;
      updateChipActiveState();
      loadCatalog();
    });

    dom.availFilter.addEventListener("change", (e) => {
      state.availabilityFilter = e.target.value;
      renderBookGrid();
    });

    // Pagination
    dom.prevPageBtn.addEventListener("click", () => {
      if (state.currentPage > 1) {
        state.currentPage--;
        loadCatalog();
      }
    });

    dom.nextPageBtn.addEventListener("click", () => {
      const maxPage = Math.ceil(state.totalMatches / state.pageSize) || 1;
      if (state.currentPage < maxPage) {
        state.currentPage++;
        loadCatalog();
      }
    });

    // Close autocomplete on click outside
    document.addEventListener("click", (e) => {
      if (!dom.acMenu.contains(e.target) && e.target !== dom.searchInput) {
        closeAutocomplete();
      }
    });

    // Modal close events
    dom.modalCloseBtn.addEventListener("click", closeDetailModal);
    dom.detailModal.addEventListener("click", (e) => {
      if (e.target === dom.detailModal) closeDetailModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeDetailModal();
        closeAutocomplete();
      }
    });

    // View Navigation
    if (dom.navCatalogBtn) {
      dom.navCatalogBtn.addEventListener("click", (e) => {
        e.preventDefault();
        switchView("catalog");
      });
    }
    if (dom.navBorrowedBtn) {
      dom.navBorrowedBtn.addEventListener("click", (e) => {
        e.preventDefault();
        switchView("borrowed");
      });
    }

    // Student identity selector
    if (dom.studentSelect) {
      dom.studentSelect.addEventListener("change", (e) => {
        setStudentId(e.target.value);
      });
    }

    // Hash navigation
    window.addEventListener("hashchange", () => {
      const hash = window.location.hash;
      if (hash === "#borrowed-section") {
        switchView("borrowed");
      } else if (hash === "#catalog-section") {
        switchView("catalog");
      }
    });
  }

  // --- Academic Calendar Integration ---
  async function loadUpcomingCalendar() {
    try {
      const res = await fetch(`${API_BASE}/calendar/upcoming`);
      if (!res.ok) return;
      const data = await res.json();
      state.upcomingExam = data;

      if (data.status === "IN_PROGRESS") {
        dom.examName.textContent = `Active Exam Period: ${data.exam_type}`;
        dom.examDates.textContent = `Official window: ${data.start_date} to ${data.end_date} (Asia/Kolkata). Peak borrowing surge active.`;
        dom.examBadge.className = "badge badge-critical";
        dom.examBadge.textContent = "Exam In Progress";
      } else if (data.status === "UPCOMING") {
        const sem = data.entry ? `${data.entry.semester} ${data.entry.academic_year}` : "";
        dom.examName.textContent = `Upcoming Examination: ${data.exam_type} ${sem ? '(' + sem + ')' : ''}`;
        dom.examDates.textContent = `Scheduled from ${data.start_date} to ${data.end_date} (Asia/Kolkata).`;
        dom.examBadge.className = "badge badge-info";
        dom.examBadge.textContent = `⏳ ${data.days_remaining} Days Remaining`;
      } else {
        dom.examName.textContent = "Standard Academic Period";
        dom.examDates.textContent = "No university exams currently scheduled. Standard library borrowing active.";
        dom.examBadge.className = "badge badge-safe";
        dom.examBadge.textContent = "Normal Circulation";
      }
    } catch (err) {
      console.warn("Unable to load academic calendar:", err);
    }
  }

  // --- Department Categories ---
  async function loadDepartments() {
    try {
      const res = await fetch(`${API_BASE}/departments`);
      if (!res.ok) return;
      const data = await res.json();
      state.departments = data.departments || [];
      
      // Populate select dropdown
      dom.deptFilter.innerHTML = '<option value="">All Departments</option>' +
        state.departments.map(d => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`).join("");

      // Populate quick chips
      dom.categoryChips.innerHTML = `
        <button type="button" class="chip active" data-dept="">All Subjects</button>
        ${state.departments.map(d => `<button type="button" class="chip" data-dept="${escapeHtml(d)}">${escapeHtml(d)}</button>`).join("")}
      `;

      dom.categoryChips.querySelectorAll(".chip").forEach(btn => {
        btn.addEventListener("click", () => {
          const dept = btn.getAttribute("data-dept");
          state.selectedDept = dept;
          dom.deptFilter.value = dept;
          state.currentPage = 1;
          updateChipActiveState();
          loadCatalog();
        });
      });
    } catch (err) {
      console.warn("Failed to load departments:", err);
    }
  }

  function updateChipActiveState() {
    dom.categoryChips.querySelectorAll(".chip").forEach(btn => {
      const dept = btn.getAttribute("data-dept");
      btn.classList.toggle("active", dept === state.selectedDept);
    });
  }

  // --- Autocomplete Handling ---
  function handleSearchInput(e) {
    const val = e.target.value.trim();
    state.searchQuery = val;
    dom.searchClearBtn.style.display = val ? "block" : "none";

    clearTimeout(state.debounceTimer);
    if (val.length < 2) {
      closeAutocomplete();
      return;
    }

    state.debounceTimer = setTimeout(() => {
      fetchAutocompleteSuggestions(val);
    }, 250);
  }

  async function fetchAutocompleteSuggestions(q) {
    try {
      let url = `${API_BASE}/books?q=${encodeURIComponent(q)}&limit=8`;
      if (state.selectedDept) url += `&department=${encodeURIComponent(state.selectedDept)}`;
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      renderAutocomplete(data.books || []);
    } catch (err) {
      console.error("Autocomplete fetch error:", err);
    }
  }

  function renderAutocomplete(books) {
    if (!books || books.length === 0) {
      dom.acMenu.innerHTML = `<div style="padding: 12px 16px; color: var(--text-muted); font-size: 0.85rem;">No library catalog matches found for this query.</div>`;
      dom.acMenu.classList.add("open");
      state.acSelectedIndex = -1;
      return;
    }

    dom.acMenu.innerHTML = books.map((b, idx) => {
      let badgeClass = "badge-safe";
      let badgeText = `${b.available_copies} available`;
      if (b.available_copies === 0) {
        badgeClass = "badge-critical";
        badgeText = "Checked Out";
      } else if (b.available_copies <= 2) {
        badgeClass = "badge-moderate";
        badgeText = `${b.available_copies} left`;
      }

      return `
        <div class="autocomplete-item" data-index="${idx}" data-book-id="${escapeHtml(b.book_id)}">
          <div class="ac-info">
            <div class="ac-title">${escapeHtml(b.title)}</div>
            <div class="ac-meta">${escapeHtml(b.author)} &bull; ${escapeHtml(b.department)}</div>
          </div>
          <span class="badge ${badgeClass} ac-badge">${badgeText}</span>
        </div>
      `;
    }).join("");

    dom.acMenu.classList.add("open");
    state.acSelectedIndex = -1;

    dom.acMenu.querySelectorAll(".autocomplete-item").forEach(item => {
      item.addEventListener("click", () => {
        const bookId = item.getAttribute("data-book-id");
        closeAutocomplete();
        openBookDetail(bookId);
      });
    });
  }

  function handleAutocompleteKeydown(e) {
    const items = dom.acMenu.querySelectorAll(".autocomplete-item");
    if (!items.length || !dom.acMenu.classList.contains("open")) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      state.acSelectedIndex = (state.acSelectedIndex + 1) % items.length;
      updateAcSelection(items);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      state.acSelectedIndex = (state.acSelectedIndex - 1 + items.length) % items.length;
      updateAcSelection(items);
    } else if (e.key === "Enter" && state.acSelectedIndex >= 0) {
      e.preventDefault();
      items[state.acSelectedIndex].click();
    }
  }

  function updateAcSelection(items) {
    items.forEach((item, idx) => {
      item.classList.toggle("selected", idx === state.acSelectedIndex);
    });
    if (state.acSelectedIndex >= 0 && items[state.acSelectedIndex]) {
      items[state.acSelectedIndex].scrollIntoView({ block: "nearest" });
    }
  }

  function closeAutocomplete() {
    dom.acMenu.classList.remove("open");
    state.acSelectedIndex = -1;
  }

  function handleClearSearch() {
    dom.searchInput.value = "";
    state.searchQuery = "";
    dom.searchClearBtn.style.display = "none";
    closeAutocomplete();
    state.currentPage = 1;
    loadCatalog();
  }

  function handleSearchSubmit(e) {
    e.preventDefault();
    closeAutocomplete();
    state.searchQuery = dom.searchInput.value.trim();
    state.currentPage = 1;
    loadCatalog();
  }

  // --- Catalog Loading & Rendering ---
  async function loadCatalog() {
    dom.bookGrid.innerHTML = `
      <div style="grid-column: 1 / -1; text-align: center; padding: 40px; color: var(--text-muted);">
        <div style="font-size: 1.5rem; margin-bottom: 8px;">📚</div>
        Loading library catalog holdings...
      </div>
    `;

    const offset = (state.currentPage - 1) * state.pageSize;
    let url = `${API_BASE}/books?limit=${state.pageSize}&offset=${offset}`;
    if (state.searchQuery) url += `&q=${encodeURIComponent(state.searchQuery)}`;
    if (state.selectedDept) url += `&department=${encodeURIComponent(state.selectedDept)}`;

    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      const data = await res.json();
      state.totalMatches = data.total_matches || 0;
      state.currentBooks = data.books || [];

      renderBookGrid();
      renderPagination();
    } catch (err) {
      dom.bookGrid.innerHTML = `
        <div class="alert alert-danger" style="grid-column: 1 / -1;">
          <strong>Error loading catalog:</strong> ${escapeHtml(err.message)}
        </div>
      `;
    }
  }

  function renderBookGrid() {
    let books = state.currentBooks;

    // Optional client-side availability filter
    if (state.availabilityFilter === "available") {
      books = books.filter(b => b.available_copies > 0);
    } else if (state.availabilityFilter === "low") {
      books = books.filter(b => b.available_copies <= 2);
    }

    // Update stats text
    const startIdx = state.totalMatches === 0 ? 0 : (state.currentPage - 1) * state.pageSize + 1;
    const endIdx = Math.min(state.currentPage * state.pageSize, state.totalMatches);
    dom.toolbarStats.textContent = state.totalMatches > 0
      ? `Showing ${startIdx}–${endIdx} of ${state.totalMatches} catalog titles`
      : "No matching titles found";

    if (!books.length) {
      dom.bookGrid.innerHTML = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 48px; background: white; border-radius: 8px; border: 1px solid var(--border-subtle);">
          <h3 style="color: var(--brand-navy); margin-bottom: 6px;">No books found</h3>
          <p style="color: var(--text-muted); font-size: 0.9rem;">
            No titles matched your search query. Try searching by partial title, author surname, or selecting "All Departments".
          </p>
        </div>
      `;
      return;
    }

    dom.bookGrid.innerHTML = books.map(b => {
      let badgeClass = "badge-safe";
      let statusText = "In Stock";
      if (b.available_copies === 0) {
        badgeClass = "badge-critical";
        statusText = "Checked Out";
      } else if (b.available_copies <= 2) {
        badgeClass = "badge-moderate";
        statusText = "Low Stock";
      }

      return `
        <article class="book-card" tabindex="0" data-book-id="${escapeHtml(b.book_id)}">
          <div class="book-dept-tag">${escapeHtml(b.department)}</div>
          <h3 class="book-title" title="${escapeHtml(b.title)}">${escapeHtml(b.title)}</h3>
          <div class="book-author">By ${escapeHtml(b.author)}</div>
          <div style="font-size: 0.78rem; color: var(--text-muted); margin-bottom: 8px;">
            ${escapeHtml(b.edition || 'Standard Edition')} &bull; Call: ${escapeHtml(b.book_id)}
          </div>
          <div class="book-copies-row">
            <div>
              <div class="copies-stat">
                <span>${b.available_copies}</span> / ${b.total_copies}
              </div>
              <div style="font-size: 0.72rem; color: var(--text-muted);">available copies</div>
            </div>
            <span class="badge ${badgeClass}">${statusText}</span>
          </div>
          <button class="btn btn-primary btn-sm" style="margin-top: 14px; width: 100%;" onclick="event.stopPropagation(); window.openBookDetailModal('${escapeHtml(b.book_id)}')">
            Check Availability & Forecast
          </button>
        </article>
      `;
    }).join("");

    // Clicking a card opens detail view
    dom.bookGrid.querySelectorAll(".book-card").forEach(card => {
      card.addEventListener("click", () => {
        const bookId = card.getAttribute("data-book-id");
        openBookDetail(bookId);
      });
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          const bookId = card.getAttribute("data-book-id");
          openBookDetail(bookId);
        }
      });
    });
  }

  function renderPagination() {
    const maxPage = Math.ceil(state.totalMatches / state.pageSize) || 1;
    dom.prevPageBtn.disabled = state.currentPage <= 1;
    dom.nextPageBtn.disabled = state.currentPage >= maxPage;
    dom.pageIndicator.textContent = `Page ${state.currentPage} of ${maxPage}`;
  }

  // --- Book Details & Availability Modal ---
  async function openBookDetail(bookId) {
    state.selectedBookId = bookId;
    dom.detailModal.classList.add("active");
    dom.modalBody.innerHTML = `
      <div style="text-align: center; padding: 40px; color: var(--text-muted);">
        <div style="font-size: 1.5rem; margin-bottom: 8px;">⏳</div>
        Retrieving live physical inventory & evaluating 7-day demand trajectory...
      </div>
    `;

    try {
      const res = await fetch(`${API_BASE}/predict/${encodeURIComponent(bookId)}`);
      if (!res.ok) throw new Error(`Could not load details for ${bookId}`);
      const data = await res.json();
      renderBookDetailModal(data);
    } catch (err) {
      dom.modalBody.innerHTML = `
        <div class="alert alert-danger">
          <strong>Error loading book availability:</strong> ${escapeHtml(err.message)}
        </div>
      `;
    }
  }

  // Expose global modal opener for inline buttons
  window.openBookDetailModal = openBookDetail;

  function renderBookDetailModal(d) {
    const inv = d.inventory || {};
    const dem = d.demand_forecast || d.ml_prediction || {};
    const risk = d.stockout_assessment || {};
    const cal = d.academic_calendar || {};
    const mv = d.recent_movement || {};

    let badgeClass = "badge-safe";
    let alertClass = "alert-success";
    if (risk.risk_level === "CRITICAL") {
      badgeClass = "badge-critical";
      alertClass = "alert-danger";
    } else if (risk.risk_level === "HIGH") {
      badgeClass = "badge-high";
      alertClass = "alert-warning";
    } else if (risk.risk_level === "MODERATE") {
      badgeClass = "badge-moderate";
      alertClass = "alert-warning";
    }

    const c1d = mv.checkouts_last_1d ?? 0;
    const c3d = mv.checkouts_last_3d ?? 0;
    const c7d = mv.checkouts_last_7d ?? 0;
    const r7d = mv.returns_last_7d ?? 0;

    dom.modalBody.innerHTML = `
      <div class="detail-meta-header">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;">
          <div>
            <div class="detail-dept">${escapeHtml(d.department)} &bull; ${escapeHtml(d.edition || 'Standard Edition')}</div>
            <h2 class="detail-title">${escapeHtml(d.title)}</h2>
            <div class="detail-author">By <strong>${escapeHtml(d.author)}</strong> &bull; Catalog Call Number: <span style="font-family: var(--font-mono);">${escapeHtml(d.book_id)}</span></div>
          </div>
          <span class="badge ${badgeClass}" style="font-size: 0.82rem; padding: 6px 12px;">${escapeHtml(risk.risk_level)} SHORTAGE RISK</span>
        </div>
      </div>

      <!-- Shortage Warning Assessment -->
      <div class="alert ${alertClass}">
        <div>
          <strong>Availability Assessment:</strong> ${escapeHtml(risk.warning_message)}
          <div style="font-size: 0.82rem; margin-top: 4px; opacity: 0.95;">
            ${escapeHtml(risk.explanation)}
          </div>
        </div>
      </div>

      <!-- Distinct 3-Tier Grid -->
      <div class="forecast-tiers-grid">
        <!-- Tier 1: Deterministic Physical Inventory -->
        <div class="tier-card" style="border-top: 3px solid var(--brand-primary);">
          <div class="tier-label">1. Deterministic Inventory</div>
          <div class="tier-val" style="color: ${inv.available_copies <= 2 ? 'var(--status-critical)' : 'var(--brand-primary)'};">
            ${inv.available_copies} <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-muted);">/ ${inv.total_copies} total</span>
          </div>
          <div class="tier-sub">
            <strong>${inv.checked_out_copies}</strong> on active loan<br>
            Strictly <strong>First-Come, First-Served</strong>.
          </div>
        </div>

        <!-- Tier 2: Future Demand Forecast (7 Days) -->
        <div class="tier-card" style="border-top: 3px solid var(--brand-accent);">
          <div class="tier-label">2. Projected 7-Day Demand</div>
          <div class="tier-val">
            ${(dem.predicted_next_7d_demand ?? dem.predicted_next_7d_checkouts ?? 0).toFixed(2)}
            <span style="font-size: 0.85rem; font-weight: normal; color: var(--text-muted);">loans</span>
          </div>
          <div class="tier-sub">
            Burn rate: <strong>${(dem.daily_burn_rate ?? 0).toFixed(2)} copies/day</strong><br>
            Model: ${escapeHtml(dem.forecast_origin || 'Velocity Weighted Run-Rate')}
          </div>
        </div>

        <!-- Tier 3: Projected Availability Window -->
        <div class="tier-card" style="border-top: 3px solid ${risk.estimated_days_to_stockout !== null ? 'var(--status-high)' : 'var(--status-safe)'};">
          <div class="tier-label">3. Stockout Horizon</div>
          <div class="tier-val" style="font-size: 1.25rem;">
            ${risk.estimated_days_to_stockout !== null 
              ? `${risk.estimated_days_to_stockout} days remaining` 
              : 'Safe Buffer'}
          </div>
          <div class="tier-sub">
            ${risk.estimated_stockout_date 
              ? `Est. Date: <strong>${risk.estimated_stockout_date}</strong>` 
              : 'Physical copies exceed 14-day demand window.'}
          </div>
        </div>
      </div>

      <!-- Recent Movement Activity Box -->
      <div class="movement-details-box">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
          <div style="font-size: 0.78rem; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.4px;">
            Recent Circulation Movement (Asia/Kolkata)
          </div>
          <div style="font-size: 0.75rem; color: var(--text-muted);">
            Latest Transaction: <strong>${d.metadata?.latest_transaction_timestamp ? new Date(d.metadata.latest_transaction_timestamp).toLocaleDateString() : 'Baseline Period'}</strong>
          </div>
        </div>
        <div class="movement-row">
          <div class="m-pill">
            <div class="m-val">${c1d}</div>
            <div class="m-lbl">Past 24h</div>
          </div>
          <div class="m-pill">
            <div class="m-val">${c3d}</div>
            <div class="m-lbl">Past 3 Days</div>
          </div>
          <div class="m-pill">
            <div class="m-val">${c7d}</div>
            <div class="m-lbl">Past 7 Days</div>
          </div>
          <div class="m-pill">
            <div class="m-val" style="color: var(--status-safe);">${r7d}</div>
            <div class="m-lbl">7d Returns</div>
          </div>
        </div>
        <div style="font-size: 0.78rem; color: var(--text-secondary); margin-top: 8px;">
          ${escapeHtml(mv.activity_impact_note || 'Standard baseline circulation.')}
        </div>
      </div>

      <!-- Academic Exam Context -->
      <div style="background-color: #eff6ff; border: 1px solid #bfdbfe; border-radius: var(--radius-sm); padding: 10px 14px; font-size: 0.82rem; color: #1e40af; margin-bottom: 14px;">
        <strong>Academic Exam Context:</strong> ${escapeHtml(cal.countdown_label || 'Standard circulation')} &bull; ${escapeHtml(cal.historical_cycle_note || '')}
      </div>

      <!-- Student Borrowing Action Panel -->
      <div class="borrow-action-panel">
        <div class="borrow-panel-header">
          <div class="borrow-panel-title">Borrow Book (Circulation Desk)</div>
          <span style="font-size: 0.78rem; color: var(--text-muted);">
            Active Borrower: <strong>${escapeHtml(state.studentProfiles[state.currentStudentId]?.name || state.currentStudentId)} (${escapeHtml(state.currentStudentId)})</strong>
          </span>
        </div>
        <div class="borrow-meta-note">
          <strong>Borrowing Policy:</strong> 14-day academic loan period. Issued on a strict <strong>First-Come, First-Served (FCFS)</strong> basis. Real-time checkout updates physical availability immediately.
        </div>
        <div id="modal-borrow-feedback"></div>
        <div class="borrow-btn-container">
          ${inv.available_copies > 0 ? `
            <button
              type="button"
              class="btn btn-primary"
              id="btn-modal-borrow-action"
              onclick="window.borrowStudentBook('${escapeHtml(d.book_id)}', '${escapeHtml(d.title)}')"
            >
              📘 Borrow Book (14-Day Loan)
            </button>
          ` : `
            <button type="button" class="btn btn-secondary" disabled style="opacity: 0.65; cursor: not-allowed;">
              ❌ Out of Stock (0 Copies Available)
            </button>
            <span style="font-size: 0.8rem; color: var(--status-critical);">All physical copies currently on loan. Check stockout horizon for estimated returns.</span>
          `}
        </div>
      </div>

      <!-- Model Transparency Disclaimer -->
      <div class="model-transparency-note">
        <strong>About this forecast:</strong> Predictions use an academic multiple linear regression baseline combined with observed borrowing velocity and authoritative university exam calendars. Predictions are an advisory study aid, not a guaranteed reservation. Borrowing remains strictly First-Come, First-Served (FCFS).
      </div>
    `;
  }

  // --- Student Borrowing & Return Workflows ---
  function switchView(viewName) {
    state.currentView = viewName;
    if (viewName === "borrowed") {
      if (dom.catalogViewWrapper) dom.catalogViewWrapper.style.display = "none";
      if (dom.borrowedViewSection) dom.borrowedViewSection.style.display = "block";
      if (dom.navCatalogBtn) dom.navCatalogBtn.classList.remove("active");
      if (dom.navBorrowedBtn) dom.navBorrowedBtn.classList.add("active");
      loadActiveLoans();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } else {
      if (dom.catalogViewWrapper) dom.catalogViewWrapper.style.display = "block";
      if (dom.borrowedViewSection) dom.borrowedViewSection.style.display = "none";
      if (dom.navCatalogBtn) dom.navCatalogBtn.classList.add("active");
      if (dom.navBorrowedBtn) dom.navBorrowedBtn.classList.remove("active");
    }
  }

  function setStudentId(studentId) {
    state.currentStudentId = studentId;
    localStorage.setItem("apex_student_id", studentId);
    syncStudentProfile();
    loadActiveLoans();
    // If detail modal is currently open, refresh to show new borrower ID
    if (state.selectedBookId) {
      openBookDetail(state.selectedBookId);
    }
  }

  function syncStudentProfile() {
    if (dom.studentSelect) dom.studentSelect.value = state.currentStudentId;
    const prof = state.studentProfiles[state.currentStudentId] || { name: "Student", dept: "General" };
    if (dom.borrowerNameDisplay) {
      dom.borrowerNameDisplay.textContent = `${prof.name} (${state.currentStudentId} - ${prof.dept})`;
    }
  }

  async function loadActiveLoans() {
    try {
      const res = await fetch(`${API_BASE}/loans?student_id=${encodeURIComponent(state.currentStudentId)}&status=active`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.activeLoans = data.loans || [];

      // Update pills and stats
      if (dom.borrowedCountPill) {
        dom.borrowedCountPill.textContent = state.activeLoans.length;
        dom.borrowedCountPill.style.display = state.activeLoans.length > 0 ? "inline-block" : "none";
      }
      if (dom.activeLoanCountDisplay) {
        dom.activeLoanCountDisplay.textContent = state.activeLoans.length;
      }
      renderBorrowedLoans();
    } catch (err) {
      if (dom.borrowedLoansGrid) {
        dom.borrowedLoansGrid.innerHTML = `
          <div class="alert alert-danger" style="grid-column: 1 / -1;">
            <strong>Error loading loans:</strong> ${escapeHtml(err.message)}
          </div>
        `;
      }
    }
  }

  function renderBorrowedLoans() {
    if (!dom.borrowedLoansGrid) return;
    const loans = state.activeLoans;
    if (!loans.length) {
      dom.borrowedLoansGrid.innerHTML = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 48px; background: white; border-radius: 8px; border: 1px solid var(--border-subtle);">
          <div style="font-size: 2.2rem; margin-bottom: 10px;">📚</div>
          <h3 style="color: var(--brand-navy); margin-bottom: 6px;">No Active Borrowed Books</h3>
          <p style="color: var(--text-muted); font-size: 0.9rem; max-width: 500px; margin: 0 auto 16px auto;">
            You currently have zero active library loans. Search the catalog and borrow books under the First-Come, First-Served policy.
          </p>
          <button type="button" class="btn btn-primary" onclick="window.switchStudentView('catalog')">
            Browse Library Catalog
          </button>
        </div>
      `;
      return;
    }

    dom.borrowedLoansGrid.innerHTML = loans.map(l => {
      const checkoutDateStr = l.checkout_date ? new Date(l.checkout_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : 'N/A';
      const dueDateStr = l.due_date ? new Date(l.due_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : 'N/A';
      const isOverdue = l.is_overdue || (l.days_remaining < 0);
      const statusBadge = isOverdue
        ? `<span class="badge badge-critical">Overdue (${Math.abs(l.days_remaining)}d)</span>`
        : `<span class="badge badge-safe">${l.days_remaining} Days Remaining</span>`;

      return `
        <article class="loan-card" data-loan-id="${escapeHtml(l.loan_id)}">
          <div class="loan-card-dept">${escapeHtml(l.department || 'General')} &bull; Call: ${escapeHtml(l.book_id)}</div>
          <h3 class="loan-card-title">${escapeHtml(l.title)}</h3>
          <div class="loan-card-author">By ${escapeHtml(l.author)}</div>
          
          <div class="loan-meta-box">
            <div class="loan-meta-item">
              <strong>Borrowed On</strong>
              <span>${checkoutDateStr}</span>
            </div>
            <div class="loan-meta-item">
              <strong>Due Date</strong>
              <span>${dueDateStr}</span>
            </div>
            <div class="loan-meta-item">
              <strong>Loan Reference</strong>
              <span style="font-family: var(--font-mono); font-size: 0.76rem;">${escapeHtml(l.loan_id)}</span>
            </div>
            <div class="loan-meta-item">
              <strong>Loan Status</strong>
              <span>${statusBadge}</span>
            </div>
          </div>

          <div class="loan-actions-row">
            <button
              type="button"
              class="btn btn-secondary btn-sm"
              id="btn-ret-${escapeHtml(l.loan_id)}"
              onclick="window.returnStudentLoan('${escapeHtml(l.loan_id)}', '${escapeHtml(l.book_id)}', '${escapeHtml(l.title)}')"
            >
              🔄 Return Book
            </button>
            <button
              type="button"
              class="btn btn-outline btn-sm"
              onclick="window.openBookDetailModal('${escapeHtml(l.book_id)}')"
              style="padding: 4px 10px; font-size: 0.78rem;"
            >
              View Forecast
            </button>
          </div>
        </article>
      `;
    }).join("");
  }

  async function returnStudentLoan(loanId, bookId, bookTitle) {
    if (!confirm(`Return "${bookTitle}" back to the central library shelf? Physical availability will immediately increase.`)) {
      return;
    }

    const btn = document.getElementById(`btn-ret-${loanId}`);
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Returning...";
    }

    try {
      const res = await fetch(`${API_BASE}/loans/return`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          loan_id: loanId,
          book_id: bookId,
          student_id: state.currentStudentId,
          notes: "Student portal return"
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "Return rejected by server.");
      }

      showBorrowedFeedback("success", `<strong>Return Confirmed:</strong> "${bookTitle}" has been returned. Shelf inventory restored to ${data.data?.available_copies_after ?? 'updated'} copies.`);
      
      // Refresh state
      await loadActiveLoans();
      await loadCatalog();
      if (state.selectedBookId === bookId) {
        await openBookDetail(bookId);
      }
    } catch (err) {
      showBorrowedFeedback("danger", `<strong>Return Failed:</strong> ${err.message}`);
      if (btn) {
        btn.disabled = false;
        btn.textContent = "🔄 Return Book";
      }
    }
  }

  async function borrowStudentBook(bookId, bookTitle) {
    const btn = document.getElementById("btn-modal-borrow-action");
    const feedback = document.getElementById("modal-borrow-feedback");

    if (btn) {
      btn.disabled = true;
      btn.textContent = "Processing Loan...";
    }
    if (feedback) feedback.innerHTML = "";

    try {
      const res = await fetch(`${API_BASE}/loans/borrow`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          book_id: bookId,
          student_id: state.currentStudentId,
          notes: "Student portal checkout"
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "Borrowing request rejected.");
      }

      const tx = data.data || {};
      const dueDateStr = tx.due_date ? new Date(tx.due_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '14 days';

      if (feedback) {
        feedback.innerHTML = `
          <div class="alert alert-success" style="margin-bottom: 12px;">
            <strong>Loan Confirmed!</strong> Successfully borrowed "${escapeHtml(bookTitle)}".<br>
            <span style="font-size: 0.82rem;">Loan Reference: <code>${escapeHtml(tx.loan_id || tx.transaction_id)}</code> &bull; Due Date: <strong>${dueDateStr}</strong> (14 Days).</span>
          </div>
        `;
      }

      // Refresh active loans count and catalog in background
      await loadActiveLoans();
      await loadCatalog();

      // Refresh modal details after 1.2s to show updated tiers and movement
      setTimeout(async () => {
        if (state.selectedBookId === bookId) {
          await openBookDetail(bookId);
        }
      }, 1200);

    } catch (err) {
      if (feedback) {
        feedback.innerHTML = `
          <div class="alert alert-danger" style="margin-bottom: 12px;">
            <strong>Borrowing Failed:</strong> ${escapeHtml(err.message)}
          </div>
        `;
      }
      if (btn) {
        btn.disabled = false;
        btn.textContent = "📘 Borrow Book (14-Day Loan)";
      }
    }
  }

  function showBorrowedFeedback(type, messageHtml) {
    if (!dom.borrowedFeedbackArea) return;
    dom.borrowedFeedbackArea.innerHTML = `
      <div class="alert alert-${type}" style="margin-bottom: 16px;">
        ${messageHtml}
      </div>
    `;
    setTimeout(() => {
      if (dom.borrowedFeedbackArea) dom.borrowedFeedbackArea.innerHTML = "";
    }, 8000);
  }

  // Global exports for inline button handlers
  window.switchStudentView = switchView;
  window.returnStudentLoan = returnStudentLoan;
  window.borrowStudentBook = borrowStudentBook;

  function closeDetailModal() {
    dom.detailModal.classList.remove("active");
    state.selectedBookId = null;
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

  // Initialize on DOM load
  window.addEventListener("DOMContentLoaded", init);
})();
