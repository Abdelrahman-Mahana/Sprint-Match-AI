/**
 * SprintMatch AI - API & Core State Module
 * Handles global state, API config, authentication, and core data fetching.
 */

      // App State
      let currentMainView = 'form';    // 'form', 'kanban', 'tasksList', 'employees', 'matrix'
      let currentEmpSubView = 'kanban'; // 'kanban' or 'list'
      let currentDeptFilter = 'ALL';
      let currentTasks = [];
      let currentEmployees = [];
      let assignedTasksList = [];
      let thinkingInterval = null;
      let thinkingElapsedSeconds = 0;
      let currentAvailabilityMap = {};
      let currentResult = null;
      let allExcludedCandidates = [];
      let currentPrimaryEmployeeId = null;
      let currentPrimaryCandidate = null;
      let currentDrawerEmployeeId = null;
      let employeeCodeToIdMap = {};
      let currentAssignMode = 'ai';
      let currentEffortHours = 16.0;

      const API_BASE = window.location.origin;
      let API_KEY = localStorage.getItem('sprintmatch_api_key') || 'dev-manager-key';




      function saveSettings() {
        const key = document.getElementById('configApiKey').value.trim();
        if (key) {
          API_KEY = key;
          localStorage.setItem('sprintmatch_api_key', key);
          showToast('Settings saved. Refreshing data...', 'success');
          checkHealth();
          fetchEmployeesCache();
          fetchTasks();
          toggleModal('settingsModal');
        }
      }



      // ================= HEALTH & INITIALIZATION =================
      async function checkHealth() {
        const dot = document.getElementById('healthDot');
        const text = document.getElementById('healthText');
        try {
          const res = await fetch(`${API_BASE}/ready`, { headers: { 'X-API-Key': API_KEY } });
          if (res.ok) {
            dot.className = 'w-2 h-2 rounded-full bg-emerald-400';
            text.innerText = 'Connected · Ready';
          } else {
            dot.className = 'w-2 h-2 rounded-full bg-rose-400';
            text.innerText = `HTTP ${res.status}`;
          }
        } catch (err) {
          dot.className = 'w-2 h-2 rounded-full bg-rose-500';
          text.innerText = 'Service Offline';
        }
      }

      async function fetchEmployeesCache() {
        try {
          const [empRes, availRes] = await Promise.all([
            fetch(`${API_BASE}/api/v1/employees?limit=100`, { headers: { 'X-API-Key': API_KEY } }),
            fetch(`${API_BASE}/api/v1/workforce/availability?preset=NEXT_WORKING_DAYS&working_days=5`, { headers: { 'X-API-Key': API_KEY } })
          ]);

          if (empRes.ok) {
            const data = await empRes.json();
            currentEmployees = data.items || [];
            currentEmployees.forEach(emp => {
              employeeCodeToIdMap[emp.employee_code] = emp.id;
            });
            const topCount = document.getElementById('topEmployeeCount');
            const dirCount = document.getElementById('empDirectoryCount');
            if (topCount) topCount.innerText = currentEmployees.length;
            if (dirCount) dirCount.innerText = currentEmployees.length;

          }

          if (availRes.ok) {
            const availData = await availRes.json();
            (availData.employees || []).forEach(row => {
              currentAvailabilityMap[row.employee_id] = row.availability;
            });
          }

          renderEmployeesApp();
          renderWorkforceMatrix();
        } catch (e) { }
      }

      async function fetchTasks() {
        try {
          const [resTasks, resAssigned] = await Promise.all([
            fetch(`${API_BASE}/api/v1/tasks?limit=150`, { headers: { 'X-API-Key': API_KEY } }),
            fetch(`${API_BASE}/api/v1/tasks/assigned`, { headers: { 'X-API-Key': API_KEY } }).catch(() => null)
          ]);

          if (resTasks && resTasks.ok) {
            const data = await resTasks.json();
            currentTasks = data.items || [];
          }

          if (resAssigned && resAssigned.ok) {
            const assignedData = await resAssigned.json();
            assignedTasksList = assignedData || [];
          }
          // Load and merge stored assigned tasks so nothing gets lost


          const countEl = document.getElementById('topTaskCount');
          const tblCountEl = document.getElementById('tasksTableTotalCount');
          if (countEl) countEl.innerText = currentTasks.length;
          if (tblCountEl) tblCountEl.innerText = currentTasks.length;

          // Render clean Scrum Board (Only assigned tasks!)

          renderTasksListView();
        } catch (e) {
          console.error('fetchTasks error:', e);
        }
      }


