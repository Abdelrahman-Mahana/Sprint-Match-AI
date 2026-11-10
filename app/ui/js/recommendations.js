/**
 * SprintMatch AI - Recommendation Results & Factor Cards
 * Renders candidate matches, factor breakdown, alternatives, and exclusions.
 */

      // ================= RENDER RESULTS =================
      function renderRecommendationResults(res) {
        if (!res) return;
        const cand = res.primary_candidate;
        currentPrimaryCandidate = cand;

        const primaryCard = document.getElementById('primaryCandidateCard');
        const noCandidateCard = document.getElementById('noSuitableCandidateCard');
        const statusHeading = document.getElementById('resultStatusHeading');
        const statusSub = document.getElementById('resultStatusSub');
        const statusDot = document.getElementById('resultStatusDot');

        if (!cand) {
          // No suitable candidate
          const isAr = /[\u0600-\u06FF]/.test(res.explanation || '');
          if (primaryCard) primaryCard.classList.add('hidden');
          if (noCandidateCard) noCandidateCard.classList.remove('hidden');
          if (statusHeading) statusHeading.innerText = isAr ? 'نتيجة التحليل: لا يوجد مرشح مستوفٍ لجميع الشروط' : 'Recommendation: No Eligible Candidate';
          if (statusSub) {
            statusSub.dir = isAr ? 'rtl' : 'ltr';
            statusSub.innerText = res.explanation || (isAr ? 'لم يستوفِ أي موظف الشروط الإلزامية للمهمة.' : 'All evaluated team members failed hard constraints (missing mandatory skills or capacity).');
          }
          if (statusDot) statusDot.className = 'w-3 h-3 rounded-full bg-amber-500';
          const reasonText = document.getElementById('noCandidateReasonText');
          if (reasonText) {
            reasonText.dir = isAr ? 'rtl' : 'ltr';
            reasonText.innerText = res.explanation || (isAr ? 'لم يستوفِ أي مرشح الحد الأدنى من المتطلبات.' : 'No candidate satisfied minimum requirements.');
          }
          renderFactorCards([]);
          renderAlternatives(res.alternatives || []);
          renderExcluded(res.excluded_candidates || []);
          return;
        }

        // Success candidate match
        const isAr = /[\u0600-\u06FF]/.test(res.explanation || '');
        if (primaryCard) primaryCard.classList.remove('hidden');
        if (noCandidateCard) noCandidateCard.classList.add('hidden');
        if (statusHeading) statusHeading.innerText = isAr ? 'ترشيح الذكاء الاصطناعي: تم اعتماد المرشح الأنسب' : 'AI Recommendation: Match Verified';
        if (statusSub) statusSub.innerText = isAr
          ? `تم ترشيح المطور الأنسب بنسبة ثقة تقييم بلغت ${Math.round((cand.confidence || 0.85) * 100)}%.`
          : `Recommended primary candidate with ${Math.round((cand.confidence || 0.85) * 100)}% evaluation confidence.`;
        if (statusDot) statusDot.className = 'w-3 h-3 rounded-full bg-emerald-500 animate-pulse';

        currentPrimaryEmployeeId = cand.employee_id;
        const empName = cand.name || cand.employee_name || 'Recommended Candidate';
        const empCode = cand.employee_code || '';

        const empRecord = currentEmployees.find(e => e.id === cand.employee_id || e.employee_code === empCode) || {};
        const jobTitle = empRecord.job_title || 'Software Engineer';
        const dept = empRecord.department_name || empRecord.department || 'Core Engineering';
        const team = empRecord.team_name || empRecord.team || 'Sprint Team';

        document.getElementById('heroCandidateName').innerText = empName;
        document.getElementById('heroCandidateCode').innerText = empCode;
        document.getElementById('heroCandidateTitle').innerText = `${jobTitle} · ${dept} (${team})`;
        document.getElementById('heroAvatar').innerText = empName.split(' ').filter(Boolean).map(n => n[0]).join('').substring(0, 2).toUpperCase();

        const rawScore = (cand.suitability_score !== undefined ? cand.suitability_score : cand.overall_score) ?? 0;
        const scoreVal = rawScore > 1 ? rawScore : (rawScore * 100);
        const scorePercent = scoreVal.toFixed(1);

        document.getElementById('heroScoreValue').innerText = `${scorePercent}%`;
        const heroRationale = document.getElementById('heroRationaleText');
        if (heroRationale) {
          heroRationale.dir = isAr ? 'rtl' : 'ltr';
          heroRationale.innerHTML = (res.explanation || cand.rationale || 'Strong skill alignment and available capacity in the execution sprint window.').replace(/\n/g, '<br>');
        }

        // Hero Badges
        const badgeStatus = document.getElementById('heroBadgeStatus');
        if (badgeStatus) {
          const isAvail = (cand.availability_status === 'AVAILABLE' || cand.available_hours > 0);
          badgeStatus.innerHTML = isAvail
            ? `<i class="fa-solid fa-circle-check text-[10px]"></i><span>${cand.availability_status || 'Available'}</span>`
            : `<i class="fa-solid fa-triangle-exclamation text-[10px]"></i><span>${cand.availability_status || 'Busy'}</span>`;
          badgeStatus.className = isAvail
            ? 'inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/60 border border-emerald-200 dark:border-emerald-800 px-2.5 py-0.5 rounded-full'
            : 'inline-flex items-center gap-1.5 text-xs font-semibold text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/60 border border-amber-200 dark:border-amber-800 px-2.5 py-0.5 rounded-full';
        }
        const badgeCapacity = document.getElementById('heroBadgeCapacity');
        if (badgeCapacity && cand.available_hours !== undefined) {
          badgeCapacity.innerHTML = `<i class="fa-regular fa-clock text-[10px]"></i><span>${Number(cand.available_hours).toFixed(1)}h Available Capacity</span>`;
        }

        // Render 7 Deterministic Factor Cards
        renderFactorCards(cand.score_components || cand.component_scores || []);

        // Render Alternatives
        renderAlternatives(res.alternatives || []);

        // Render Excluded
        renderExcluded(res.excluded_candidates || []);

        // Update Stages
        const sDraft = document.getElementById('stageDraft');
        if (sDraft) sDraft.className = 'o-stage-step completed';
        const sTriaged = document.getElementById('stageTriaged');
        if (sTriaged) sTriaged.className = 'o-stage-step completed';
        const sAiMatched = document.getElementById('stageAiMatched');
        if (sAiMatched) sAiMatched.className = 'o-stage-step active';
      }

      function renderFactorCards(scoreComponents) {
        const compMap = {};
        if (Array.isArray(scoreComponents)) {
          scoreComponents.forEach(c => {
            if (c && c.criterion) compMap[c.criterion] = c;
          });
        } else if (scoreComponents && typeof scoreComponents === 'object') {
          Object.assign(compMap, scoreComponents);
        }

        const isArFactors = Object.values(compMap).some(item => Array.isArray(item.evidence) && item.evidence.some(e => /[\u0600-\u06FF]/.test(e)));
        let factors = [
          { label: isArFactors ? 'المهارات' : 'Skill Match', key: 'SKILL_MATCH', icon: 'fa-brain', defaultWeight: '32%' },
          { label: isArFactors ? 'الخبرة السابقة' : 'Experience', key: 'EXPERIENCE', icon: 'fa-briefcase', defaultWeight: '16%' },
          { label: isArFactors ? 'جودة العمل' : 'Quality', key: 'QUALITY', icon: 'fa-award', defaultWeight: '16%' },
          { label: isArFactors ? 'الموثوقية' : 'Reliability', key: 'RELIABILITY', icon: 'fa-shield-halved', defaultWeight: '11%' },
          { label: isArFactors ? 'الساعات المتاحة' : 'Capacity', key: 'CAPACITY', icon: 'fa-clock', defaultWeight: '16%' },
          { label: isArFactors ? 'ملاءمة الصعوبة' : 'Complexity Fit', key: 'COMPLEXITY_FIT', icon: 'fa-bolt', defaultWeight: '11%' }
        ];

        // Only show PROJECT_FAMILIARITY if it is explicitly applicable (has a project)
        if (compMap['PROJECT_FAMILIARITY'] && compMap['PROJECT_FAMILIARITY'].applicable !== false) {
          factors.push({ label: isArFactors ? 'فريق المشروع' : 'Familiarity', key: 'PROJECT_FAMILIARITY', icon: 'fa-code-branch', defaultWeight: '5%' });
        }

        const grid = document.getElementById('factorCardsGrid');
        if (grid) {
          grid.innerHTML = '';
          grid.className = factors.length === 6
            ? 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3 text-center'
            : 'grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-3 text-center';
        }

        const heading = document.getElementById('factorsGridHeading');
        if (heading) {
          heading.innerText = factors.length === 6
            ? (isArFactors ? 'التقييم السداسي متعدد المعايير' : '6-Factor Deterministic Scoring Verification')
            : (isArFactors ? 'التقييم السباعي متعدد المعايير' : '7-Factor Deterministic Scoring Verification');
        }

        factors.forEach(f => {
          const item = compMap[f.key];
          let pct = 85;
          let weightStr = f.defaultWeight;
          let evidenceTooltip = '';
          let isNA = false;

          if (item) {
            if (item.applicable === false) {
              isNA = true;
            }
            const raw = (item.score_used !== undefined ? item.score_used : item.raw_score) ?? 85;
            pct = Math.round(raw > 1 ? raw : raw * 100);
            if (item.weight !== undefined) {
              weightStr = `${Math.round(item.weight * 100)}%`;
            }
            if (Array.isArray(item.evidence) && item.evidence.length > 0) {
              evidenceTooltip = item.evidence.join(' · ');
            }
          }

          const card = document.createElement('div');
          card.className = 'bg-slate-50 dark:bg-surface-100 p-3 rounded-xl border border-slate-200 dark:border-white/10 space-y-1.5 shadow-2xs group relative';
          if (evidenceTooltip) {
            card.title = evidenceTooltip;
          }
          card.innerHTML = `
          <div class="text-[10px] text-slate-500 flex items-center justify-center gap-1 font-semibold">
            <i class="fa-solid ${f.icon} text-odoo-teal"></i>
            <span>${f.label}</span>
          </div>
          <div class="text-base font-bold font-mono text-slate-900 dark:text-white">${isNA ? (isArFactors ? 'غير منطبق' : 'N/A') : pct + '%'}</div>
          <div class="w-full bg-slate-200 dark:bg-surface-200 h-1.5 rounded-full overflow-hidden">
            <div class="bg-odoo-teal h-full rounded-full transition-all duration-500" style="width: ${isNA ? 0 : pct}%"></div>
          </div>
          <div class="text-[9px] text-slate-400 font-mono">${isArFactors ? 'الوزن' : 'wt'}: ${weightStr}</div>
        `;
          grid.appendChild(card);
        });
      }

      function renderAlternatives(alternatives) {
        const list = document.getElementById('alternativesList');
        const badge = document.getElementById('altCountBadge');
        list.innerHTML = '';
        badge.innerText = (alternatives || []).length;

        if (!alternatives || alternatives.length === 0) {
          list.innerHTML = '<div class="p-6 text-center text-slate-400 text-xs">No alternative candidates met all hard constraints.</div>';
          return;
        }

        alternatives.forEach(alt => {
          const altName = alt.name || alt.employee_name || 'Alternative Candidate';
          const altCode = alt.employee_code || '';
          const empRecord = currentEmployees.find(e => e.id === alt.employee_id || e.employee_code === altCode) || {};
          const job = empRecord.job_title || 'Software Engineer';
          const rawScore = (alt.suitability_score !== undefined ? alt.suitability_score : alt.overall_score) ?? 0;
          const scoreVal = rawScore > 1 ? rawScore : (rawScore * 100);
          const score = scoreVal.toFixed(1);
          const initials = altName.split(' ').filter(Boolean).map(n => n[0]).join('').substring(0, 2).toUpperCase();

          const card = document.createElement('div');
          card.className = 'p-4 rounded-xl bg-slate-50 dark:bg-surface-100 border border-slate-200 dark:border-white/10 flex items-center justify-between gap-4';
          card.innerHTML = `
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-full bg-slate-200 dark:bg-surface-200 text-slate-700 dark:text-slate-300 font-bold flex items-center justify-center text-xs font-mono">
              ${initials}
            </div>
            <div>
              <div class="flex items-center gap-2">
                <span class="font-bold text-xs text-slate-900 dark:text-white">${altName}</span>
                <span class="font-mono text-[10px] text-slate-400">${altCode}</span>
              </div>
              <p class="text-[11px] text-slate-500">${job} · Rank #${alt.rank || 2}</p>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <div class="text-right">
              <span class="font-mono font-bold text-sm text-odoo-primary dark:text-pink-400">${score}%</span>
              <span class="block text-[9px] uppercase text-slate-400 font-semibold">Match Score</span>
            </div>
            <button onclick="assignSpecificEmployee('${alt.employee_id}', '${altName.replace(/'/g, "\'")}')" class="px-3 py-1.5 bg-white dark:bg-surface-200 hover:bg-odoo-primary hover:text-white text-slate-700 dark:text-slate-200 rounded-lg border border-slate-200 dark:border-white/10 text-xs font-medium transition">
              Assign Instead
            </button>
          </div>
        `;
          list.appendChild(card);
        });
      }

      function renderExcluded(excluded) {
        allExcludedCandidates = excluded || [];
        const list = document.getElementById('excludedList');
        const badge = document.getElementById('exCountBadge');
        list.innerHTML = '';
        badge.innerText = allExcludedCandidates.length;

        if (allExcludedCandidates.length === 0) {
          list.innerHTML = '<div class="p-6 text-center text-slate-400 text-xs">No team members were excluded.</div>';
          return;
        }

        allExcludedCandidates.forEach(ex => {
          const exName = ex.name || ex.employee_name || 'Team Member';
          const exCode = ex.employee_code || '';
          const reasons = (ex.exclusion_reasons && ex.exclusion_reasons.length > 0)
            ? ex.exclusion_reasons.map(r => r.message).join('; ')
            : (ex.reason || 'Failed constraint check');
          const code = (ex.exclusion_reasons && ex.exclusion_reasons[0]?.code)
            || ex.constraint_violated
            || 'EXCLUDED';

          const card = document.createElement('div');
          card.className = 'p-3.5 rounded-xl bg-rose-50/50 dark:bg-rose-950/20 border border-rose-200/60 dark:border-rose-900/40 flex items-center justify-between text-xs gap-3';
          card.innerHTML = `
          <div>
            <div class="flex items-center gap-2">
              <span class="font-bold text-slate-800 dark:text-slate-200">${exName}</span>
              <span class="font-mono text-[10px] text-slate-400">${exCode}</span>
            </div>
            <p class="text-[11px] text-rose-700 dark:text-rose-400 mt-0.5">${reasons}</p>
          </div>
          <span class="px-2.5 py-1 rounded bg-rose-100 dark:bg-rose-950/60 text-rose-700 dark:text-rose-300 font-mono text-[10px] uppercase font-semibold shrink-0">
            ${code}
          </span>
        `;
          list.appendChild(card);
        });
      }

      function filterExcludedCandidates() {
        const q = document.getElementById('excludedSearch').value.toLowerCase().trim();
        const filtered = allExcludedCandidates.filter(ex => {
          const name = (ex.name || ex.employee_name || '').toLowerCase();
          const code = (ex.employee_code || '').toLowerCase();
          const reasons = (ex.exclusion_reasons || []).map(r => (r.message || '').toLowerCase()).join(' ') + (ex.reason || '').toLowerCase();
          return name.includes(q) || code.includes(q) || reasons.includes(q);
        });
        renderExcluded(filtered);
      }

      // ================= ASSIGNMENT ACTION =================
      function assignRecommendedEmployee() {
        if (!currentPrimaryCandidate) {
          showToast('Please run SprintMatch AI recommendation first', 'warn');
          return;
        }
        const name = currentPrimaryCandidate.name || currentPrimaryCandidate.employee_name;
        assignSpecificEmployee(currentPrimaryCandidate.employee_id, name);
      }

      function assignSpecificEmployee(employeeId, employeeName) {
        currentPrimaryEmployeeId = employeeId;
        const emp = currentEmployees.find(e => e.id === employeeId || e.full_name === employeeName);
        const job = emp ? (emp.job_title || 'Software Engineer') : 'Team Member';
        const dept = emp ? (emp.department_name || emp.department || 'Engineering') : '';
        const empCode = emp ? (emp.employee_code || '') : '';

        const banner = document.getElementById('assignmentStatusBanner');
        if (banner) {
          banner.classList.remove('hidden');
          document.getElementById('assignedDevName').innerText = employeeName;
          document.getElementById('assignedDevSub').innerText = `${job} · ${dept}`;
          banner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        // Add to Scrum board under In Progress
        const randomHex = Math.floor(Math.random() * 0xFFFF).toString(16).toUpperCase().padStart(4, '0');
        const taskCode = document.getElementById('sheetTaskCode')?.innerText || `TASK-PM-${randomHex}`;
        const taskTitle = document.getElementById('taskTitleInput')?.value?.trim() || 'Custom Task';
        const hours = parseFloat(document.getElementById('taskHoursInput')?.value) || 16;
        const priority = document.getElementById('taskPriorityInput')?.value || 'HIGH';

        // Check if already in list
        const existingIdx = assignedTasksList.findIndex(x => x.task_code === taskCode || x.title === taskTitle);
        const assignedItem = {
          task_code: taskCode,
          title: taskTitle,
          estimated_hours: hours,
          priority: priority.toUpperCase(),
          task_status: 'IN_PROGRESS',
          assignment_status: 'ACTIVE',
          employee_id: employeeId,
          employee_name: employeeName,
          employee_code: empCode,
          job_title: job,
          department_name: dept
        };

        if (existingIdx !== -1) {
          assignedTasksList[existingIdx] = assignedItem;
        } else {
          assignedTasksList.unshift(assignedItem);
        }



        showToast(`Assigned task to ${employeeName} successfully!`, 'success');
      }



      function copyExplanation() {
        const text = document.getElementById('heroRationaleText').innerText;
        navigator.clipboard.writeText(text);
        showToast('Rationale copied to clipboard', 'info');
      }


