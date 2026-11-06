"""OpenDART account matching helpers.

Keep account selection fail-closed: exact taxonomy/local-name matches are allowed,
while broad substring matches such as ``AdjustmentsForOtherRevenue`` are not.
"""

from __future__ import annotations

from typing import Any


REVENUE_ACCOUNT_IDS = {
    "ifrs-full_Revenue",
    "ifrs-full_RevenueFromContractsWithCustomers",
    "ifrs-full_SalesRevenueGoods",
    "ifrs-full_SalesRevenueServices",
    "dart_Revenue",
    "dart_SalesRevenue",
}

REVENUE_LOCAL_IDS = {
    "Revenue",
    "RevenueFromContractsWithCustomers",
    "SalesRevenue",
    "SalesRevenueGoods",
    "SalesRevenueServices",
}

REVENUE_ACCOUNT_NAMES = {
    "매출액",
    "수익(매출액)",
    "매출",
    "영업수익",
    "매출액(수익)",
}


def _compact_name(value: Any) -> str:
    return str(value or "").strip().replace(" ", "")


def is_revenue_account(account_id: Any, account_name: Any) -> bool:
    """Return True only for a canonical revenue account.

    Company-extension taxonomy IDs such as ``company_Revenue`` are accepted by
    their exact local token. IDs that merely *contain* ``Revenue`` are rejected,
    preventing adjustment/other-revenue rows from shadowing the real sales row.
    """
    acc_id = str(account_id or "").strip()
    acc_name = _compact_name(account_name)
    local_id = acc_id.rsplit("_", 1)[-1] if "_" in acc_id else acc_id

    return (
        acc_id in REVENUE_ACCOUNT_IDS
        or local_id in REVENUE_LOCAL_IDS
        or acc_name in REVENUE_ACCOUNT_NAMES
    )
