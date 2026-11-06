from decimal import Decimal

PROJECTS = {
    "P1001": {"project_id": "P1001", "name": "星河 / 办公数字化", "budget": "1000000.00", "spent": "650000.00", "committed": "200000.00", "source_version": 1},
    "P2002": {"project_id": "P2002", "name": "星河 / 仓储改造", "budget": "800000.00", "spent": "300000.00", "committed": "0.00", "source_version": 1},
}


def get_budget(project_id: str) -> dict:
    if project_id not in PROJECTS:
        raise ValueError("项目不存在，请选择 P1001 或 P2002")
    result = dict(PROJECTS[project_id])
    result["available"] = str(Decimal(result["budget"]) - Decimal(result["spent"]) - Decimal(result["committed"]))
    return result


def calculate_gap(project_id: str, amount: str) -> dict:
    budget = get_budget(project_id)
    amount = Decimal(amount)
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError("金额必须为正数且最多两位小数")
    return {"project_id": project_id, "amount": str(amount), "available": budget["available"],
            "gap": str(max(Decimal("0.00"), amount - Decimal(budget["available"]))),
            "source_version": budget["source_version"]}
