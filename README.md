# LeadLens CareOS

LeadLens CareOS is an AI-powered multi-tenant business operating system for physiotherapy clinics, wellness businesses, and other small service-based practices.

**Jarvis is the product.** Jarvis acts as an AI Chief of Staff over a tenant-isolated CRM and automation layer, helping clinic owners understand operations, surface priorities, coordinate specialist AI agents, and execute approval-gated workflows across WhatsApp, Gmail, and Google Calendar.

LeadLens began as a single-clinic pilot and was subsequently migrated into a production-hardened multi-tenant SaaS foundation.

---

## Why LeadLens Exists

Small clinics often run core operations across disconnected tools:

- patient records
- appointments
- payments
- treatment packages
- follow-ups
- WhatsApp conversations
- spreadsheets
- calendars
- manual reminders
- ad-hoc reporting

LeadLens brings those workflows into one operating system where the CRM provides the business data and Jarvis uses that data to help the owner make decisions and automate repetitive work.

The goal is not to create an AI chatbot sitting beside the business.

The goal is to create an AI system that understands the business context and can safely help operate it.

---

## Core Product

LeadLens has two primary workspaces.

### CRM Workspace

The CRM manages the operational data Jarvis needs to understand the clinic.

It includes:

- patients
- appointments
- treatment packages
- payments
- therapists / practitioners
- leads
- follow-ups
- clinic settings
- operational signals

### Jarvis Workspace

Jarvis acts as the AI Chief of Staff.

It includes:

- Mission Control
- Patient Intelligence
- specialist AI agents
- automation workflows
- approval queue
- integration actions
- business memory
- reports
- operational insights

---

## High-Level Architecture

```mermaid
flowchart TD

    U[User]
    A[Authentication]
    M[Membership]
    O[Organization]
    T[TenantContext]

    U --> A
    A --> M
    M --> O
    O --> T

    T --> CRM[CRM]
    T --> J[Jarvis]
    T --> S[Scheduler]
    T --> AP[Approvals]
    T --> I[Integrations]
    T --> AU[Audit]

    CRM --> DB[(PostgreSQL)]
    J --> DB
    S --> DB
    AP --> DB
    I --> DB
    AU --> DB
