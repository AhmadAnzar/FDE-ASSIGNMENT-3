from __future__ import annotations

import json
import httpx
from src.data_access import load_budgets, load_software_catalog, load_vendors

def check_budget(department: str, requested_amount: float) -> str:
    """Check if the requested amount is within the department's available budget."""
    try:
        budgets_df = load_budgets()
        dept_budget = budgets_df[budgets_df['department'].str.lower() == department.lower()]
        if dept_budget.empty:
            return json.dumps({"error": f"Department '{department}' not found in budget records."})
        
        available = float(dept_budget.iloc[0]['available_software_budget'])
        sufficient = requested_amount <= available
        return json.dumps({
            "department": department,
            "requested_amount": requested_amount,
            "available_budget": available,
            "budget_sufficient": sufficient,
            "finding": f"Requested ${requested_amount:,.2f} vs Available ${available:,.2f}."
        })
    except Exception as e:
        return json.dumps({"error": str(e)})

def calculate_approval_thresholds(annual_amount: float) -> str:
    """Calculate required minimum business approvals based on the annual amount."""
    try:
        if annual_amount <= 1000:
            approvals = ["Manager"]
        elif annual_amount <= 10000:
            approvals = ["Department Head", "Procurement"]
        elif annual_amount <= 25000:
            approvals = ["Department Head", "Finance", "Procurement"]
        else:
            approvals = ["Department Head", "Finance", "CFO", "Procurement"]
            
        return json.dumps({
            "annual_amount": annual_amount,
            "minimum_approvals": approvals
        })
    except Exception as e:
        return json.dumps({"error": str(e)})

def check_software_catalog(product_name: str = "", vendor_name: str = "", category: str = "") -> str:
    """Check the software catalog for existing tools that might overlap with the request."""
    try:
        catalog_df = load_software_catalog()
        matches = []
        
        for _, row in catalog_df.iterrows():
            overlap = False
            reasons = []
            if product_name and product_name.lower() in str(row['product_name']).lower():
                overlap = True
                reasons.append("product_name match")
            if vendor_name and vendor_name.lower() in str(row['vendor']).lower():
                overlap = True
                reasons.append("vendor match")
            if category and category.lower() in str(row['category']).lower():
                overlap = True
                reasons.append("category match")
                
            if overlap:
                matches.append({
                    "product": str(row['product_name']),
                    "vendor": str(row['vendor']),
                    "category": str(row['category']),
                    "status": str(row['status']),
                    "overlap_reasons": reasons
                })
                
        return json.dumps({"matches_found": len(matches), "matches": matches})
    except Exception as e:
        return json.dumps({"error": str(e)})

def check_vendor_risk_api(vendor_name: str) -> str:
    """Call the mock vendor risk API to get security and risk information about a vendor."""
    try:
        # According to run_local.py, the mock API is on port 8001
        url = f"http://127.0.0.1:8001/vendor-risk/{vendor_name}"
        response = httpx.get(url, timeout=2.0)
        if response.status_code == 200:
            return json.dumps(response.json())
        elif response.status_code == 404:
            return json.dumps({"vendor_name": vendor_name, "status": "not_found", "message": "No vendor-risk record found."})
        else:
            return json.dumps({"vendor_name": vendor_name, "status": "error", "message": f"API Error {response.status_code}: {response.text}"})
    except httpx.RequestError as e:
        return json.dumps({"vendor_name": vendor_name, "status": "unavailable", "message": "Vendor-risk service unavailable or timed out."})
    except Exception as e:
        return json.dumps({"error": str(e)})

def check_internal_vendor_registry(vendor_name: str) -> str:
    """Check the internal vendors.csv registry for vendor status."""
    try:
        vendors_df = load_vendors()
        vendor = vendors_df[vendors_df['vendor_name'].str.lower() == vendor_name.lower()]
        if vendor.empty:
            return json.dumps({"vendor_name": vendor_name, "status": "not_found", "message": "Not found in internal registry."})
        
        row = vendor.iloc[0]
        return json.dumps({
            "vendor_name": str(row['vendor_name']),
            "category": str(row['category']),
            "internal_status": str(row['status'])
        })
    except Exception as e:
        return json.dumps({"error": str(e)})
