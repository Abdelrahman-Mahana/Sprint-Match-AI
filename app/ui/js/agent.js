/**
 * SprintMatch AI - Agent Reasoning Engine & Thinking Animation
 * Manages GPT-style animated reasoning steps and recommendation agent execution.
 */

      // ================= CHATGPT-STYLE AI AGENT REASONING ENGINE =================
      const THINKING_STEPS_CONFIG = {
        en: [
          {
            step: 1,
            activeTitle: "Deconstructing task requirements & target tech stack...",
            activeDesc: (title, hours, comp) => `Parsing requirements for "${title}" (${hours} estimated hours, ${comp} complexity)...`,
            completedTitle: "Task Decomposition & Competency Extraction",
            completedDesc: "Core technical competencies, complexity rating, and sprint requirements decomposed.",
            icon: "fa-solid fa-compass fa-spin text-odoo-primary"
          },
          {
            step: 2,
            activeTitle: "Screening engineering roster across departments...",
            activeDesc: () => "Filtering active engineers across DevOps, Frontend, Mobile, QA, and Odoo teams against verified catalog...",
            completedTitle: "Engineering Roster & Skills Screening",
            completedDesc: "Screened candidates across all engineering departments against verified skills catalog.",
            icon: "fa-solid fa-users-viewfinder animate-pulse text-indigo-500"
          },
          {
            step: 3,
            activeTitle: "Validating real-time capacity and sprint availability...",
            activeDesc: () => "Checking Africa/Cairo standard shifts, active sprint commitments, and uncommitted working hours...",
            completedTitle: "Sprint Capacity & Work Schedule Verification",
            completedDesc: "Verified available sprint working hours in Africa/Cairo timezone without risking developer burnout.",
            icon: "fa-solid fa-calendar-check animate-pulse text-amber-500"
          },
          {
            step: 4,
            activeTitle: "Evaluating candidate trade-offs and multi-factor ranking...",
            activeDesc: () => "Computing 7-factor optimization: skill match, experience, quality, capacity & workload balance...",
            completedTitle: "Multi-Objective Trade-Offs & 7-Factor Ranking",
            completedDesc: "Computed multi-factor optimization: skill match, velocity, quality score, capacity, and project familiarity.",
            icon: "fa-solid fa-chart-line animate-pulse text-cyan-500"
          },
          {
            step: 5,
            activeTitle: "Synthesizing final decision memo & rationale...",
            activeDesc: () => "Formulating comprehensive recommendation rationale and candidate comparison memo...",
            completedTitle: "Decision Rationale & Recommendation Memo",
            completedDesc: "Synthesized executive recommendation rationale and transparent evaluation evidence.",
            icon: "fa-solid fa-pen-fancy animate-pulse text-purple-500"
          }
        ],
        ar: [
          {
            step: 1,
            activeTitle: "تفكيك متطلبات المهمة واستخراج المهارات الفنية المطلوبة...",
            activeDesc: (title, hours, comp) => `تحليل مواصفات "${title}" (${hours} ساعة تقديرية، مستوى صعوبة ${comp})...`,
            completedTitle: "تفكيك متطلبات المهمة واستخراج الكفاءات",
            completedDesc: "تم استخراج المهارات البرمجية الأساسية ومستوى التعقيد المطلوب بنجاح.",
            icon: "fa-solid fa-compass fa-spin text-odoo-primary"
          },
          {
            step: 2,
            activeTitle: "فحص سجل المهندسين ومطابقة التخصصات والأقسام...",
            activeDesc: () => "مطابقة كفاءات المهندسين في فرق DevOps، Frontend، Mobile، QA، و Odoo مع المهارات المعتمدة...",
            completedTitle: "فحص ومطابقة سجل المهندسين وقاموس المهارات",
            completedDesc: "تمت تصفية ومطابقة مهندسي Nile Digital Solutions عبر كافة الأقسام.",
            icon: "fa-solid fa-users-viewfinder animate-pulse text-indigo-500"
          },
          {
            step: 3,
            activeTitle: "التحقق من السعة الإنتاجية وساعات التفرغ في السبرنت...",
            activeDesc: () => "مراجعة جداول العمل (توقيت القاهرة)، والالتزامات الحالية لضمان عدم حدوث إجهاد وظيفي...",
            completedTitle: "التحقق من ساعات العمل والتفرغ المتاح",
            completedDesc: "تم التأكد من توفر ساعات كافية في نافذة السبرنت دون تعارض زمني.",
            icon: "fa-solid fa-calendar-check animate-pulse text-amber-500"
          },
          {
            step: 4,
            activeTitle: "حساب مصفوفة المفاضلة والتقييم متعدد المعايير...",
            activeDesc: () => "موازنة تطابق المهارات، الخبرة السابقة، سرعة الإنجاز، الجودة، وتوزيع أعباء العمل...",
            completedTitle: "مفاضلة المرشحين والتقييم متعدد المعايير",
            completedDesc: "تم احتساب درجات التطابق الموزونة وترتيب المرشحين بحسب الأولوية.",
            icon: "fa-solid fa-chart-line animate-pulse text-cyan-500"
          },
          {
            step: 5,
            activeTitle: "صياغة تقرير ومذكرة الترشيح النهائية المسببة...",
            activeDesc: () => "توليد تقرير القرار النهائي المبرر ومقارنة المرشح الأفضل بالبدائل المتاحة...",
            completedTitle: "مذكرة القرار والتوصية النهائية المسببة",
            completedDesc: "تمت صياغة تقرير التوصية النهائي متضمناً أسباب الاختيار والبدائل.",
            icon: "fa-solid fa-pen-fancy animate-pulse text-purple-500"
          }
        ]
      };

      let thinkingCurrentStepIndex = 0;
      let thinkingTaskCtx = { title: 'Task', hours: '16', complexity: 'high' };
      let thinkingIsArabic = false;

      function toggleThinkingDetails() {
        const body = document.getElementById('chatGptThinkingBody');
        const chevron = document.getElementById('thinkingChevron');
        const toggleText = document.getElementById('thinkingToggleStateText');
        if (!body) return;

        const isHidden = body.classList.contains('hidden');
        if (isHidden) {
          body.classList.remove('hidden');
          if (chevron) chevron.classList.add('rotate-180');
          if (toggleText) toggleText.innerText = thinkingIsArabic ? 'إخفاء التفاصيل' : 'Hide Details';
        } else {
          body.classList.add('hidden');
          if (chevron) chevron.classList.remove('rotate-180');
          if (toggleText) toggleText.innerText = thinkingIsArabic ? 'عرض التفاصيل' : 'Details';
        }
      }

      function renderThinkingStep(stepIdx, animateTransition = true) {
        thinkingCurrentStepIndex = stepIdx;
        const steps = thinkingIsArabic ? THINKING_STEPS_CONFIG.ar : THINKING_STEPS_CONFIG.en;
        const currentConfig = steps[stepIdx];

        // Update Header badges
        const stepBadge = document.getElementById('thinkingActiveStepBadge');
        if (stepBadge) {
          stepBadge.innerText = thinkingIsArabic ? `خطوة ${stepIdx + 1} من 5` : `Step ${stepIdx + 1} of 5`;
          stepBadge.className = 'text-[11px] font-medium px-2 py-0.5 rounded-full bg-odoo-primary/10 text-odoo-primary dark:text-pink-300 border border-odoo-primary/20';
        }

        // Update progress bar
        const progBar = document.getElementById('thinkingProgressBar');
        if (progBar) {
          const pct = Math.min(95, (stepIdx + 1) * 20);
          progBar.style.width = `${pct}%`;
        }

        // Update the previous step in timeline as completed
        if (stepIdx > 0) {
          const prevIdx = stepIdx; // 1-based for previous step ID
          const prevItem = document.getElementById(`thoughtItem${prevIdx}`);
          const prevTime = document.getElementById(`thoughtTime${prevIdx}`);
          const prevCheck = document.getElementById(`thoughtCheck${prevIdx}`);
          if (prevItem) {
            prevItem.classList.remove('hidden');
            prevItem.classList.remove('opacity-0');
          }
          if (prevTime) {
            prevTime.innerText = `${(thinkingElapsedSeconds || stepIdx * 0.9).toFixed(1)}s`;
          }
          if (prevCheck) {
            prevCheck.innerHTML = '<i class="fa-solid fa-check"></i>';
          }
        }

        // Smooth step transition on the Active Hero Card
        const cardTitle = document.getElementById('activeStepTitle');
        const cardDesc = document.getElementById('activeStepDescription');
        const cardIcon = document.getElementById('activeStepIcon');
        const statusPill = document.getElementById('activeStepStatusPill');

        const applyContent = () => {
          if (cardTitle) {
            cardTitle.innerText = currentConfig.activeTitle;
            cardTitle.style.opacity = '1';
          }
          if (cardDesc) {
            cardDesc.innerText = typeof currentConfig.activeDesc === 'function'
              ? currentConfig.activeDesc(thinkingTaskCtx.title, thinkingTaskCtx.hours, thinkingTaskCtx.complexity)
              : currentConfig.activeDesc;
            cardDesc.style.opacity = '1';
          }
          if (cardIcon) {
            cardIcon.className = currentConfig.icon;
          }
          if (statusPill) {
            statusPill.innerText = thinkingIsArabic ? 'قيد المعالجة' : 'Processing';
            statusPill.className = 'text-[10px] font-mono px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 font-semibold shrink-0 animate-pulse border border-amber-500/20';
          }
        };

        if (animateTransition && cardTitle && cardDesc) {
          cardTitle.style.opacity = '0';
          cardDesc.style.opacity = '0';
          setTimeout(applyContent, 120);
        } else {
          applyContent();
        }
      }

      function startAgentThinkingAnimation() {
        const awaitingEl = document.getElementById('awaitingRecommendationState');
        const resultsEl = document.getElementById('recommendationResultsSection');
        const container = document.getElementById('agentThinkingContainer');
        const body = document.getElementById('chatGptThinkingBody');
        const chevron = document.getElementById('thinkingChevron');
        const toggleText = document.getElementById('thinkingToggleStateText');
        const headerLabel = document.getElementById('thinkingHeaderLabel');
        const secondsBadge = document.getElementById('thinkingSecondsBadge');
        const iconInner = document.getElementById('thinkingIconInner');
        const statusIcon = document.getElementById('thinkingStatusIcon');

        if (awaitingEl) awaitingEl.classList.add('hidden');
        if (resultsEl) resultsEl.classList.add('hidden');

        if (container) {
          container.classList.remove('hidden');
          container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        // Default: Keep timeline collapsed while thinking so user focuses on the active step spotlight ("زي GPT")
        if (body) body.classList.add('hidden');
        if (chevron) chevron.classList.remove('rotate-180');

        // Task specs & language detection
        const taskTitle = document.getElementById('taskTitleInput')?.value?.trim() || 'Task';
        const taskDesc = document.getElementById('taskDescInput')?.value?.trim() || '';
        const hours = document.getElementById('taskHoursInput')?.value || '16';
        const complexity = document.getElementById('taskComplexityInput')?.value || 'high';

        thinkingTaskCtx = { title: taskTitle, hours: hours, complexity: complexity };
        thinkingIsArabic = /[\u0600-\u06FF]/.test(taskTitle + ' ' + taskDesc);

        if (toggleText) toggleText.innerText = thinkingIsArabic ? 'عرض التفاصيل' : 'Details';
        if (headerLabel) headerLabel.innerText = thinkingIsArabic ? 'جاري التفكير وتحليل السبرنت...' : 'Thinking...';
        if (secondsBadge) {
          secondsBadge.innerText = '0.0s';
          secondsBadge.classList.remove('hidden');
        }
        if (iconInner) {
          iconInner.className = 'fa-solid fa-sparkles animate-spin text-odoo-primary';
          iconInner.style.animationDuration = '2.5s';
        }
        if (statusIcon) {
          statusIcon.className = 'w-7 h-7 rounded-lg bg-odoo-primary/10 dark:bg-pink-500/10 text-odoo-primary dark:text-pink-300 flex items-center justify-center text-xs transition-colors shrink-0';
        }

        // Reset and display progress bar (green color)
        const progContainer = document.getElementById('thinkingProgressBarContainer');
        if (progContainer) progContainer.classList.remove('hidden');
        const initProgBar = document.getElementById('thinkingProgressBar');
        if (initProgBar) {
          initProgBar.className = 'bg-emerald-500 h-full transition-all duration-500 ease-out';
          initProgBar.style.width = '20%';
        }

        // Reset Active Card extras
        const extras = document.getElementById('activeStepExtras');
        if (extras) extras.innerHTML = '';
        const activePulse = document.getElementById('activeStepPulseGlow');
        if (activePulse) {
          activePulse.className = 'absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-transparent via-odoo-primary to-transparent animate-pulse';
        }

        // Reset Timeline Items
        for (let i = 1; i <= 5; i++) {
          const item = document.getElementById(`thoughtItem${i}`);
          if (item) {
            item.classList.add('hidden');
            item.classList.add('opacity-0');
          }
          const timeEl = document.getElementById(`thoughtTime${i}`);
          if (timeEl) timeEl.innerText = '...';
          const checkEl = document.getElementById(`thoughtCheck${i}`);
          if (checkEl) checkEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
        }

        // Localized Titles & Descriptions for Timeline
        const cfgList = thinkingIsArabic ? THINKING_STEPS_CONFIG.ar : THINKING_STEPS_CONFIG.en;
        for (let i = 1; i <= 5; i++) {
          const titleEl = document.getElementById(`thoughtTitle${i}`);
          const descEl = document.getElementById(`thoughtItem${i}Details`);
          if (titleEl) titleEl.innerText = cfgList[i - 1].completedTitle;
          if (descEl) descEl.innerText = cfgList[i - 1].completedDesc;
        }

        const skillsPills = document.getElementById('thoughtExtractedSkillsPills');
        if (skillsPills) skillsPills.innerHTML = `<span class="text-[10px] text-slate-400 italic">${thinkingIsArabic ? 'جاري استخراج المهارات البرمجية...' : 'Extracting competencies from task description...'}</span>`;

        const t5Memo = document.getElementById('thoughtExplanationText');
        if (t5Memo) {
          t5Memo.innerText = thinkingIsArabic ? 'جاري صياغة مذكرة القرار والترشيح...' : 'Synthesizing candidate comparison and decision rationale...';
        }
        const traceBadges = document.getElementById('thoughtGraphNodesBadges');
        if (traceBadges) traceBadges.innerHTML = '';

        // Start at Step 1 immediately
        renderThinkingStep(0, false);

        thinkingElapsedSeconds = 0;
        if (thinkingInterval) clearInterval(thinkingInterval);

        // Smooth step advancement:
        // Step 1: 0.0s - 1.1s
        // Step 2: 1.1s - 2.3s
        // Step 3: 2.3s - 3.5s
        // Step 4: 3.5s - 4.8s
        // Step 5: 4.8s+ (wait for response)
        thinkingInterval = setInterval(() => {
          thinkingElapsedSeconds += 0.2;
          if (secondsBadge) secondsBadge.innerText = `${thinkingElapsedSeconds.toFixed(1)}s`;

          if (thinkingElapsedSeconds >= 1.1 && thinkingCurrentStepIndex === 0) {
            renderThinkingStep(1, true);
          } else if (thinkingElapsedSeconds >= 2.3 && thinkingCurrentStepIndex === 1) {
            renderThinkingStep(2, true);
          } else if (thinkingElapsedSeconds >= 3.5 && thinkingCurrentStepIndex === 2) {
            renderThinkingStep(3, true);
          } else if (thinkingElapsedSeconds >= 4.8 && thinkingCurrentStepIndex === 3) {
            renderThinkingStep(4, true);
          }
        }, 200);
      }

      function finishAgentThinkingAnimation(res) {
        if (thinkingInterval) {
          clearInterval(thinkingInterval);
          thinkingInterval = null;
        }

        try {
          const headerLabel = document.getElementById('thinkingHeaderLabel');
          const secondsBadge = document.getElementById('thinkingSecondsBadge');
          const iconInner = document.getElementById('thinkingIconInner');
          const statusIcon = document.getElementById('thinkingStatusIcon');
          const progBar = document.getElementById('thinkingProgressBar');
          const stepBadge = document.getElementById('thinkingActiveStepBadge');

          // Calculate duration in seconds
          const durationSec = res.execution_time_ms
            ? (res.execution_time_ms / 1000).toFixed(1)
            : (thinkingElapsedSeconds > 0 ? thinkingElapsedSeconds.toFixed(1) : '1.5');

          // Progress bar to 100%
          if (progBar) progBar.style.width = '100%';
          // Hide progress bar when steps finish as requested
          const progContainer = document.getElementById('thinkingProgressBarContainer');
          if (progContainer) progContainer.classList.add('hidden');

          // Header Label: "Thought for X seconds" / "تم التفكير في X ثوانٍ"
          if (headerLabel) {
            headerLabel.innerText = thinkingIsArabic
              ? `فكر لمدة ${durationSec} ثانية`
              : `Thought for ${durationSec} seconds`;
          }
          if (secondsBadge) {
            secondsBadge.innerText = '';
            secondsBadge.classList.add('hidden');
          }
          if (stepBadge) {
            stepBadge.innerText = thinkingIsArabic ? '5 من 5 مكتملة' : '5 of 5 Complete';
            stepBadge.className = 'text-[11px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20';
          }
          if (iconInner) {
            iconInner.className = 'fa-solid fa-sparkles text-slate-400 dark:text-slate-500';
            iconInner.style.animationDuration = '';
          }
          if (statusIcon) {
            statusIcon.className = 'w-7 h-7 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center justify-center text-xs transition-colors shrink-0';
          }

          const cand = res.primary_candidate;
          const candName = cand ? (cand.name || cand.employee_name || 'Candidate') : 'Candidate';
          const candScore = cand
            ? Math.round(((cand.suitability_score !== undefined ? cand.suitability_score : cand.overall_score) || 0) * (cand.suitability_score <= 1 ? 100 : 1))
            : 0;
          const altCount = (res.alternatives || []).length;

          // Transform Active Hero Card into a sleek Completion State
          const cardTitle = document.getElementById('activeStepTitle');
          const cardDesc = document.getElementById('activeStepDescription');
          const cardIcon = document.getElementById('activeStepIcon');
          const statusPill = document.getElementById('activeStepStatusPill');
          const cardIconBox = document.getElementById('activeStepIconBox');
          const activePulse = document.getElementById('activeStepPulseGlow');

          if (cardTitle) {
            cardTitle.innerText = thinkingIsArabic
              ? 'تم اكتمال التفكير والتحليل الذكي بنجاح'
              : 'AI Reasoning & Matching Complete';
          }
          if (cardDesc) {
            cardDesc.innerHTML = thinkingIsArabic
              ? `تم ترشيح المهندس <strong class="text-slate-800 dark:text-white">${candName}</strong> (${candScore}% نسبة تطابق) بعد تقييم ${altCount} مرشحاً بديلاً عبر مصفوفة القيود السباعية.`
              : `Identified top match <strong class="text-slate-800 dark:text-white">${candName}</strong> (${candScore}% match score) with ${altCount} alternative candidate${altCount === 1 ? '' : 's'} evaluated.`;
          }
          if (cardIcon) {
            cardIcon.className = 'fa-solid fa-circle-check text-emerald-500 text-lg';
          }
          if (cardIconBox) {
            cardIconBox.className = 'w-9 h-9 rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center justify-center text-sm shrink-0 mt-0.5 shadow-2xs';
          }
          if (statusPill) {
            statusPill.innerText = thinkingIsArabic ? 'مكتمل' : 'Completed';
            statusPill.className = 'text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-semibold shrink-0 border border-emerald-500/20';
          }
          if (activePulse) {
            activePulse.className = 'absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-transparent via-emerald-500 to-transparent';
          }

          // Render Quick Mini-Pills in Active Hero Card Extras
          const reqSkills = (res.task_analysis && res.task_analysis.requirements && res.task_analysis.requirements.required_skills) || [];
          const extras = document.getElementById('activeStepExtras');
          if (extras) {
            extras.innerHTML = '';
            if (reqSkills.length > 0) {
              reqSkills.slice(0, 4).forEach(s => {
                const b = document.createElement('span');
                b.className = 'px-2 py-0.5 rounded-md bg-odoo-primary/10 text-odoo-primary dark:text-pink-300 border border-odoo-primary/20 text-[10px] font-mono font-medium';
                b.innerText = s.skill_name || s.name;
                extras.appendChild(b);
              });
              if (reqSkills.length > 4) {
                const more = document.createElement('span');
                more.className = 'text-[10px] text-slate-400';
                more.innerText = `+${reqSkills.length - 4} more`;
                extras.appendChild(more);
              }
            }
          }

          // Reveal and mark all 5 timeline items as done
          for (let i = 1; i <= 5; i++) {
            const item = document.getElementById(`thoughtItem${i}`);
            if (item) {
              item.classList.remove('hidden');
              item.classList.remove('opacity-0');
            }
            const timeEl = document.getElementById(`thoughtTime${i}`);
            if (timeEl) {
              timeEl.innerText = i === 5 ? `${durationSec}s` : `${(i * (parseFloat(durationSec) / 5)).toFixed(1)}s`;
            }
            const checkEl = document.getElementById(`thoughtCheck${i}`);
            if (checkEl) {
              checkEl.innerHTML = '<i class="fa-solid fa-check"></i>';
            }
          }

          // Step 1: Extracted Skills in timeline
          const skillsContainer = document.getElementById('thoughtExtractedSkillsPills');
          if (skillsContainer) {
            skillsContainer.innerHTML = '';
            if (reqSkills.length > 0) {
              reqSkills.forEach(s => {
                const badge = document.createElement('span');
                badge.className = s.mandatory
                  ? 'px-2 py-0.5 rounded-md bg-odoo-primary/10 text-odoo-primary dark:text-pink-300 border border-odoo-primary/20 text-[10px] font-semibold font-mono'
                  : 'px-2 py-0.5 rounded-md bg-slate-200/70 dark:bg-white/10 text-slate-700 dark:text-slate-300 border border-slate-300/40 dark:border-white/10 text-[10px] font-mono';
                badge.innerText = `${s.skill_name || s.name} (${s.mandatory ? (thinkingIsArabic ? 'إلزامي' : 'Mandatory') : (thinkingIsArabic ? 'إضافي' : 'Bonus')})`;
                skillsContainer.appendChild(badge);
              });
            } else {
              skillsContainer.innerHTML = `<span class="text-[10px] text-slate-400 italic">${thinkingIsArabic ? 'تم استنتاج الكفاءات البرمجية الأساسية' : 'Core engineering competencies inferred'}</span>`;
            }
          }

          // Step 4: Candidate evaluation details in timeline
          const t4Details = document.getElementById('thoughtItem4Details');
          if (t4Details && cand) {
            t4Details.innerHTML = thinkingIsArabic
              ? `تم تحديد المرشح الأفضل <strong class="text-slate-800 dark:text-white font-semibold">${candName}</strong> بنسبة تطابق ${candScore}% مع فحص ${altCount} مهندساً بديلاً.`
              : `Identified top match <strong class="text-slate-800 dark:text-white font-semibold">${candName}</strong> (${candScore}% match score) with ${altCount} alternative candidate${altCount === 1 ? '' : 's'} evaluated.`;
          }

          // Step 5: Decision Memo in timeline
          const memoEl = document.getElementById('thoughtExplanationText');
          if (memoEl) {
            const isAr = /[\u0600-\u06FF]/.test(res.explanation || '');
            memoEl.dir = isAr ? 'rtl' : 'ltr';
            const memoHeader = isAr ? 'تقرير ومذكرة الترشيح:' : 'Recommendation Memo:';
            memoEl.innerHTML = res.explanation
              ? `<span class="not-italic font-medium text-odoo-primary dark:text-pink-400 block mb-1">${memoHeader}</span> ${res.explanation.replace(/\n/g, '<br>')}`
              : 'Optimal candidate selected through weighted multi-objective constraint satisfaction.';
          }

          // Populate trace nodes
          const traceContainer = document.getElementById('thoughtGraphNodesBadges');
          const traceStats = document.getElementById('thoughtGraphTraceStats');
          if (traceContainer) {
            traceContainer.innerHTML = '';
            const trace = res.trace || [];
            if (traceStats) traceStats.innerText = `LangGraph Trace (${trace.length} nodes · ${res.execution_time_ms || 0}ms)`;
            trace.forEach(t => {
              const item = document.createElement('span');
              item.className = 'px-1.5 py-0.5 rounded bg-slate-200/80 dark:bg-surface-200 text-[10px] font-mono text-slate-600 dark:text-slate-300';
              item.innerText = `${t.node}${t.duration_ms !== undefined ? ` (${t.duration_ms}ms)` : ''}`;
              traceContainer.appendChild(item);
            });
          }

        } catch (err) {
          console.warn('Error in finishAgentThinkingAnimation:', err);
        } finally {
          const resultsEl = document.getElementById('recommendationResultsSection');
          if (resultsEl) resultsEl.classList.remove('hidden');
          const awaitingEl = document.getElementById('awaitingRecommendationState');
          if (awaitingEl) awaitingEl.classList.add('hidden');
        }
      }

      async function runRecommendationAgent() {
        const btn = document.getElementById('btnRunAgent');
        const btnIcon = document.getElementById('agentBtnIcon');
        const btnText = document.getElementById('agentBtnText');

        const taskTitle = document.getElementById('taskTitleInput')?.value?.trim();
        const taskDescription = document.getElementById('taskDescInput')?.value?.trim();
        const hours = parseFloat(document.getElementById('taskHoursInput')?.value) || 16.0;
        const complexity = document.getElementById('taskComplexityInput')?.value || 'high';
        const priority = document.getElementById('taskPriorityInput')?.value || 'high';
        const deadlineVal = document.getElementById('taskDeadlineInput')?.value;
        const asOf = document.getElementById('asOfInput')?.value?.trim() || '2026-11-03T10:00:00+02:00';

        if (!taskTitle) {
          showToast('Please enter a Task Title', 'warn');
          document.getElementById('taskTitleInput')?.focus();
          return;
        }
        if (!taskDescription) {
          showToast('Please enter a Task Description', 'warn');
          document.getElementById('taskDescInput')?.focus();
          return;
        }

        btn.disabled = true;
        if (btnIcon) btnIcon.className = 'fa-solid fa-spinner fa-spin text-xs';
        if (btnText) btnText.innerText = 'AI Agent Matching...';

        startAgentThinkingAnimation();

        const payload = {
          task: {
            title: taskTitle,
            description: taskDescription,
            project_id: null,
            estimated_hours: hours,
            priority: priority,
            complexity: complexity,
            deadline: deadlineVal ? `${deadlineVal}T18:00:00+02:00` : null
          },
          execution_window: {
            preset: 'NEXT_WORKING_DAYS',
            working_days: 5
          },
          as_of: asOf
        };

        try {
          const res = await fetch(`${API_BASE}/api/v1/recommendations`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-API-Key': API_KEY
            },
            body: JSON.stringify(payload)
          });

          if (!res.ok) {
            const err = await res.json();
            const msg = err.error?.message || err.detail || `HTTP ${res.status}`;
            throw new Error(msg);
          }

          const data = await res.json();
          currentResult = data;
          finishAgentThinkingAnimation(data);
          renderRecommendationResults(data);
          showToast('AI Recommendation generated successfully!', 'success');

          const resultsSec = document.getElementById('recommendationResultsSection');
          if (resultsSec) {
            resultsSec.classList.remove('hidden');
            resultsSec.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }
        } catch (err) {
          if (thinkingInterval) {
            clearInterval(thinkingInterval);
            thinkingInterval = null;
          }
          const headerLabel = document.getElementById('thinkingHeaderLabel');
          if (headerLabel) headerLabel.innerText = 'Thinking Interrupted';
          const iconInner = document.getElementById('thinkingIconInner');
          if (iconInner) {
            iconInner.className = 'fa-solid fa-triangle-exclamation text-rose-500';
            iconInner.style.animationDuration = '';
          }
          const memoEl = document.getElementById('thoughtExplanationText');
          if (memoEl) memoEl.innerText = `Agent stopped: ${err.message}`;
          showToast(`Recommendation error: ${err.message}`, 'error');
        } finally {
          btn.disabled = false;
          if (btnIcon) btnIcon.className = 'fa-solid fa-wand-magic-sparkles text-xs';
          if (btnText) btnText.innerText = 'Run SprintMatch AI Recommendation';
        }
      }


