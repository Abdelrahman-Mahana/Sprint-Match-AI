import sys
import json
import time
import urllib.request

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

API_URL = "http://127.0.0.1:8000/api/v1/recommendations"
HEADERS = {
    "Content-Type": "application/json",
    "X-API-Key": "dev-manager-key"
}

ROBUSTNESS_TEST_CASES = [
    {
        "id": "CASE_1_TYPOS_DEVOPS",
        "name": "Heavy Typos & Misspellings (DevOps)",
        "scenario": "Slang & misspelled technologies: 'doker', 'kubernets', 'terform', 'k8s', 'cicd'",
        "payload": {
            "task": {
                "title": "urgnt fixx doker and kubernets terform clstr",
                "description": "we need someone to write terform scripts and deploy doker images onto AWS k8s clstr, harden iam permissions and fix cicd pipelines asap.",
                "estimated_hours": 24.0,
                "priority": "high",
                "complexity": "high",
                "deadline": "2026-11-14T18:00:00+02:00"
            },
            "execution_window": {"preset": "NEXT_WORKING_DAYS", "working_days": 5},
            "as_of": "2026-11-03T10:00:00+02:00"
        },
        "expected_dept": "DevOps & Cloud",
        "expected_skills": ["Docker", "Kubernetes", "Terraform", "AWS", "CI/CD"],
    },
    {
        "id": "CASE_2_ARABIC_COLLOQUIAL",
        "name": "Colloquial Egyptian Arabic (عامي مصري) (Mobile)",
        "scenario": "Pure Egyptian Arabic prompt: 'فلاتر ودارت مع امان عالي للـ لوجين واستخدام البصمة'",
        "payload": {
            "task": {
                "title": "عاوزين نطور ابلكيشن موبايل فلاتر عشان الدفع السريع واللوجين",
                "description": "عاوزين نعمل ابلكيشن موبايل جديد شغال فلاتر ودارت مع امان عالي للـ لوجين واستخدام البصمة مع ربط السيرفر ويكون سريع جدا ومريح للعملاء.",
                "estimated_hours": 28.0,
                "priority": "high",
                "complexity": "high",
                "deadline": "2026-11-14T18:00:00+02:00"
            },
            "execution_window": {"preset": "NEXT_WORKING_DAYS", "working_days": 5},
            "as_of": "2026-11-03T10:00:00+02:00"
        },
        "expected_dept": "Frontend & Mobile",
        "expected_skills": ["Flutter", "API Security"],
    },
    {
        "id": "CASE_3_MESSY_BACKEND_ARABIZI",
        "name": "Messy Backend with Arabizi & Slang (FastAPI + Caching)",
        "scenario": "Mixed Arabizi & English typos: 'psql fast-api rediss sqlalchemy'",
        "payload": {
            "task": {
                "title": "fix backend api crashes psql fast-api rediss",
                "description": "el system 3ando high traffic w el psql queries ba2et batee2a gedan, me7tageen n-optimize el fast-api endpoints w n-add rediss caching w sqlalchemy connection pooling.",
                "estimated_hours": 20.0,
                "priority": "high",
                "complexity": "high",
                "deadline": "2026-11-14T18:00:00+02:00"
            },
            "execution_window": {"preset": "NEXT_WORKING_DAYS", "working_days": 5},
            "as_of": "2026-11-03T10:00:00+02:00"
        },
        "expected_dept": "Backend Engineering",
        "expected_skills": ["FastAPI", "PostgreSQL", "Redis", "SQLAlchemy"],
    },
    {
        "id": "CASE_4_FRONTEND_TYPOS",
        "name": "Frontend Typos & Slang (React / Next.js)",
        "scenario": "Informal client description: 'reack and nxtjs dashboard with tailwindcss and ts'",
        "payload": {
            "task": {
                "title": "reack and nxtjs dashboard for clients",
                "description": "client needs responsive web dashboard using reack, nxtjs and tailwindcss styling with typescript types and fast charts rendering.",
                "estimated_hours": 20.0,
                "priority": "high",
                "complexity": "high",
                "deadline": "2026-11-14T18:00:00+02:00"
            },
            "execution_window": {"preset": "NEXT_WORKING_DAYS", "working_days": 5},
            "as_of": "2026-11-03T10:00:00+02:00"
        },
        "expected_dept": "Frontend & Mobile",
        "expected_skills": ["React", "Next.js", "Tailwind CSS", "TypeScript"],
    },
    {
        "id": "CASE_5_ODOO_ARABIC",
        "name": "Odoo Enterprise Customization in Arabic",
        "scenario": "Enterprise Arabic request: 'تعديل موديول المبيعات والفواتير في اودو 17 مع ربط بوستجرس'",
        "payload": {
            "task": {
                "title": "تعديل موديول المبيعات والفواتير في اودو 17",
                "description": "مطلوب مهندس اودو خبير يكتب نماذج بايثون مخصصة وتعديل شاشات xml في اودو 17 مع ربط بوستجرس وتطبيق دورات العمل لمحاسبة المبيعات.",
                "estimated_hours": 30.0,
                "priority": "high",
                "complexity": "high",
                "deadline": "2026-11-14T18:00:00+02:00"
            },
            "execution_window": {"preset": "NEXT_WORKING_DAYS", "working_days": 5},
            "as_of": "2026-11-03T10:00:00+02:00"
        },
        "expected_dept": "Odoo Implementation",
        "expected_skills": ["Odoo Development", "Python", "PostgreSQL"],
    },
    {
        "id": "CASE_6_QA_INFORMAL",
        "name": "QA Testing with Informal Slang & Typos",
        "scenario": "Informal testing request: 'playwrite and python automated regression tests with cicd'",
        "payload": {
            "task": {
                "title": "playwrite and python automated regression tests",
                "description": "we need automated e2e test suite written in playwrite and python with cicd pipeline integration to catch bugs before release.",
                "estimated_hours": 18.0,
                "priority": "high",
                "complexity": "medium",
                "deadline": "2026-11-14T18:00:00+02:00"
            },
            "execution_window": {"preset": "NEXT_WORKING_DAYS", "working_days": 5},
            "as_of": "2026-11-03T10:00:00+02:00"
        },
        "expected_dept": "Quality Assurance",
        "expected_skills": ["Playwright", "Python", "Test Automation", "CI/CD"],
    },
    {
        "id": "CASE_7_GARBAGE_INPUT",
        "name": "Complete Gibberish & Broken Garbage Input",
        "scenario": "Random non-sense text: 'asdf qwe 12345 !!! do something fast please urgent error'",
        "payload": {
            "task": {
                "title": "asdf qwe 12345 !!!",
                "description": "do something fast please urgent error nothing works",
                "estimated_hours": 10.0,
                "priority": "urgent",
                "complexity": "medium",
                "deadline": "2026-11-14T18:00:00+02:00"
            },
            "execution_window": {"preset": "NEXT_WORKING_DAYS", "working_days": 5},
            "as_of": "2026-11-03T10:00:00+02:00"
        },
        "expect_needs_input": True,
    },
    {
        "id": "CASE_8_IMPOSSIBLE_TECH",
        "name": "Non-Existent Fantasy Technology",
        "scenario": "Fantasy tech: 'Quantum Blockchain Smart Contract in Vyper Substrate'",
        "payload": {
            "task": {
                "title": "Quantum Blockchain Smart Contract in Vyper Substrate",
                "description": "Zero-knowledge zk-SNARK quantum entangled contract on non-standard substrate parachain.",
                "estimated_hours": 16.0,
                "priority": "medium",
                "complexity": "high",
                "deadline": "2026-11-14T18:00:00+02:00"
            },
            "execution_window": {"preset": "NEXT_WORKING_DAYS", "working_days": 5},
            "as_of": "2026-11-03T10:00:00+02:00"
        },
        "expect_needs_input": True,
    }
]


def run_robustness_battery():
    print("=" * 85)
    print("🧪 SPRINTMATCH AI AGENT - REAL-WORLD STRESS & ROBUSTNESS TEST BATTERY")
    print("=" * 85)
    print("Testing Agent resilience against: Heavy Typos, Slang, Arabic Prompts, Arabizi, and Garbage Inputs\n")

    passed_count = 0
    total_count = len(ROBUSTNESS_TEST_CASES)

    for i, tc in enumerate(ROBUSTNESS_TEST_CASES, 1):
        print(f"[{i}/{total_count}] ▶ Testing: {tc['name']}")
        print(f"      Scenario: {tc['scenario']}")
        print(f"      Title:    \"{tc['payload']['task']['title']}\"")
        
        t0 = time.perf_counter()
        req = urllib.request.Request(API_URL, data=json.dumps(tc["payload"]).encode("utf-8"), headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                data = json.loads(resp.read().decode("utf-8"))
                status = data.get("status")

                if tc.get("expect_needs_input"):
                    if status == "NEEDS_INPUT":
                        passed_count += 1
                        print(f"      ✅ RESULT: PASSED ({elapsed_ms}ms) -> Correctly identified missing/unresolvable information (NEEDS_INPUT)")
                        print(f"         Agent Rationale: \"{data.get('rationale', '')[:110]}...\"\n")
                    else:
                        print(f"      ❌ RESULT: FAILED -> Expected NEEDS_INPUT but got status: {status}\n")
                    continue

                if status == "SUCCESS":
                    cand = data.get("primary_candidate", {})
                    reqs = data.get("task_analysis", {}).get("requirements", {})
                    skills_found = [s["skill_name"] for s in reqs.get("required_skills", [])]
                    
                    cand_name = cand.get("name", "Unknown")
                    cand_code = cand.get("employee_code", "Unknown")
                    match_score = cand.get("suitability_score", 0.0)
                    trace_nodes = len(data.get("trace", []))

                    # Check skills coverage
                    expected_skills = tc.get("expected_skills", [])
                    skills_matched = any(any(es.lower() in sf.lower() for sf in skills_found) for es in expected_skills) if expected_skills else True

                    if skills_matched and cand_name != "Unknown":
                        passed_count += 1
                        print(f"      ✅ RESULT: PASSED ({elapsed_ms}ms)")
                        print(f"         ⭐ Recommended: [{cand_code}] {cand_name} (Rank 1)")
                        print(f"         🎯 Match Score:  {match_score}%")
                        print(f"         💡 Skills Extracted from Text: {skills_found}")
                        print(f"         ⚡ Pipeline Trace: {trace_nodes} Agent/Scoring nodes executed cleanly\n")
                    else:
                        print(f"      ⚠️ PARTIAL MATCH: Candidate: {cand_name}, Skills found: {skills_found}\n")
                else:
                    print(f"      ❌ RESULT: FAILED with status: {status}\n")
        except Exception as e:
            print(f"      ❌ REQUEST FAILED: {e}\n")

    print("=" * 85)
    print(f"📊 SUMMARY: {passed_count}/{total_count} CASES PASSED ({round(passed_count / total_count * 100, 1)}% SUCCESS RATE)")
    print("=" * 85)


if __name__ == "__main__":
    run_robustness_battery()
