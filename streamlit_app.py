import streamlit as st
import json
import pandas as pd
from src.solution import handle_request
from src.data_access import load_requests

# Set up the page with a clean layout
st.set_page_config(page_title="Procurement Copilot", page_icon="🌿", layout="wide")

st.title("🌿 AI Procurement Copilot")
st.markdown("Intelligent Software Request Triage & Compliance")
st.markdown("---")

# Load requests
try:
    requests_data = load_requests()
    req_dict = {req["request_id"]: req for req in requests_data}
except Exception as e:
    st.error(f"Failed to load requests data: {e}")
    st.stop()

# Sidebar for selection to save vertical space
with st.sidebar:
    st.header("📥 Pending Requests")
    selected_req_id = st.selectbox("Select a request to review:", list(req_dict.keys()))
    
    if selected_req_id:
        st.markdown("### Request Details")
        req = req_dict[selected_req_id]
        cost = req.get('annual_cost_usd')
        cost_str = f"${cost:,.2f}" if cost is not None else "Unknown"
        
        st.info(f"""
**Product:** {req.get('product_name')} ({req.get('vendor_name')})  
**Cost:** {cost_str}  
**Category:** {req.get('category')}  
**Data Access:** {req.get('data_access_level')}  
        """)
        st.markdown("**Justification:**")
        st.caption(f"_{req.get('business_justification')}_")
        
        analyze_btn = st.button("🤖 Analyze Request", type="primary", use_container_width=True)

# Main area for results
if selected_req_id and 'analyze_btn' in locals() and analyze_btn:
    with st.spinner("Gathering evidence and checking policy..."):
        try:
            # Use the single agent architecture as the default production model
            decision = handle_request(selected_req_id, architecture="single")
            
            st.header("📋 Copilot Recommendation")
            
            # Use native Streamlit status boxes
            st.success(f"**Recommendation:** {decision.recommendation}")
            st.info(f"**Next Step:** {decision.next_step}")
            
            # Approvals & Risks using columns
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Required Approvals")
                if decision.required_approvals:
                    for app in decision.required_approvals:
                        st.markdown(f"✅ **{app}**")
                else:
                    st.markdown("None")
            
            with c2:
                st.subheader("Risk Flags")
                if decision.risk_flags:
                    for flag in decision.risk_flags:
                        st.markdown(f"⚠️ **{flag}**")
                else:
                    st.markdown("✅ Clear")
                    
            st.markdown("---")
            st.subheader("🔍 Gathered Evidence")
            for ev in decision.evidence:
                st.markdown(f"- **`{ev.source}`**: {ev.finding}")
                
        except Exception as e:
            st.error(f"Error analyzing request: {e}")
elif not ('analyze_btn' in locals() and analyze_btn):
    st.info("👈 Select a request from the sidebar and click 'Analyze Request' to see the policy evaluation.")
