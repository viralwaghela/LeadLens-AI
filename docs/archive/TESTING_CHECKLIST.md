# LeadLens v1 Bug Testing Checklist

## 1. App Startup
- [ ] App starts without terminal errors
- [ ] Dashboard loads correctly
- [ ] All tabs are visible

## 2. Onboarding
- [ ] Existing company loads correctly
- [ ] Company data appears on Business tab
- [ ] No onboarding screen appears after company setup

## 3. Executive Dashboard
- [ ] Health score displays
- [ ] Financial overview displays
- [ ] Department snapshot displays
- [ ] Pending approvals display

## 4. CEO Command Center
- [ ] Business update can be submitted
- [ ] AI response is generated
- [ ] Output saves to memory

## 5. COO
- [ ] Start My Business Day works
- [ ] COO plan generates
- [ ] Save COO plan works
- [ ] Tasks/reports/logs update

## 6. Marketing
- [ ] Campaign history displays
- [ ] Campaign opens
- [ ] Strategy displays
- [ ] Calendar displays
- [ ] Reels display
- [ ] Captions display
- [ ] Downloads work

## 7. Sales
- [ ] Campaign history displays
- [ ] Campaign opens
- [ ] Strategy displays
- [ ] Emails display
- [ ] WhatsApp displays
- [ ] Proposal displays
- [ ] Downloads work

## 8. Finance
- [ ] Report history displays
- [ ] Report opens
- [ ] Summary displays
- [ ] Expenses display
- [ ] Forecast displays
- [ ] Downloads work

## 9. HR
- [ ] Package history displays
- [ ] Package opens
- [ ] Job description displays
- [ ] Interview questions display
- [ ] Onboarding displays
- [ ] Downloads work

## 10. Operations
- [ ] Package history displays
- [ ] Package opens
- [ ] Daily plan displays
- [ ] Assignments display
- [ ] Risks display
- [ ] Downloads work

## 11. Notifications
- [ ] Notifications display
- [ ] Success notifications appear
- [ ] Error notifications appear if generation fails

## 12. Activity
- [ ] Activity timeline displays
- [ ] New agent activity appears after test runs

## 13. Memory
- [ ] Memory dashboard opens
- [ ] Tasks display
- [ ] Decisions display
- [ ] Approvals display
- [ ] Daily logs display

## 14. Generated Files
- [ ] generated/marketing contains files
- [ ] generated/sales contains files
- [ ] generated/finance contains files
- [ ] generated/hr contains files
- [ ] generated/operations contains files

## 15. Clinic CRM
- [ ] Patient Records opens without errors
- [ ] Patient can be added with a stable patient ID
- [ ] Patient search and status filters work
- [ ] Patient profile shows linked appointments, packages and payments
- [ ] Patient details and consent can be updated
- [ ] Patient archiving requires confirmation and removes the record from active lists
- [ ] Appointment can be scheduled only for an existing patient
- [ ] Appointment status can be updated
- [ ] Package can be assigned only to an existing patient
- [ ] Package sessions remaining and status can be updated
- [ ] Payment can be recorded only for an existing patient
- [ ] Therapist can be added and capacity can be updated
- [ ] CRM Insights uses saved CRM records rather than manual demo inputs
- [ ] Patient names, phone numbers and emails do not appear in Jarvis context
- [ ] Clinic workflow candidates require consent
- [ ] Preparing a clinic workflow creates an approval and sends nothing externally

### CRM automated check

```powershell
python test_clinic_crm.py
```

## Bugs Found
| Bug | Page | Error | Fixed |
|---|---|---|---|
| | | | |
