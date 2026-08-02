import streamlit as st
from services.live_workflow_service import prepare_clinic_workflow
from services.outcome_learning_service import outcome_summary,record_outcome
from services.security_service import ROLE_PERMISSIONS,audit_event,audit_rows,mask_sensitive
from ui.patient_crm import show_patient_crm
def _money(x): return f"₹{float(x or 0):,.0f}"
def show_pilot_crm():
 show_patient_crm()
def show_live_workflows():
 st.markdown('<div class="eyebrow">PHASE 17 · LIVE CLINIC WORKFLOWS</div>',unsafe_allow_html=True); st.title("Live Workflow Pilot"); workflow=st.selectbox("Workflow",["Inactive patient recovery","Package renewal","Appointment reminders"]); st.caption("Only patients with recorded consent are included.")
 if st.button("Prepare workflow",type="primary"): result=prepare_clinic_workflow(workflow); st.success(f"Prepared for {result['audience']} patient(s)."); st.info(result["message"]); st.write("A task, approval and stored draft were created. Nothing was sent externally.")
def show_outcome_learning():
 st.markdown('<div class="eyebrow">PHASE 18 · CLOSED-LOOP LEARNING</div>',unsafe_allow_html=True); st.title("Outcome & Learning Center"); s=outcome_summary(); a,b,c=st.columns(3); a.metric("Measured actions",s["count"]); b.metric("Conversions",s["conversions"]); c.metric("Revenue recovered",_money(s["revenue"]))
 with st.form("outcome_form"):
  rec=st.text_input("Recommendation or campaign"); action=st.text_input("Action taken"); contacts=st.number_input("Contacts",min_value=0,value=0); conv=st.number_input("Conversions",min_value=0,value=0); rev=st.number_input("Revenue recovered",min_value=0.0,value=0.0,step=1000.0); notes=st.text_area("Notes")
  if st.form_submit_button("Record measured outcome"): record_outcome(rec,action,contacts,conv,rev,notes); audit_event("local-owner","record_outcome","campaign",rec); st.success("Outcome saved to Jarvis memory."); st.rerun()
 if s["rows"]: st.dataframe(s["rows"],use_container_width=True)
def show_security_center():
 st.markdown('<div class="eyebrow">PHASE 19 · SECURITY & ACCOUNTS</div>',unsafe_allow_html=True); st.title("Security & Account Foundation"); st.warning("Local role-and-audit foundation, not production authentication."); role=st.selectbox("Test role",list(ROLE_PERMISSIONS)); st.write("Permissions:"); [st.write(f"- {p}") for p in sorted(ROLE_PERMISSIONS[role])]; sample=st.text_input("Test masking","9876543210"); st.code(mask_sensitive(sample)); st.subheader("Audit log"); st.dataframe(audit_rows()[-100:],use_container_width=True)
def show_deployment_center():
 st.markdown('<div class="eyebrow">PHASE 20 · DEPLOYMENT & PILOT</div>',unsafe_allow_html=True); st.title("Deployment & Pilot Center"); checks={"Application starts locally":True,"Pilot CRM exists":True,"Approval-first workflows exist":True,"Outcome measurement exists":True,"Role and audit foundation exists":True,"Google Calendar connected":False,"Gmail connected":False,"WhatsApp Business connected":False,"Production authentication enabled":False,"Production database configured":False}
 for label,done in checks.items(): st.checkbox(label,value=done,disabled=True)
 st.subheader("Recommended pilot"); st.write("1. Import 20–50 consented patient records."); st.write("2. Run renewal and recovery workflows in draft mode."); st.write("3. Record outcomes for two weeks."); st.write("4. Review accuracy and duplicate tasks."); st.write("5. Connect one external channel after approval controls pass."); st.info("Dockerfile, Streamlit configuration and Render blueprint are included.")
