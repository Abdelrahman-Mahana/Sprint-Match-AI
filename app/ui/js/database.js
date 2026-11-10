/**
 * SprintMatch AI - Database & Schema Inspector Controller
 * Inspects PostgreSQL tables, schema metadata, records pagination, and external DB connections.
 */

      // ================= FULL DATABASE & SCHEMA INSPECTOR CONTROLLER =================
      let currentDbWorkforceSubTab = 'workforce';
      let currentInspectorTable = 'employees';
      let currentInspectorSubTab = 'data';
      let currentInspectorPage = 0;
      let allDatabaseTables = [];
      let currentInspectedData = null;
      let currentInspectorCat = 'ALL';

      function switchDbWorkforceSubTab(subTab) {
        loadDatabaseTables();
      }

      async function loadDatabaseTables() {
        const listContainer = document.getElementById('inspectorTablesList');
        if (listContainer) {
          listContainer.innerHTML = '<div class="p-8 text-center text-slate-400 text-xs"><i class="fa-solid fa-spinner fa-spin text-odoo-primary mr-2"></i>Loading database schema...</div>';
        }

        try {
          const [tablesRes, infoRes] = await Promise.all([
            fetch(`${API_BASE}/api/v1/database/tables`, { headers: { 'X-API-Key': API_KEY } }),
            fetch(`${API_BASE}/api/v1/database/info`, { headers: { 'X-API-Key': API_KEY } })
          ]);

          if (infoRes.ok) {
            const info = await infoRes.json();
            const dTitle = document.getElementById('inspectorActiveDbTitle');
            const dUri = document.getElementById('inspectorDbUriDisplay');
            const dPing = document.getElementById('inspectorPingBadge');
            const dRecs = document.getElementById('inspectorRecordsCountBadge');

            if (dTitle) dTitle.innerText = `Connected Database: ${info.database || 'sprintmatch'}`;
            if (dUri) dUri.innerText = info.masked_url || '';
            if (dPing) dPing.innerText = `${info.latency_ms || 1.2}ms`;
            if (dRecs && info.stats) {
              const sumRows = (info.stats.employees_count || 58) + (info.stats.tasks_count || 146);
              dRecs.innerText = `~${sumRows}+ Key Records`;
            }
          }

          if (tablesRes.ok) {
            const data = await tablesRes.json();
            allDatabaseTables = data.tables || [];
            const categories = data.categories || [];

            // Dynamically populate Category Filter Pills based on active database schema
            renderInspectorCategoryPills(categories, data.total_tables || allDatabaseTables.length);

            const subCount = document.getElementById('subTablesCount');
            const badgeCount = document.getElementById('inspectorTablesCountBadge');
            if (subCount) subCount.innerText = `${allDatabaseTables.length} Tables`;
            if (badgeCount) badgeCount.innerText = `${allDatabaseTables.length} Tables`;
            const topNavCount = document.getElementById('topEmployeeCount');
            if (topNavCount) topNavCount.innerText = `${allDatabaseTables.length} Tables`;

            filterInspectorTables();

            // Auto-select 'employees' or first table
            const hasEmployees = allDatabaseTables.some(t => t.table_name === 'employees');
            const targetTable = hasEmployees ? 'employees' : (allDatabaseTables[0]?.table_name || '');
            if (targetTable) {
              inspectTable(targetTable, 0);
            }
          }
        } catch (e) {
          if (listContainer) {
            listContainer.innerHTML = `<div class="p-4 text-center text-rose-500 text-xs">Failed to load tables: ${e.message}</div>`;
          }
        }
      }

      function getTableCategoryIcon(category, tableName) {
        const c = ((category || '') + ' ' + (tableName || '')).toLowerCase();
        if (c.includes('user') || c.includes('employee') || c.includes('member') || c.includes('workforce') || c.includes('hr') || c.includes('partner') || c.includes('customer') || c.includes('team')) {
          return 'fa-users';
        }
        if (c.includes('task') || c.includes('project') || c.includes('sprint') || c.includes('order') || c.includes('workflow') || c.includes('ticket') || c.includes('process')) {
          return 'fa-list-check';
        }
        if (c.includes('ai') || c.includes('agent') || c.includes('recommendation') || c.includes('intelligence') || c.includes('bot')) {
          return 'fa-brain';
        }
        if (c.includes('metric') || c.includes('performance') || c.includes('score') || c.includes('stat') || c.includes('analytic') || c.includes('evaluation')) {
          return 'fa-chart-line';
        }
        if (c.includes('audit') || c.includes('log') || c.includes('event') || c.includes('history') || c.includes('time') || c.includes('calendar') || c.includes('schedule')) {
          return 'fa-clock-rotate-left';
        }
        if (c.includes('setting') || c.includes('config') || c.includes('system') || c.includes('security') || c.includes('auth')) {
          return 'fa-shield-halved';
        }
        if (c.includes('product') || c.includes('catalog') || c.includes('item') || c.includes('stock') || c.includes('inventory') || c.includes('purchase')) {
          return 'fa-box-open';
        }
        if (c.includes('company') || c.includes('department') || c.includes('structure') || c.includes('skill') || c.includes('organization')) {
          return 'fa-sitemap';
        }
        return 'fa-table';
      }

      function renderInspectorCategoryPills(categories, totalCount) {
        const container = document.getElementById('inspectorCategoryPills');
        if (!container) return;

        container.innerHTML = '';

        // If previous category does not exist in the newly connected database, reset to ALL
        if (currentInspectorCat !== 'ALL' && !categories.some(c => c.name === currentInspectorCat)) {
          currentInspectorCat = 'ALL';
        }

        // "All" button
        const allBtn = document.createElement('button');
        allBtn.type = 'button';
        allBtn.setAttribute('data-cat', 'ALL');
        allBtn.className = (currentInspectorCat === 'ALL')
          ? 'inspector-cat-btn px-2.5 py-1 rounded-md bg-odoo-primary text-white font-semibold transition shrink-0'
          : 'inspector-cat-btn px-2.5 py-1 rounded-md bg-slate-100 dark:bg-surface-100 text-slate-600 dark:text-slate-400 hover:bg-slate-200 transition shrink-0';
        allBtn.innerText = `All (${totalCount})`;
        allBtn.onclick = () => filterInspectorByCat('ALL');
        container.appendChild(allBtn);

        // Dynamic category buttons
        categories.forEach(cat => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.setAttribute('data-cat', cat.name);
          btn.className = (currentInspectorCat === cat.name)
            ? 'inspector-cat-btn px-2.5 py-1 rounded-md bg-odoo-primary text-white font-semibold transition shrink-0'
            : 'inspector-cat-btn px-2.5 py-1 rounded-md bg-slate-100 dark:bg-surface-100 text-slate-600 dark:text-slate-400 hover:bg-slate-200 transition shrink-0';
          btn.innerText = `${cat.name} (${cat.count})`;
          btn.onclick = () => filterInspectorByCat(cat.name);
          container.appendChild(btn);
        });
      }

      function filterInspectorByCat(cat) {
        currentInspectorCat = cat;
        document.querySelectorAll('.inspector-cat-btn').forEach(btn => {
          if (btn.getAttribute('data-cat') === cat) {
            btn.className = 'inspector-cat-btn px-2.5 py-1 rounded-md bg-odoo-primary text-white font-semibold transition shrink-0';
          } else {
            btn.className = 'inspector-cat-btn px-2.5 py-1 rounded-md bg-slate-100 dark:bg-surface-100 text-slate-600 dark:text-slate-400 hover:bg-slate-200 transition shrink-0';
          }
        });
        filterInspectorTables();
      }

      function filterInspectorTables() {
        const container = document.getElementById('inspectorTablesList');
        if (!container) return;

        const q = (document.getElementById('inspectorTableSearch')?.value || '').toLowerCase().trim();
        const filtered = allDatabaseTables.filter(t => {
          const matchesCat = currentInspectorCat === 'ALL' || t.category === currentInspectorCat;
          const matchesQuery = !q || t.table_name.toLowerCase().includes(q) || t.category.toLowerCase().includes(q);
          return matchesCat && matchesQuery;
        });

        const countEl = document.getElementById('inspectorFilteredTablesCount');
        if (countEl) countEl.innerText = filtered.length;

        if (filtered.length === 0) {
          container.innerHTML = '<div class="p-6 text-center text-slate-400 text-xs">No tables match your filter.</div>';
          return;
        }

        container.innerHTML = '';
        filtered.forEach(t => {
          const isActive = t.table_name === currentInspectorTable;
          const card = document.createElement('div');
          card.className = `p-2.5 rounded-xl border transition cursor-pointer flex items-center justify-between text-xs group ${isActive
              ? 'border-odoo-primary bg-odoo-primary/5 dark:bg-pink-950/20 font-semibold'
              : 'border-slate-200 dark:border-white/10 hover:border-slate-300 dark:hover:border-white/20 hover:bg-slate-50 dark:hover:bg-surface-100'
            }`;
          card.onclick = () => inspectTable(t.table_name, 0);

          const icon = getTableCategoryIcon(t.category, t.table_name);

          card.innerHTML = `
          <div class="flex items-center gap-2.5 min-w-0">
            <i class="fa-solid ${icon} text-xs ${isActive ? 'text-odoo-primary dark:text-pink-400' : 'text-slate-400 group-hover:text-odoo-primary'}"></i>
            <div class="truncate">
              <span class="block truncate ${isActive ? 'text-odoo-primary dark:text-pink-300' : 'text-slate-800 dark:text-slate-200'}">${t.table_name}</span>
              <span class="text-[10px] text-slate-400 block">${t.columns_count} columns</span>
            </div>
          </div>
          <span class="text-[10px] font-mono px-2 py-0.5 rounded-full ${isActive ? 'bg-odoo-primary text-white font-bold' : 'bg-slate-100 dark:bg-surface-200 text-slate-600 dark:text-slate-300'} shrink-0">
            ${t.row_count} rows
          </span>
        `;
          container.appendChild(card);
        });
      }

      async function inspectTable(tableName, page = 0, search = '') {
        currentInspectorTable = tableName;
        currentInspectorPage = page;
        filterInspectorTables(); // update active highlight

        const titleEl = document.getElementById('activeTableTitle');
        const statsEl = document.getElementById('activeTableStatsSubtitle');
        const catBadge = document.getElementById('activeTableCategoryBadge');
        const thead = document.getElementById('inspectorDataTableHead');
        const tbody = document.getElementById('inspectorDataTableBody');
        const schemaTbody = document.getElementById('inspectorSchemaTableBody');

        if (titleEl) titleEl.innerText = tableName;
        if (statsEl) statsEl.innerText = 'Loading table data and schema...';

        const foundMeta = allDatabaseTables.find(t => t.table_name === tableName);
        if (catBadge) catBadge.innerText = foundMeta ? foundMeta.category : 'Database Table';

        try {
          const offset = page * 50;
          const url = `${API_BASE}/api/v1/database/tables/${tableName}?limit=50&offset=${offset}${search ? `&search=${encodeURIComponent(search)}` : ''}`;
          const res = await fetch(url, { headers: { 'X-API-Key': API_KEY } });
          if (!res.ok) throw new Error('Table inspect query failed');
          const data = await res.json();
          currentInspectedData = data;

          const pkCols = data.columns.filter(c => c.primary_key).map(c => c.name).join(', ') || 'None';
          if (statsEl) statsEl.innerText = `${data.total_rows} Total Rows · ${data.columns.length} Columns · PK: ${pkCols}`;

          // 1. Render Schema Tab
          if (schemaTbody) {
            schemaTbody.innerHTML = '';
            data.columns.forEach(col => {
              const tr = document.createElement('tr');
              tr.className = 'hover:bg-slate-50 dark:hover:bg-surface-100 transition';
              tr.innerHTML = `
              <td class="px-4 py-2.5 font-mono font-bold text-slate-900 dark:text-white flex items-center gap-1.5">
                ${col.primary_key ? '<i class="fa-solid fa-key text-[10px] text-amber-500" title="Primary Key"></i>' : ''}
                <span>${col.name}</span>
              </td>
              <td class="px-4 py-2.5 font-mono text-[11px] text-odoo-primary dark:text-pink-300">
                ${col.type}
              </td>
              <td class="px-4 py-2.5">
                ${col.primary_key ? '<span class="px-2 py-0.5 rounded bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300 font-semibold text-[10px]">PRIMARY KEY</span>' : ''}
                ${col.foreign_key ? `<span class="px-2 py-0.5 rounded bg-blue-100 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300 font-semibold text-[10px] ml-1">FK → ${col.foreign_key}</span>` : ''}
                ${!col.primary_key && !col.foreign_key ? '<span class="text-slate-400 text-[10px]">—</span>' : ''}
              </td>
              <td class="px-4 py-2.5">
                ${col.nullable ? '<span class="text-slate-500 font-mono text-[10px]">NULL</span>' : '<span class="text-rose-600 font-mono font-semibold text-[10px]">NOT NULL</span>'}
              </td>
              <td class="px-4 py-2.5 font-mono text-[10px] text-slate-400">
                ${col.default !== null ? col.default : '<span class="italic text-slate-300 dark:text-slate-600">none</span>'}
              </td>
            `;
              schemaTbody.appendChild(tr);
            });
          }

          // 2. Render Data Grid Tab
          if (thead && tbody) {
            thead.innerHTML = '';
            tbody.innerHTML = '';

            const headTr = document.createElement('tr');
            headTr.innerHTML = '<th class="px-3 py-2.5 w-10 text-slate-400 text-center font-mono">#</th>' +
              data.columns_order.map(cName => {
                const colMeta = data.columns.find(c => c.name === cName);
                const isPk = colMeta && colMeta.primary_key;
                return `<th class="px-3 py-2.5 ${isPk ? 'text-amber-600 dark:text-amber-400 font-bold' : ''}">
                <div class="flex items-center gap-1">
                  ${isPk ? '<i class="fa-solid fa-key text-[9px]"></i>' : ''}
                  <span>${cName}</span>
                </div>
                <span class="text-[9px] font-normal text-slate-400 font-mono block">${colMeta ? colMeta.type.toLowerCase() : ''}</span>
              </th>`;
              }).join('');
            thead.appendChild(headTr);

            if (data.rows.length === 0) {
              tbody.innerHTML = `<tr><td colspan="${data.columns_order.length + 1}" class="p-8 text-center text-slate-400">No records found in table "${tableName}".</td></tr>`;
            } else {
              data.rows.forEach((row, rIdx) => {
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-slate-50 dark:hover:bg-surface-100 transition';
                let cells = `<td class="px-3 py-2 text-center text-slate-400 text-[10px] font-mono select-none">${offset + rIdx + 1}</td>`;

                data.columns_order.forEach(colName => {
                  const val = row[colName];
                  let displayVal = val;
                  let cellClass = 'text-slate-700 dark:text-slate-300';

                  if (val === null || val === undefined) {
                    displayVal = '<span class="italic text-slate-300 dark:text-slate-600">null</span>';
                  } else if (typeof val === 'boolean') {
                    displayVal = val
                      ? '<span class="px-1.5 py-0.2 rounded bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300 font-bold text-[9px]">true</span>'
                      : '<span class="px-1.5 py-0.2 rounded bg-slate-100 text-slate-600 dark:bg-surface-200 dark:text-slate-400 text-[9px]">false</span>';
                  } else if (typeof val === 'string' && val.length > 50) {
                    displayVal = `<span title="${val.replace(/"/g, '&quot;')}">${val.substring(0, 48)}…</span>`;
                  }

                  cells += `<td class="px-3 py-2 ${cellClass}">${displayVal}</td>`;
                });

                tr.innerHTML = cells;
                tbody.appendChild(tr);
              });
            }
          }

          // 3. Update Pagination controls
          const startRow = data.rows.length > 0 ? offset + 1 : 0;
          const endRow = Math.min(offset + data.rows.length, data.total_rows);
          const pagLabel = document.getElementById('inspectorPaginationLabel');
          if (pagLabel) pagLabel.innerText = `Showing ${startRow} - ${endRow} of ${data.total_rows}`;

          const btnPrev = document.getElementById('btnInspectorPrev');
          const btnNext = document.getElementById('btnInspectorNext');
          if (btnPrev) btnPrev.disabled = page === 0;
          if (btnNext) btnNext.disabled = endRow >= data.total_rows;

        } catch (e) {
          if (statsEl) statsEl.innerText = `Error querying table: ${e.message}`;
        }
      }

      function switchInspectorTableSubTab(tab) {
        currentInspectorSubTab = tab;
        const dataWrapper = document.getElementById('inspectorDataGridWrapper');
        const schemaWrapper = document.getElementById('inspectorSchemaWrapper');
        const searchWrapper = document.getElementById('inspectorRowSearchWrapper');
        const btnData = document.getElementById('btnInspectorSubData');
        const btnSchema = document.getElementById('btnInspectorSubSchema');

        if (tab === 'data') {
          if (dataWrapper) dataWrapper.classList.remove('hidden');
          if (schemaWrapper) schemaWrapper.classList.add('hidden');
          if (searchWrapper) searchWrapper.classList.remove('hidden');
          if (btnData) btnData.className = 'px-3 py-1 rounded-md bg-white dark:bg-surface-200 text-odoo-primary dark:text-white font-semibold shadow-2xs transition flex items-center gap-1.5 cursor-pointer';
          if (btnSchema) btnSchema.className = 'px-3 py-1 rounded-md text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition flex items-center gap-1.5 font-medium cursor-pointer';
        } else {
          if (dataWrapper) dataWrapper.classList.add('hidden');
          if (schemaWrapper) schemaWrapper.classList.remove('hidden');
          if (searchWrapper) searchWrapper.classList.add('hidden');
          if (btnSchema) btnSchema.className = 'px-3 py-1 rounded-md bg-white dark:bg-surface-200 text-odoo-primary dark:text-white font-semibold shadow-2xs transition flex items-center gap-1.5 cursor-pointer';
          if (btnData) btnData.className = 'px-3 py-1 rounded-md text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition flex items-center gap-1.5 font-medium cursor-pointer';
        }
      }

      function changeInspectorPage(direction) {
        const newPage = Math.max(0, currentInspectorPage + direction);
        const search = document.getElementById('inspectorRowSearchInput')?.value?.trim() || '';
        inspectTable(currentInspectorTable, newPage, search);
      }

      function searchInspectorRows() {
        const search = document.getElementById('inspectorRowSearchInput')?.value?.trim() || '';
        inspectTable(currentInspectorTable, 0, search);
      }

      function exportInspectedTableCSV() {
        if (!currentInspectedData || !currentInspectedData.rows || currentInspectedData.rows.length === 0) {
          showToast('No records loaded to export', 'warn');
          return;
        }
        const cols = currentInspectedData.columns_order;
        const rows = [cols.join(",")];

        currentInspectedData.rows.forEach(r => {
          const line = cols.map(c => {
            const val = r[c] === null || r[c] === undefined ? "" : String(r[c]);
            return `"${val.replace(/"/g, '""')}"`;
          });
          rows.push(line.join(","));
        });

        const blob = new Blob([rows.join(String.fromCharCode(13, 10))], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${currentInspectorTable}-${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast(`Exported ${currentInspectorTable} as CSV!`, 'success');
      }

      function exportInspectedTableJSON() {
        if (!currentInspectedData || !currentInspectedData.rows || currentInspectedData.rows.length === 0) {
          showToast('No records loaded to export', 'warn');
          return;
        }
        const blob = new Blob([JSON.stringify(currentInspectedData.rows, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${currentInspectorTable}-${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast(`Exported ${currentInspectorTable} as JSON!`, 'success');
      }


      // ================= DATABASE CONNECTION MANAGER =================
      let currentDbMode = 'uri';

      async function openDatabaseModal() {
        const modal = document.getElementById('databaseConnectionModal');
        const feedback = document.getElementById('dbTestFeedbackBox');
        if (feedback) feedback.classList.add('hidden');

        try {
          const res = await fetch(`${API_BASE}/api/v1/database/info`, {
            headers: { 'X-API-Key': API_KEY }
          });
          if (res.ok) {
            const info = await res.json();
            const dDialect = document.getElementById('activeDbDialect');
            const dName = document.getElementById('activeDbName');
            const dHostPort = document.getElementById('activeDbHostPort');
            const dRecords = document.getElementById('activeDbRecords');
            const dLatency = document.getElementById('activeDbLatencyBadge');
            const dMasked = document.getElementById('activeDbMaskedUrl');
            const inputUrl = document.getElementById('inputDatabaseUrl');

            if (dDialect) dDialect.innerText = (info.dialect || 'PostgreSQL').toUpperCase();
            if (dName) dName.innerText = info.database || 'sprintmatch';
            if (dHostPort) dHostPort.innerText = `${info.host || 'localhost'}:${info.port || 55432}`;
            if (dRecords) dRecords.innerText = `${info.stats?.employees_count || 58} Devs (${info.stats?.tables_count || 35} Tables)`;
            if (dLatency) dLatency.innerText = `Ping: ${info.latency_ms || 1.2}ms`;
            if (dMasked) dMasked.innerText = info.masked_url || '';
            if (inputUrl && !inputUrl.value) inputUrl.value = info.masked_url || '';
          }
        } catch (e) { }

        if (modal) modal.classList.remove('hidden');
      }

      function switchDbInputMode(mode) {
        currentDbMode = mode;
        const modeUri = document.getElementById('dbInputModeUri');
        const modeForm = document.getElementById('dbInputModeForm');
        const btnUri = document.getElementById('btnDbModeUri');
        const btnForm = document.getElementById('btnDbModeForm');

        if (mode === 'uri') {
          if (modeUri) modeUri.classList.remove('hidden');
          if (modeForm) modeForm.classList.add('hidden');
          if (btnUri) btnUri.className = 'px-3 py-1 rounded-md bg-odoo-primary text-white font-semibold transition';
          if (btnForm) btnForm.className = 'px-3 py-1 rounded-md text-slate-600 dark:text-slate-400 hover:text-slate-900 transition';
        } else {
          if (modeUri) modeUri.classList.add('hidden');
          if (modeForm) modeForm.classList.remove('hidden');
          if (btnForm) btnForm.className = 'px-3 py-1 rounded-md bg-odoo-primary text-white font-semibold transition';
          if (btnUri) btnUri.className = 'px-3 py-1 rounded-md text-slate-600 dark:text-slate-400 hover:text-slate-900 transition';
        }
      }

      function selectDbPreset(preset) {
        const inputUrl = document.getElementById('inputDatabaseUrl');
        const host = document.getElementById('inputDbHost');
        const port = document.getElementById('inputDbPort');
        const db = document.getElementById('inputDbName');
        const user = document.getElementById('inputDbUser');
        const pwd = document.getElementById('inputDbPassword');

        if (preset === 'local_55432') {
          if (inputUrl) inputUrl.value = 'postgresql+psycopg://sprintmatch:sprintmatch_dev_pw@localhost:55432/sprintmatch';
          if (host) host.value = 'localhost';
          if (port) port.value = '55432';
          if (db) db.value = 'sprintmatch';
          if (user) user.value = 'sprintmatch';
          if (pwd) pwd.value = 'sprintmatch_dev_pw';
          showToast('Loaded Local PostgreSQL (55432) Preset', 'info');
        } else if (preset === 'standard_5432') {
          if (inputUrl) inputUrl.value = 'postgresql+psycopg://postgres:postgres@localhost:5432/sprintmatch';
          if (host) host.value = 'localhost';
          if (port) port.value = '5432';
          if (db) db.value = 'sprintmatch';
          if (user) user.value = 'postgres';
          if (pwd) pwd.value = 'postgres';
          showToast('Loaded Standard PostgreSQL (5432) Preset', 'info');
        } else if (preset === 'custom_cloud') {
          if (inputUrl) inputUrl.value = 'postgresql+psycopg://postgres.projectref:password@aws-0-region.pooler.supabase.com:6543/postgres';
          if (host) host.value = 'aws-0-region.pooler.supabase.com';
          if (port) port.value = '6543';
          if (db) db.value = 'postgres';
          if (user) user.value = 'postgres.projectref';
          if (pwd) pwd.value = '';
          showToast('Loaded Cloud DB Preset template', 'info');
        }
      }

      function togglePasswordVisibility(inputId, iconId) {
        const input = document.getElementById(inputId);
        const icon = document.getElementById(iconId);
        if (!input) return;
        if (input.type === 'password') {
          input.type = 'text';
          if (icon) icon.className = 'fa-regular fa-eye-slash text-xs';
        } else {
          input.type = 'password';
          if (icon) icon.className = 'fa-regular fa-eye text-xs';
        }
      }

      function getDbPayload() {
        if (currentDbMode === 'uri') {
          const rawUrl = document.getElementById('inputDatabaseUrl')?.value?.trim();
          return { database_url: rawUrl };
        } else {
          const host = document.getElementById('inputDbHost')?.value?.trim();
          const port = parseInt(document.getElementById('inputDbPort')?.value) || 5432;
          const db = document.getElementById('inputDbName')?.value?.trim();
          const user = document.getElementById('inputDbUser')?.value?.trim();
          const pwd = document.getElementById('inputDbPassword')?.value || '';
          return {
            host: host,
            port: port,
            database: db,
            username: user,
            password: pwd,
            engine_type: 'postgresql'
          };
        }
      }

      async function testDatabaseConnection() {
        const payload = getDbPayload();
        const feedback = document.getElementById('dbTestFeedbackBox');
        const btn = document.getElementById('btnTestDbConnection');

        if (!payload.database_url && !payload.host) {
          showToast('Please enter connection string or host parameters', 'warn');
          return;
        }

        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-xs"></i><span>Testing Connection...</span>';

        if (feedback) {
          feedback.classList.remove('hidden');
          feedback.className = 'rounded-xl p-3.5 text-xs bg-slate-100 dark:bg-surface-100 border border-slate-200 dark:border-white/10 space-y-1 text-slate-700 dark:text-slate-300';
          feedback.innerHTML = '<div class="flex items-center gap-2"><i class="fa-solid fa-spinner fa-spin text-odoo-primary"></i><span>Attempting live handshake with database...</span></div>';
        }

        try {
          const res = await fetch(`${API_BASE}/api/v1/database/test`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-API-Key': API_KEY
            },
            body: JSON.stringify(payload)
          });

          const data = await res.json();
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-solid fa-vial-circle-check text-xs text-odoo-teal"></i><span>Test Connection (اختبار الاتصال)</span>';

          if (data.ok) {
            const tablesList = (data.tables_found || []).slice(0, 8).join(', ');
            feedback.className = 'rounded-xl p-3.5 text-xs bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-300 dark:border-emerald-800 space-y-1.5 text-emerald-900 dark:text-emerald-200';
            feedback.innerHTML = `
            <div class="flex items-center justify-between font-bold">
              <span class="flex items-center gap-1.5 text-emerald-700 dark:text-emerald-300">
                <i class="fa-solid fa-circle-check text-sm"></i>
                <span>Database Connection Verified Successfully!</span>
              </span>
              <span class="font-mono text-[10px] bg-emerald-200/60 dark:bg-emerald-900/60 px-2 py-0.5 rounded font-normal">Latency: ${data.latency_ms}ms</span>
            </div>
            <p class="text-[11px] leading-relaxed text-emerald-800 dark:text-emerald-300">${data.message}</p>
            <div class="text-[10px] text-emerald-700/80 dark:text-emerald-400/80">
              <span class="font-semibold">Detected Schema (${data.tables_count} Tables):</span> ${tablesList}...
            </div>
          `;
            showToast('Database connection test PASSED!', 'success');
          } else {
            feedback.className = 'rounded-xl p-3.5 text-xs bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800 space-y-1.5 text-rose-900 dark:text-rose-200';
            feedback.innerHTML = `
            <div class="flex items-center gap-1.5 font-bold text-rose-700 dark:text-rose-300">
              <i class="fa-solid fa-circle-xmark text-sm"></i>
              <span>Connection Failed</span>
            </div>
            <p class="text-[11px] leading-relaxed font-mono break-all text-rose-800 dark:text-rose-300">${data.message}</p>
          `;
            showToast('Connection test failed - check logs', 'error');
          }
        } catch (e) {
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-solid fa-vial-circle-check text-xs text-odoo-teal"></i><span>Test Connection (اختبار الاتصال)</span>';
          if (feedback) {
            feedback.className = 'rounded-xl p-3.5 text-xs bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800 text-rose-800 dark:text-rose-200';
            feedback.innerHTML = `<i class="fa-solid fa-triangle-exclamation mr-1"></i> Network error: ${e.message}`;
          }
        }
      }

      async function applyDatabaseConnection() {
        const payload = getDbPayload();
        const btn = document.getElementById('btnApplyDbConnection');

        if (!payload.database_url && !payload.host) {
          showToast('Please specify a database to connect to', 'warn');
          return;
        }

        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-xs"></i><span>Connecting & Reloading...</span>';

        try {
          const res = await fetch(`${API_BASE}/api/v1/database/connect`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-API-Key': API_KEY
            },
            body: JSON.stringify(payload)
          });

          const data = await res.json();
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-solid fa-plug-circle-check text-xs"></i><span>Connect & Apply (اتصال وتطبيق)</span>';

          if (res.ok && data.ok) {
            showToast(`Successfully connected to database "${data.database}"!`, 'success');
            toggleModal('databaseConnectionModal');

            // Update Health text in top navbar
            const healthText = document.getElementById('healthText');
            if (healthText) healthText.innerText = `${data.database} Connected`;

            // Reload workforce directory from new database
            await fetchEmployeesCache();
            await fetchTasks();
            await loadDatabaseTables();
          } else {
            showToast(data.detail || data.message || 'Failed to switch database', 'error');
            const feedback = document.getElementById('dbTestFeedbackBox');
            if (feedback) {
              feedback.classList.remove('hidden');
              feedback.className = 'rounded-xl p-3.5 text-xs bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800 text-rose-800 dark:text-rose-200';
              feedback.innerText = data.detail || data.message;
            }
          }
        } catch (e) {
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-solid fa-plug-circle-check text-xs"></i><span>Connect & Apply (اتصال وتطبيق)</span>';
          showToast(`Connection error: ${e.message}`, 'error');
        }
      }


