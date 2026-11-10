/**
 * SprintMatch AI - UI View & Interaction Controller
 * Manages navigation, presets, modals, employees directory, matrix, and backlog.
 */

      // ================= PRESETS CONFIGURATION =================
      const TASK_PRESETS = {
        'BACKEND-REFUND': {
          title: 'Build High-Throughput Idempotent Payment Refund API Engine',
          desc: 'Design and expose an asynchronous, idempotent REST endpoint using FastAPI and Python to process full and partial payment refunds. Persist transaction ledgers in PostgreSQL with atomic row-level locks, publish state-change events to Kafka, and implement Redis-backed idempotency caching.',
          hours: 16,
          complexity: 'high',
          priority: 'high'
        },
        'BACKEND-STREAM': {
          title: 'Real-Time Kafka Event Streaming Pipeline for Financial Ledgers',
          desc: 'Architect a distributed event consumer in Python and FastAPI to ingest high-volume transaction events from Apache Kafka. Ensure exactly-once processing semantics, update balance ledgers in PostgreSQL, and cache hot account balances in Redis with sub-millisecond read latency.',
          hours: 20,
          complexity: 'critical',
          priority: 'urgent'
        },
        'BACKEND-WEBHOOK': {
          title: 'Asynchronous Webhook Dispatcher for Third-Party ERP Synchronization',
          desc: 'Implement a resilient, asynchronous webhook delivery engine using FastAPI, Redis queueing, and Python. Features exponential backoff retries, dead-letter auditing in PostgreSQL, HMAC-SHA256 payload signatures, and circuit breakers for external banking endpoints.',
          hours: 16,
          complexity: 'high',
          priority: 'high'
        },
        'FRONTEND-ANALYTICS': {
          title: 'Build Real-Time Financial Analytics Dashboard & Visualizations',
          desc: 'Develop a responsive enterprise analytics dashboard using React, Next.js, and TypeScript. Implement interactive transaction volume charts, failure rate heatmaps, Tailwind CSS styling, dark mode support, and WebSocket integration for real-time live metrics streaming.',
          hours: 12,
          complexity: 'medium',
          priority: 'high'
        },
        'FRONTEND-CHECKOUT': {
          title: 'Design Modular Multi-Step Checkout Experience with State Machine',
          desc: 'Refactor the merchant checkout flow using Next.js, TypeScript, and React. Build a resilient client-side state machine with validation, accessible UI components with Tailwind CSS, cross-browser compatibility, and localized Arabic/English RTL layout support.',
          hours: 14,
          complexity: 'medium',
          priority: 'medium'
        },
        'MOBILE-BIOMETRIC': {
          title: 'Implement Merchant Biometric Authentication & Secure Storage',
          desc: 'Build a production-grade biometric authentication module (Face ID / Fingerprint) in Flutter using Dart. Integrate encrypted secure key storage, biometric session renegotiation, offline token caching, and robust error fallback flows for iOS and Android.',
          hours: 14,
          complexity: 'medium',
          priority: 'high'
        },
        'MOBILE-OFFLINE': {
          title: 'Offline-First POS Transaction Queue & Synchronization Engine',
          desc: 'Implement an offline-first transaction synchronization layer in Flutter using Dart. Queue merchant POS payments locally in an encrypted database during network loss, and synchronize idempotently with core backend APIs once connectivity is restored.',
          hours: 18,
          complexity: 'high',
          priority: 'urgent'
        },
        'DEVOPS-K8S': {
          title: 'Architect Active-Passive Multi-Region Kubernetes Failover with Terraform',
          desc: 'Implement automated active-passive cross-region cluster failover on AWS using Terraform and Kubernetes. Configure ingress controllers, automated DNS failover via Route 53, cert-manager SSL renewals, and zero-downtime blue/green deployment pipelines.',
          hours: 24,
          complexity: 'critical',
          priority: 'urgent'
        },
        'DEVOPS-CICD': {
          title: 'Hardened GitOps CI/CD Pipeline with Automated Security Scanning',
          desc: 'Construct an enterprise-grade GitHub Actions CI/CD deployment pipeline using Docker, Linux & Bash, and Kubernetes. Integrate automated container vulnerability scanning with Trivy, static code quality gates, and automated canary deployments.',
          hours: 12,
          complexity: 'medium',
          priority: 'high'
        },
        'ODOO-COMMISSION': {
          title: 'Develop Custom Odoo 17 Sales Commission & Payroll Tier Module',
          desc: 'Build a custom Odoo 17 ERP enterprise module in Python with PostgreSQL optimization. Create tiered automated commission calculation engines linked to sales invoices, manager approval workflows, automated journal entries, and custom QWeb PDF reporting.',
          hours: 20,
          complexity: 'high',
          priority: 'high'
        },
        'ODOO-INVENTORY': {
          title: 'Automate Multi-Warehouse Inventory Reordering & Stock Synchronization',
          desc: 'Customize Odoo 17 inventory and procurement engine to automate replenishment rules across regional warehouses. Implement real-time stock reservations, PostgreSQL query optimization for large stock valuation tables, and ERP webhook listeners.',
          hours: 16,
          complexity: 'high',
          priority: 'medium'
        },
        'QA-PLAYWRIGHT': {
          title: 'Automated End-to-End Regression Test Suite with Playwright',
          desc: 'Develop a comprehensive, parallelized end-to-end regression automation suite using Playwright and Python. Cover critical merchant checkout flows, webhook deliveries, payment refunds, edge-case network latency simulations, and integrate into CI/CD pipelines.',
          hours: 16,
          complexity: 'medium',
          priority: 'high'
        },
        'QA-CYPRESS': {
          title: 'Component & Contract Testing Suite with Cypress for React SPA',
          desc: 'Author resilient component and integration tests in Cypress and TypeScript covering the React web app. Mock backend REST API responses, test responsive mobile breakpoints, and verify WCAG 2.1 AA accessibility standards in automated pull request checks.',
          hours: 10,
          complexity: 'low',
          priority: 'medium'
        },
        'AI-RAG': {
          title: 'Autonomous RAG Retrieval Agent for Merchant Policy Compliance',
          desc: 'Build an enterprise Retrieval-Augmented Generation (RAG) assistant using LangChain and Python. Ingest compliance documentation, generate vector embeddings, integrate semantic search with re-ranking, and implement prompt injection defenses for customer query handling.',
          hours: 18,
          complexity: 'high',
          priority: 'high'
        },
        'DATA-POSTGRES': {
          title: 'PostgreSQL Query Optimization, Indexing & Partitioning for Scale',
          desc: 'Analyze slow transaction query execution plans (EXPLAIN ANALYZE) on a 50M-row PostgreSQL ledger table. Design composite B-Tree and BRIN indexes, implement declarative table partitioning by month, and eliminate connection pool exhaustion under peak load.',
          hours: 10,
          complexity: 'high',
          priority: 'urgent'
        },
        'CUSTOM': {
          title: '',
          desc: '',
          hours: 10,
          complexity: 'medium',
          priority: 'medium'
        }
      };

      function onPresetChange() {
        const key = document.getElementById('taskPresetSelect').value;
        const preset = TASK_PRESETS[key] || TASK_PRESETS['CUSTOM'];

        const titleEl = document.getElementById('taskTitleInput');
        const descEl = document.getElementById('taskDescInput');
        const hoursEl = document.getElementById('taskHoursInput');
        const compEl = document.getElementById('taskComplexityInput');
        const prioEl = document.getElementById('taskPriorityInput');

        if (titleEl) titleEl.value = preset.title;
        if (descEl) descEl.value = preset.desc;
        if (hoursEl) hoursEl.value = preset.hours;
        if (compEl) compEl.value = preset.complexity;
        if (prioEl) prioEl.value = preset.priority;

        showToast(`Loaded ${key} template`, 'info');
      }

      function resetTaskForm() {
        const titleEl = document.getElementById('taskTitleInput');
        const descEl = document.getElementById('taskDescInput');
        const hoursEl = document.getElementById('taskHoursInput');
        const presetEl = document.getElementById('taskPresetSelect');

        if (titleEl) titleEl.value = '';
        if (descEl) descEl.value = '';
        if (hoursEl) hoursEl.value = 10;
        if (presetEl) presetEl.value = 'CUSTOM';

        unassignCurrentDeveloper();
        showToast('Form cleared. Ready for new task.', 'info');
      }

      // ================= MANUAL ASSIGN MODAL CONTROLLER =================
      function openManualAssignModal() {
        toggleModal('manualAssignModal');
        renderManualAssignList(currentEmployees);
      }

      function filterManualAssignList() {
        const q = (document.getElementById('manualEmpSearch')?.value || '').toLowerCase().trim();
        if (!q) {
          renderManualAssignList(currentEmployees);
          return;
        }
        const filtered = currentEmployees.filter(e => {
          const name = (e.full_name || e.name || '').toLowerCase();
          const role = (e.job_title || '').toLowerCase();
          const dept = (e.department_name || e.department || '').toLowerCase();
          const code = (e.employee_code || '').toLowerCase();
          return name.includes(q) || role.includes(q) || dept.includes(q) || code.includes(q);
        });
        renderManualAssignList(filtered);
      }

      function renderManualAssignList(employees) {
        const container = document.getElementById('manualEmpList');
        const countEl = document.getElementById('manualEmpCount');
        if (countEl) countEl.innerText = `${employees.length} Developers`;
        if (!container) return;
        container.innerHTML = '';

        if (employees.length === 0) {
          container.innerHTML = '<div class="p-6 text-center text-slate-400 text-xs">No developers found matching filter.</div>';
          return;
        }

        employees.forEach(emp => {
          const name = emp.full_name || emp.name;
          const code = emp.employee_code || '';
          const job = emp.job_title || 'Software Engineer';
          const dept = emp.department_name || emp.department || 'Engineering';
          const initials = name.split(' ').filter(Boolean).map(n => n[0]).join('').substring(0, 2).toUpperCase();

          const avail = currentAvailabilityMap[emp.id] || currentAvailabilityMap[code] || {};
          const availHours = avail.available_capacity_hours !== undefined ? Number(avail.available_capacity_hours).toFixed(1) : (avail.available_working_hours !== undefined ? Number(avail.available_working_hours).toFixed(1) : '35.0');
          const isAvail = avail.status === 'AVAILABLE' || (avail.available_capacity_hours !== undefined && avail.available_capacity_hours > 5) || (avail.available_working_hours !== undefined && avail.available_working_hours > 5);
          const statusBadge = isAvail
            ? `<span class="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/60 border border-emerald-200 dark:border-emerald-800 px-2 py-0.5 rounded-full"><i class="fa-solid fa-circle text-[6px]"></i> Available (${availHours}h)</span>`
            : `<span class="inline-flex items-center gap-1 text-[10px] font-semibold text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/60 border border-amber-200 dark:border-amber-800 px-2 py-0.5 rounded-full"><i class="fa-solid fa-circle text-[6px]"></i> Busy (${availHours}h)</span>`;

          const card = document.createElement('div');
          card.className = 'p-3.5 rounded-xl bg-slate-50 dark:bg-surface-100 hover:bg-slate-100 dark:hover:bg-surface-200 border border-slate-200 dark:border-white/10 flex items-center justify-between gap-4 transition';
          card.innerHTML = `
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-odoo-primary/10 text-odoo-primary dark:text-pink-400 font-bold flex items-center justify-center text-xs font-mono shadow-2xs">
              ${initials}
            </div>
            <div>
              <div class="flex items-center gap-2">
                <span class="font-bold text-xs text-slate-900 dark:text-white">${name}</span>
                <span class="font-mono text-[10px] text-slate-400">${code}</span>
                ${statusBadge}
              </div>
              <p class="text-[11px] text-slate-500 mt-0.5">${job} · ${dept}</p>
            </div>
          </div>
          <button onclick="assignSpecificEmployee('${emp.id}', '${name.replace(/'/g, "\\'")}'); toggleModal('manualAssignModal');" class="px-3.5 py-1.5 bg-odoo-primary hover:bg-odoo-primary-dark text-white rounded-lg text-xs font-semibold shadow-xs transition flex items-center gap-1.5 shrink-0 cursor-pointer">
            <i class="fa-solid fa-user-check text-xs"></i>
            <span>Assign</span>
          </button>
        `;
          container.appendChild(card);
        });
      }

      function unassignCurrentDeveloper() {
        const banner = document.getElementById('assignmentStatusBanner');
        if (banner) banner.classList.add('hidden');
        showToast('Assignment cleared', 'info');
      }

      // Toast Notification helper
      function showToast(message, type = 'info') {
        const container = document.getElementById('toastContainer');
        const toast = document.createElement('div');
        toast.className = `p-3 rounded-lg shadow-lg text-xs font-medium pointer-events-auto flex items-center gap-2 transform transition-all duration-300 translate-y-2 opacity-0 text-white ${type === 'success' ? 'bg-emerald-600' :
            type === 'error' ? 'bg-rose-600' :
              type === 'warn' ? 'bg-amber-600' : 'bg-odoo-primary'
          }`;
        const icon = type === 'success' ? 'fa-circle-check' :
          type === 'error' ? 'fa-circle-exclamation' :
            type === 'warn' ? 'fa-triangle-exclamation' : 'fa-info-circle';
        toast.innerHTML = `<i class="fa-solid ${icon}"></i><span>${message}</span>`;
        container.appendChild(toast);
        setTimeout(() => toast.classList.remove('translate-y-2', 'opacity-0'), 10);
        setTimeout(() => {
          toast.classList.add('opacity-0', 'translate-y-2');
          setTimeout(() => toast.remove(), 300);
        }, 3500);
      }

      function toggleModal(id) {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('hidden');
      }

      function toggleTheme() {
        const html = document.documentElement;
        const isDark = html.classList.toggle('dark');
        localStorage.setItem('sprintmatch_theme', isDark ? 'dark' : 'light');
        const icon = document.getElementById('themeIcon');
        if (icon) {
          icon.className = isDark ? 'fa-solid fa-sun text-amber-300 text-xs' : 'fa-solid fa-moon text-xs';
        }
      }



      // ================= MAIN VIEW SWITCHER =================
      function switchMainView(viewName) {
        currentMainView = viewName;

        const formContainer = document.getElementById('viewFormContainer');
        const employeesContainer = document.getElementById('viewEmployeesContainer');

        [formContainer, employeesContainer].forEach(c => {
          if (c) c.classList.add('hidden');
        });

        // Reset top nav buttons
        const navBtns = [
          { id: 'topNavForm', view: 'form' },
          { id: 'topNavEmployees', view: 'employees' }
        ];

        navBtns.forEach(item => {
          const btn = document.getElementById(item.id);
          if (btn) {
            if (item.view === viewName) {
              btn.className = 'px-3.5 py-1.5 rounded-lg bg-white/20 text-white font-semibold transition flex items-center gap-2 shadow-2xs';
            } else {
              btn.className = 'px-3.5 py-1.5 rounded-lg hover:bg-white/10 text-white/80 hover:text-white transition flex items-center gap-2 font-medium';
            }
          }
        });

        if (viewName === 'employees') {
          if (employeesContainer) employeesContainer.classList.remove('hidden');
          loadDatabaseTables();
        } else {
          // default form
          if (formContainer) formContainer.classList.remove('hidden');
        }
      }

      function switchWorkspaceTab(tabKey) {
        const tabOverview = document.getElementById('tabContent-overview');
        const tabAlternatives = document.getElementById('tabContent-alternatives');
        const tabDisqualified = document.getElementById('tabContent-disqualified');
        const tabSkills = document.getElementById('tabContent-skills');

        [tabOverview, tabAlternatives, tabDisqualified, tabSkills].forEach(t => {
          if (t) t.classList.add('hidden');
        });

        const buttons = [
          { id: 'tabBtn-overview', target: tabOverview, key: 'overview' },
          { id: 'tabBtn-alternatives', target: tabAlternatives, key: 'alternatives' },
          { id: 'tabBtn-disqualified', target: tabDisqualified, key: 'disqualified' },
          { id: 'tabBtn-skills', target: tabSkills, key: 'skills' }
        ];

        buttons.forEach(b => {
          const btn = document.getElementById(b.id);
          if (b.key === tabKey) {
            if (b.target) b.target.classList.remove('hidden');
            if (btn) btn.className = 'tab-btn pb-3 border-b-2 border-odoo-primary text-odoo-primary dark:text-pink-400 font-semibold transition flex items-center gap-2 shrink-0';
          } else {
            if (btn) btn.className = 'tab-btn pb-3 border-b-2 border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-white font-medium transition flex items-center gap-2 shrink-0';
          }
        });
      }

      // ================= UNSEEN BENCHMARK DEMO SCENARIOS =================
      const UNSEEN_DEMO_SCENARIOS = {
        webhook: {
          code: 'TASK-DEMO-WBH',
          title: 'Build High-Throughput Webhook Delivery Service with HMAC Signatures & Exponential Backoff',
          description: 'As an infrastructure engineer, build an event-driven webhook dispatching microservice using Python and FastAPI. The service must enforce API Security by signing outbound payloads with SHA-256 HMAC tokens, log all delivery attempts to PostgreSQL, and handle HTTP 429 / 500 transient partner failures via Redis-backed exponential backoff with dead-letter queue (DLQ) alerts.',
          hours: 16.0
        }
      };

      function loadUnseenDemoScenario() {
        fillUnseenDemo('webhook');
        openNewTaskModal();
      }

      function fillUnseenDemo(scenarioKey = 'webhook') {
        const scenario = UNSEEN_DEMO_SCENARIOS[scenarioKey] || UNSEEN_DEMO_SCENARIOS.webhook;
        const titleInput = document.getElementById('newTaskTitle');
        const descInput = document.getElementById('newTaskDescription');
        if (titleInput) titleInput.value = scenario.title;
        if (descInput) descInput.value = scenario.description;
        setEffortHours(scenario.hours);

        const pmTitle = document.getElementById('pmTitle');
        const pmDesc = document.getElementById('pmDescription');
        const pmHours = document.getElementById('pmHours');
        if (pmTitle) pmTitle.value = scenario.title;
        if (pmDesc) pmDesc.value = scenario.description;
        if (pmHours) pmHours.value = scenario.hours;

        showToast('Loaded challenge task into creator', 'success');
      }



      function onQuickTaskDropdownChange() {
        const code = document.getElementById('quickTaskDropdown').value;
        if (code === 'UNSEEN_CHALLENGE') {
          loadUnseenDemoScenario();
        } else {
          loadScenario(code);
        }
      }

      function loadScenario(code) {
        const task = currentTasks.find(t => t.task_code === code);
        if (task) {
          renderTaskDetails(task);
        } else {
          document.getElementById('topTaskTitle').innerText = code;
          document.getElementById('sheetTaskTitle').innerText = code;
          document.getElementById('sheetTaskCode').innerText = code;
          document.getElementById('breadTaskCode').innerText = code;
        }
        const dropdown = document.getElementById('quickTaskDropdown');
        if (dropdown && dropdown.value !== code && code !== 'UNSEEN_CHALLENGE') {
          dropdown.value = code;
        }
      }

      function renderTaskDetails(task) {
        document.getElementById('sheetTaskCode').innerText = task.task_code;
        document.getElementById('sheetTaskTitle').innerText = task.title;
        document.getElementById('topTaskTitle').innerText = task.title;
        document.getElementById('breadTaskCode').innerText = task.task_code;
        document.getElementById('fieldHours').innerText = `${(task.estimated_hours || 16).toFixed(2)} Hours`;
        document.getElementById('fieldDeadline').innerText = (task.deadline || '2026-11-10').split('T')[0];
        document.getElementById('fieldProject').innerText = task.project_name || 'Core Engineering';

        const pmTitle = document.getElementById('pmTitle');
        const pmDesc = document.getElementById('pmDescription');
        const pmHours = document.getElementById('pmHours');
        if (pmTitle) pmTitle.value = task.title;
        if (pmDesc) pmDesc.value = task.description || task.title;
        if (pmHours) pmHours.value = task.estimated_hours || 16;

        const container = document.getElementById('customSkillsContainer');
        container.innerHTML = '';
        if (task.required_skills && task.required_skills.length > 0) {
          task.required_skills.forEach(s => {
            addSkillBadge(s.skill_name, s.minimum_level || 70);
          });
        } else {
          addSkillBadge('Python', 80);
          addSkillBadge('FastAPI', 75);
        }
      }

      function addSkillBadge(name, level) {
        const container = document.getElementById('customSkillsContainer');
        const chip = document.createElement('div');
        chip.className = 'inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-slate-100 dark:bg-surface-100 border border-slate-200 dark:border-white/10 text-xs font-medium mr-2 mb-2';
        chip.innerHTML = `
        <span class="text-slate-800 dark:text-slate-200 font-semibold">${name}</span>
        <span class="text-[10px] px-1.5 py-0.2 rounded bg-odoo-primary/10 text-odoo-primary dark:text-pink-300 font-mono font-bold">${level}%</span>
        <button onclick="this.parentElement.remove()" class="text-slate-400 hover:text-rose-500 ml-1">
          <i class="fa-solid fa-xmark text-[10px]"></i>
        </button>
      `;
        container.appendChild(chip);
      }

      function addCustomSkillRow() {
        const name = prompt('Skill Name (e.g., PostgreSQL, Redis, Docker, React):');
        if (name) {
          addSkillBadge(name.trim(), 75);
        }
      }

      // ================= RECOMMENDATION EXECUTION =================



      // ================= EDIT EMPLOYEE MODAL (DATABASE UPDATE) =================
      function openEditEmployeeModal(employeeId) {
        if (!employeeId) {
          showToast('Employee ID missing', 'error');
          return;
        }

        const emp = currentEmployees.find(e => e.id === employeeId);
        if (!emp) {
          showToast('Employee not found in records', 'error');
          return;
        }

        document.getElementById('editEmployeeId').value = emp.id;
        document.getElementById('editEmpCodeDisplay').innerText = emp.employee_code;
        document.getElementById('editEmpDeptDisplay').innerText = `${emp.department_name || 'Engineering'} · ${emp.team_name || 'Core Team'}`;
        document.getElementById('editEmpFullName').value = emp.full_name;
        document.getElementById('editEmpJobTitle').value = emp.job_title;
        document.getElementById('editEmpExperience').value = emp.experience_years !== undefined ? emp.experience_years : 5.0;
        document.getElementById('editEmpStatus').value = emp.employment_status || 'ACTIVE';
        document.getElementById('editEmpEmail').value = emp.email || '';

        const modal = document.getElementById('editEmployeeModal');
        if (modal) modal.classList.remove('hidden');
      }

      async function saveEmployeeEdits() {
        const empId = document.getElementById('editEmployeeId').value;
        const fullName = document.getElementById('editEmpFullName').value.trim();
        const jobTitle = document.getElementById('editEmpJobTitle').value.trim();
        const expYears = parseFloat(document.getElementById('editEmpExperience').value) || 0;
        const status = document.getElementById('editEmpStatus').value;
        const email = document.getElementById('editEmpEmail').value.trim();

        if (!fullName) {
          showToast('Employee name is required', 'warn');
          return;
        }
        if (!jobTitle) {
          showToast('Job title is required', 'warn');
          return;
        }

        const btn = document.getElementById('btnSaveEmployee');
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-xs"></i><span>Saving...</span>';

        const payload = {
          full_name: fullName,
          job_title: jobTitle,
          experience_years: expYears,
          employment_status: status,
          email: email || null
        };

        try {
          const res = await fetch(`${API_BASE}/api/v1/employees/${empId}`, {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              'X-API-Key': API_KEY
            },
            body: JSON.stringify(payload)
          });

          if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error?.message || `HTTP ${res.status}`);
          }

          const updated = await res.json();

          // Update local cache
          const idx = currentEmployees.findIndex(e => e.id === empId);
          if (idx !== -1) {
            currentEmployees[idx] = { ...currentEmployees[idx], ...updated };
          }

          // If currently inspected in 360 drawer, update drawer
          if (currentDrawerEmployeeId === empId) {
            document.getElementById('drawerName').innerText = updated.full_name;
            document.getElementById('drawerRole').innerText = `${updated.job_title} · ${updated.department_name} (${updated.team_name})`;
          }

          // If primary recommendation candidate, update hero
          if (currentPrimaryEmployeeId === empId) {
            document.getElementById('heroCandidateName').innerText = updated.full_name;
            document.getElementById('heroCandidateTitle').innerText = `${updated.job_title} · ${updated.department_name} (${updated.team_name})`;
          }

          toggleModal('editEmployeeModal');
          showToast('Employee updated successfully in database!', 'success');

          // Re-render views
          renderEmployeesApp();
          renderWorkforceMatrix();
        } catch (err) {
          showToast(`Failed to update employee: ${err.message}`, 'error');
        } finally {
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-solid fa-floppy-disk text-xs"></i><span>Save Changes to Database</span>';
        }
      }

      // ================= SCRUM BOARD STORAGE & EXPORT =================
      const STATUS_ORDER = ['BACKLOG', 'AI_MATCHED', 'IN_PROGRESS', 'REVIEW', 'DONE'];
      const STATUS_LABELS = {
        'BACKLOG': 'Backlog',
        'AI_MATCHED': 'AI Scored & Ready',
        'IN_PROGRESS': 'In Progress',
        'REVIEW': 'Review / QA',
        'DONE': 'Done'
      };

      function saveAssignedTasksToStorage() {
        try {
          localStorage.setItem('sprintmatch_assigned_tasks', JSON.stringify(assignedTasksList));
        } catch (e) { }
      }

      function loadAssignedTasksFromStorage() {
        try {
          const raw = localStorage.getItem('sprintmatch_assigned_tasks');
          if (raw) {
            const stored = JSON.parse(raw);
            if (Array.isArray(stored) && stored.length > 0) {
              stored.forEach(st => {
                if (!assignedTasksList.some(a => a.task_code === st.task_code || (a.title && a.title === st.title))) {
                  assignedTasksList.push(st);
                }
              });
            }
          }
        } catch (e) { }
      }

      // ================= RENDER BACKLOG TASKS LIST VIEW =================
      function renderTasksListView() { }
      function filterTasksListView() { }

      // ================= RENDER ODOO 17 EMPLOYEES DIRECTORY =================
      function renderEmployeesApp() {
        const grid = document.getElementById('employeesKanbanGrid');
        const tbody = document.getElementById('employeesTableBody');
        if (!grid || !tbody) return;

        grid.innerHTML = '';
        tbody.innerHTML = '';

        if (currentEmployees.length === 0) {
          grid.innerHTML = '<div class="col-span-4 p-12 text-center text-slate-400 text-xs">Loading employee directory...</div>';
          tbody.innerHTML = '<tr><td colspan="7" class="p-6 text-center text-slate-400">Loading employee directory...</td></tr>';
          return;
        }

        const q = (document.getElementById('employeeSearchInput')?.value || '').toLowerCase().trim();
        const statusFilter = document.getElementById('empStatusFilter')?.value || 'ALL';

        let count = 0;

        currentEmployees.forEach(e => {
          // Department Filter
          if (currentDeptFilter !== 'ALL') {
            if (currentDeptFilter === 'Mobile') {
              const isMobile = (e.team_name || '') === 'Mobile Apps' || (e.job_title || '').toLowerCase().includes('mobile') || (e.job_title || '').toLowerCase().includes('flutter');
              if (!isMobile) return;
            } else if (currentDeptFilter === 'Frontend') {
              const isFrontend = (e.department_name === 'Frontend & Mobile' && (e.team_name || '') !== 'Mobile Apps') || (e.job_title || '').toLowerCase().includes('frontend') || (e.job_title || '').toLowerCase().includes('react');
              if (!isFrontend) return;
            } else if (e.department_name !== currentDeptFilter) {
              return;
            }
          }

          // Live Availability from Map
          const avail = currentAvailabilityMap[e.id] || {};
          const status = avail.status || 'AVAILABLE';
          const freeCap = avail.available_capacity_hours !== undefined ? avail.available_capacity_hours : 35.0;

          // Status Filter
          if (statusFilter !== 'ALL' && status !== statusFilter) {
            return;
          }

          // Search text
          const searchBlob = `${e.full_name} ${e.employee_code} ${e.job_title} ${e.department_name} ${e.team_name}`.toLowerCase();
          if (q && !searchBlob.includes(q)) {
            return;
          }

          count++;

          const initials = (e.full_name || 'EM').split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();

          // 1. Kanban Card
          const card = document.createElement('div');
          card.className = 'o-sheet rounded-2xl p-4 sm:p-5 space-y-3.5 hover:shadow-md transition border border-slate-200 dark:border-white/10 flex flex-col justify-between';

          const statusColor = status === 'AVAILABLE' ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800' :
            status === 'PARTIALLY_AVAILABLE' ? 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 border-amber-200 dark:border-amber-800' :
              'bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border-rose-200 dark:border-rose-800';

          card.innerHTML = `
          <div>
            <div class="flex items-start justify-between gap-3">
              <div class="flex items-center gap-3">
                <div class="w-12 h-12 rounded-xl bg-odoo-primary text-white flex items-center justify-center font-bold text-sm font-mono shadow-xs">
                  ${initials}
                </div>
                <div>
                  <h4 class="font-bold text-slate-900 dark:text-white text-xs leading-tight">${e.full_name}</h4>
                  <span class="font-mono text-[10px] text-odoo-primary dark:text-pink-400 font-semibold">${e.employee_code}</span>
                  <div class="text-[11px] text-slate-500 font-medium">${e.job_title}</div>
                </div>
              </div>
            </div>

            <!-- Dept & Team badge -->
            <div class="mt-3 pt-2.5 border-t border-slate-100 dark:border-white/5 flex items-center justify-between text-[11px]">
              <span class="text-slate-600 dark:text-slate-400 truncate max-w-[150px]">${e.department_name || 'Engineering'}</span>
              <span class="text-[10px] font-mono text-slate-400">${e.experience_years ? e.experience_years + 'y exp' : ''}</span>
            </div>

            <!-- Live Status & Capacity -->
            <div class="mt-2 flex items-center justify-between gap-2">
              <span class="px-2.5 py-0.5 rounded-full text-[10px] font-semibold border ${statusColor} uppercase tracking-wider flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full ${status === 'AVAILABLE' ? 'bg-emerald-500' : status === 'PARTIALLY_AVAILABLE' ? 'bg-amber-500' : 'bg-rose-500'}"></span>
                <span>${status.replace('_', ' ')}</span>
              </span>
              <span class="text-[11px] font-mono text-slate-600 dark:text-slate-300 font-semibold">
                ${freeCap.toFixed(1)}h Free
              </span>
            </div>
          </div>

          <!-- Card Actions -->
          <div class="pt-3 border-t border-slate-100 dark:border-white/5 flex items-center gap-2">
            <button onclick="openEmployee360Modal('${e.id}')" class="flex-1 py-1.5 px-2 bg-slate-100 hover:bg-slate-200 dark:bg-surface-100 dark:hover:bg-surface-200 text-slate-700 dark:text-slate-200 rounded-lg text-[11px] font-semibold transition flex items-center justify-center gap-1">
              <i class="fa-solid fa-address-card text-xs"></i>
              <span>360°</span>
            </button>
            <button onclick="openEditEmployeeModal('${e.id}')" class="py-1.5 px-2.5 bg-slate-100 hover:bg-slate-200 dark:bg-surface-100 dark:hover:bg-surface-200 text-slate-700 dark:text-slate-200 rounded-lg text-[11px] font-semibold transition" title="Edit Employee in Database">
              <i class="fa-solid fa-pen-to-square text-xs text-odoo-primary"></i>
            </button>
            <button onclick="openNewTaskModal(); setAssignMode('manual'); document.getElementById('manualEmployeeSelect').value='${e.id}'; updateManualAssignButtonText();" class="py-1.5 px-2.5 bg-odoo-primary/10 hover:bg-odoo-primary hover:text-white text-odoo-primary dark:text-pink-300 rounded-lg text-[11px] font-semibold transition" title="Create task for this employee">
              <i class="fa-solid fa-plus text-xs"></i>
            </button>
          </div>
        `;
          grid.appendChild(card);

          // 2. Table Row
          const tr = document.createElement('tr');
          tr.className = 'hover:bg-slate-50 dark:hover:bg-surface-100 transition';
          tr.innerHTML = `
          <td class="px-4 py-3">
            <div class="flex items-center gap-2.5">
              <div class="w-8 h-8 rounded-lg bg-odoo-primary text-white flex items-center justify-center font-bold text-xs font-mono shadow-2xs">
                ${initials}
              </div>
              <div>
                <span class="font-bold text-slate-900 dark:text-white text-xs block">${e.full_name}</span>
                <span class="font-mono text-[10px] text-slate-400">${e.employee_code}</span>
              </div>
            </div>
          </td>
          <td class="px-4 py-3">
            <div class="font-medium text-slate-800 dark:text-slate-200">${e.job_title}</div>
            <div class="text-[10px] text-slate-400">${e.team_name || 'Team'}</div>
          </td>
          <td class="px-4 py-3 text-slate-600 dark:text-slate-300 font-medium">
            ${e.department_name}
          </td>
          <td class="px-4 py-3 font-mono text-slate-600 dark:text-slate-300">
            ${e.experience_years ? e.experience_years.toFixed(1) + ' yrs' : '--'}
          </td>
          <td class="px-4 py-3">
            <span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${statusColor}">
              ${status.replace('_', ' ')}
            </span>
          </td>
          <td class="px-4 py-3 font-mono font-semibold text-slate-800 dark:text-slate-200">
            ${(avail.committed_work_hours || 0).toFixed(1)}h / 35h
          </td>
          <td class="px-4 py-3 text-right">
            <div class="flex items-center justify-end gap-1.5">
              <button onclick="openEmployee360Modal('${e.id}')" class="px-2 py-1 text-xs bg-slate-100 hover:bg-slate-200 dark:bg-surface-200 text-slate-700 dark:text-slate-200 rounded-md border border-slate-200 dark:border-white/10 transition">
                360°
              </button>
              <button onclick="openEditEmployeeModal('${e.id}')" class="p-1 px-2 text-xs bg-slate-100 hover:bg-slate-200 dark:bg-surface-200 text-odoo-primary dark:text-pink-300 rounded-md border border-slate-200 dark:border-white/10 transition" title="Edit in Database">
                <i class="fa-solid fa-pen-to-square"></i>
              </button>
            </div>
          </td>
        `;
          tbody.appendChild(tr);
        });

        const dirCount = document.getElementById('empDirectoryCount');
        if (dirCount) dirCount.innerText = count;
      }

      function switchEmployeeSubView(mode) {
        currentEmpSubView = mode;
        const grid = document.getElementById('employeesKanbanGrid');
        const tableWrapper = document.getElementById('employeesTableWrapper');
        const btnKanban = document.getElementById('btnEmpKanban');
        const btnList = document.getElementById('btnEmpList');

        if (mode === 'kanban') {
          grid.classList.remove('hidden');
          tableWrapper.classList.add('hidden');
          btnKanban.className = 'px-3 py-1 rounded-md bg-white dark:bg-surface-200 text-odoo-primary dark:text-white font-semibold shadow-2xs transition flex items-center gap-1.5';
          btnList.className = 'px-3 py-1 rounded-md text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white transition flex items-center gap-1.5';
        } else {
          grid.classList.add('hidden');
          tableWrapper.classList.remove('hidden');
          btnList.className = 'px-3 py-1 rounded-md bg-white dark:bg-surface-200 text-odoo-primary dark:text-white font-semibold shadow-2xs transition flex items-center gap-1.5';
          btnKanban.className = 'px-3 py-1 rounded-md text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white transition flex items-center gap-1.5';
        }
      }

      function filterEmployeesByDept(deptName) {
        currentDeptFilter = deptName;
        document.querySelectorAll('.emp-dept-pill').forEach(pill => {
          const d = pill.getAttribute('data-dept');
          if (d === deptName) {
            pill.className = 'emp-dept-pill px-3.5 py-1 rounded-full bg-odoo-primary text-white font-semibold transition shadow-2xs';
          } else {
            pill.className = 'emp-dept-pill px-3.5 py-1 rounded-full bg-slate-100 dark:bg-surface-100 text-slate-700 dark:text-slate-300 hover:bg-slate-200 transition';
          }
        });
        renderEmployeesApp();
      }

      function filterEmployeesApp() {
        renderEmployeesApp();
      }

      function refreshAllEmployees() {
        showToast('Refreshing workforce data...', 'info');
        fetchEmployeesCache();
      }

      function renderWorkforceMatrix() { }
      function filterWorkforceMatrix() { }

      // ================= EMPLOYEE 360 DRAWER =================
      function openPrimaryEmployee360() {
        if (currentPrimaryEmployeeId) {
          openEmployee360Modal(currentPrimaryEmployeeId);
        } else {
          showToast('No candidate evaluated yet', 'info');
        }
      }

      async function openEmployee360Modal(employeeId) {
        if (!employeeId) {
          showToast('Employee ID unavailable', 'error');
          return;
        }

        currentDrawerEmployeeId = employeeId;
        const drawer = document.getElementById('employee360Drawer');
        const backdrop = document.getElementById('drawerBackdrop');
        const body = document.getElementById('drawerBody');

        backdrop.classList.remove('hidden');
        drawer.classList.remove('translate-x-full');
        body.innerHTML = `
        <div class="text-center py-16 text-slate-400 text-xs font-mono space-y-2">
          <i class="fa-solid fa-spinner fa-spin text-2xl text-odoo-primary"></i>
          <div>Loading authoritative 360° profile from Odoo HR...</div>
        </div>
      `;

        try {
          const asOf = document.getElementById('asOfInput').value.trim();
          const url = `${API_BASE}/api/v1/employees/${employeeId}/360?as_of=${encodeURIComponent(asOf || '2026-11-03T10:00:00+02:00')}`;
          const res = await fetch(url, { headers: { 'X-API-Key': API_KEY } });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const result = await res.json();
          renderEmployee360Details(result.data);
        } catch (err) {
          body.innerHTML = `<div class="p-6 text-center text-rose-500 text-xs">Failed to load profile: ${err.message}</div>`;
        }
      }

      function closeEmployee360Modal() {
        const drawer = document.getElementById('employee360Drawer');
        const backdrop = document.getElementById('drawerBackdrop');
        drawer.classList.add('translate-x-full');
        backdrop.classList.add('hidden');
      }

      function renderEmployee360Details(data) {
        if (!data || !data.profile) return;
        const emp = data.profile.employee || {};
        const skills = data.skill_assessments || [];
        const perf = data.performance || {};
        const avail = data.availability || {};
        const assignments = data.current_assignments || [];

        document.getElementById('drawerAvatar').innerText = (emp.full_name || 'EM').split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
        document.getElementById('drawerName').innerText = emp.full_name;
        document.getElementById('drawerRole').innerText = `${emp.job_title} · ${emp.department_name} (${emp.team_name})`;

        const body = document.getElementById('drawerBody');
        body.innerHTML = `
        <!-- Profile Card -->
        <div class="o-sheet rounded-xl p-5 space-y-3 shadow-xs">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase tracking-wider flex items-center gap-1.5">
              <i class="fa-solid fa-id-badge text-odoo-primary"></i>
              <span>Employment & HR Contract</span>
            </h4>
            <button onclick="openEditEmployeeModal('${emp.id}')" class="px-2.5 py-1 bg-slate-100 hover:bg-slate-200 dark:bg-surface-100 text-slate-700 dark:text-slate-300 rounded text-xs font-semibold transition flex items-center gap-1">
              <i class="fa-solid fa-pen-to-square text-xs text-odoo-primary"></i>
              <span>Edit Details</span>
            </button>
          </div>
          <div class="grid grid-cols-2 gap-3 text-xs">
            <div><span class="text-slate-400">Code:</span> <strong class="font-mono text-slate-700 dark:text-slate-300">${emp.employee_code}</strong></div>
            <div><span class="text-slate-400">Email:</span> <span class="text-slate-700 dark:text-slate-300 font-mono text-[11px]">${emp.email || '--'}</span></div>
            <div><span class="text-slate-400">Experience:</span> <span class="text-slate-700 dark:text-slate-300 font-medium">${emp.experience_years} Years</span></div>
            <div><span class="text-slate-400">Status:</span> <span class="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 text-[10px] font-semibold uppercase">${emp.employment_status || 'ACTIVE'}</span></div>
          </div>
        </div>

        <!-- Working Hours & Live Capacity -->
        <div class="o-sheet rounded-xl p-5 space-y-3 shadow-xs">
          <h4 class="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase tracking-wider flex items-center gap-1.5">
            <i class="fa-solid fa-clock text-odoo-teal"></i>
            <span>Working Hours & Capacity Availability</span>
          </h4>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center text-xs">
            <div class="bg-slate-50 dark:bg-surface-100 p-3 rounded-lg">
              <span class="text-[10px] text-slate-400 uppercase block">Scheduled</span>
              <strong class="font-mono text-sm text-slate-800 dark:text-slate-200">${(avail.scheduled_hours !== undefined ? avail.scheduled_hours : 35).toFixed(1)}h</strong>
            </div>
            <div class="bg-slate-50 dark:bg-surface-100 p-3 rounded-lg">
              <span class="text-[10px] text-slate-400 uppercase block">Committed</span>
              <strong class="font-mono text-sm text-amber-600 dark:text-amber-400">${Number(avail.committed_work_hours || 0).toFixed(1)}h</strong>
            </div>
            <div class="bg-slate-50 dark:bg-surface-100 p-3 rounded-lg">
              <span class="text-[10px] text-slate-400 uppercase block">Leaves</span>
              <strong class="font-mono text-sm text-slate-600 dark:text-slate-400">${Number(avail.leave_hours || 0).toFixed(1)}h</strong>
            </div>
            <div class="bg-teal-50 dark:bg-teal-950/40 p-3 rounded-lg border border-teal-200 dark:border-teal-800">
              <span class="text-[10px] text-teal-600 dark:text-teal-400 uppercase font-bold block">Available</span>
              <strong class="font-mono text-sm text-odoo-teal">${Number(avail.available_capacity_hours !== undefined ? avail.available_capacity_hours : 35).toFixed(1)}h</strong>
            </div>
          </div>
        </div>

        <!-- Verified Competencies (hr.employee.skill) -->
        <div class="o-sheet rounded-xl p-5 space-y-3 shadow-xs">
          <h4 class="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase tracking-wider flex items-center gap-1.5">
            <i class="fa-solid fa-graduation-cap text-indigo-500"></i>
            <span>Verified Skills & Proficiencies (${skills.length})</span>
          </h4>
          <div class="space-y-2.5">
            ${skills.map(s => {
          const pct = Math.round(s.proficiency_level ?? s.effective_level ?? s.skill_level ?? 80);
          const lvl = s.level_name || (pct >= 90 ? 'Expert' : pct >= 80 ? 'Proficient' : pct >= 70 ? 'Competent' : 'Developing');
          return `
                <div class="space-y-1">
                  <div class="flex items-center justify-between text-xs">
                    <span class="font-semibold text-slate-800 dark:text-slate-200">${s.skill_name}</span>
                    <span class="font-mono text-[11px] text-slate-500">${pct}% (Level ${lvl})</span>
                  </div>
                  <div class="w-full bg-slate-100 dark:bg-surface-200 h-1.5 rounded-full overflow-hidden">
                    <div class="bg-odoo-primary h-full rounded-full" style="width: ${pct}%"></div>
                  </div>
                </div>
              `;
        }).join('')}
          </div>
        </div>

        <!-- Active Tasks & Commitments -->
        <div class="o-sheet rounded-xl p-5 space-y-3 shadow-xs">
          <h4 class="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase tracking-wider flex items-center gap-1.5">
            <i class="fa-solid fa-tasks text-amber-500"></i>
            <span>Current Sprint Commitments (${assignments.length})</span>
          </h4>
          ${assignments.length === 0 ? '<p class="text-xs text-slate-400 italic">No conflicting tasks in the current window.</p>' : `
            <div class="space-y-2">
              ${assignments.map(a => `
                <div class="p-3 rounded-lg bg-slate-50 dark:bg-surface-100 border border-slate-200 dark:border-white/10 flex items-center justify-between text-xs">
                  <div>
                    <span class="font-mono font-bold text-odoo-primary dark:text-pink-400">${a.task_code || 'TASK'}</span>
                    <span class="text-slate-700 dark:text-slate-300 font-medium ml-2">${a.task_title || a.title || 'Active Sprint Task'}</span>
                  </div>
                  <span class="font-mono text-slate-500 font-semibold">${Number(a.remaining_estimated_hours ?? a.allocated_hours ?? 4).toFixed(1)}h</span>
                </div>
              `).join('')}
            </div>
          `}
        </div>
      `;
      }



      function handleOmniSearch() {
        const q = document.getElementById('odooOmniSearch').value.toLowerCase().trim();
        if (!q) {
          if (currentMainView === 'employees') {
            const input = document.getElementById('employeeSearchInput');
            if (input) { input.value = ''; filterEmployeesApp(); }
          }
          return;
        }

        if (currentMainView === 'employees') {
          const input = document.getElementById('employeeSearchInput');
          if (input) {
            input.value = q;
            filterEmployeesApp();
          }

        } else {
          const matched = currentTasks.find(t => (t.task_code && t.task_code.toLowerCase().includes(q)) || (t.title && t.title.toLowerCase().includes(q)));
          if (matched) {
            loadScenario(matched.task_code);
            showToast(`Loaded ${matched.task_code}: ${matched.title}`, 'info');
          }
        }
      }


