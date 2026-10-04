from .models import Project


def score_lead(project: Project, service: str, monthly_ad_budget: int | None) -> tuple[int, str, bool]:
    """Starter score. Manual qualification always stays with a person."""
    low_budget = service in {"TARGET", "COMPLEX"} and monthly_ad_budget is not None and monthly_ad_budget < project.minimum_ad_budget
    if monthly_ad_budget is None or service in {"CRM", "API"}:
        score = 40  # No ad-budget penalty for CRM or API only requests.
    elif monthly_ad_budget < 150_000:
        score = 0
    elif monthly_ad_budget < 300_000:
        score = 15
    elif monthly_ad_budget < 500_000:
        score = 25
    elif monthly_ad_budget < 1_000_000:
        score = 35
    else:
        score = 40
    priority = "HOT" if score >= 70 else "WARM" if score >= 40 else "COLD"
    if low_budget and priority == "HOT":
        priority = "WARM"
    return score, priority, low_budget
