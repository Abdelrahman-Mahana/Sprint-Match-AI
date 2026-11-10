/**
 * SprintMatch AI - Bootstrap Initialization
 * Restores theme preference and triggers initial data load on DOMContentLoaded.
 */

      // ================= BOOTSTRAP ON LOAD =================
      window.addEventListener('DOMContentLoaded', () => {
        // Restore Theme
        if (localStorage.getItem('sprintmatch_theme') === 'dark') {
          document.documentElement.classList.add('dark');
          const icon = document.getElementById('themeIcon');
          if (icon) icon.className = 'fa-solid fa-sun text-amber-300 text-xs';
        }

        checkHealth();
        fetchEmployeesCache();
        fetchTasks();
      });

