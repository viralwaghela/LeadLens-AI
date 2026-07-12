# LeadLens AI

# Executive Intelligence Platform for Small Businesses

LeadLens AI is an AI-powered executive operating system designed to help founders and business owners gain complete visibility across their organization.

The platform consolidates information from multiple departments, stores organizational memory, tracks approvals, and provides AI-generated executive insights through a conversational interface.

---

## Problem Statement

Small and medium businesses often struggle with:

- Information scattered across departments
- Lack of executive visibility
- Manual reporting and status tracking
- Difficulty identifying priorities and risks
- No centralized business memory

LeadLens solves these challenges by acting as an AI Executive Assistant for business owners.

---

# Features

## Executive Dashboard
- Business health score
- Revenue, expenses, profit and margin tracking
- Company-wide executive overview

---

## AI Executive Assistant

Ask natural language questions such as:

```text
What happened today?
What approvals are pending?
What should I prioritize?
What are the biggest risks right now?
Summarize the business in one paragraph.
```

The AI analyzes business memory and generates executive-level responses.

---

## Executive Brief Automation

Automatically generates:

- Daily business summaries
- Key achievements
- Pending decisions
- Strategic recommendations

---

## Business Memory Engine

Persistent storage system for:

- Decisions
- Daily logs
- Approvals
- Reports
- Tasks
- Department activities

This allows LeadLens to understand historical context and answer business questions intelligently.

---

## Approval Workflow Engine

Track and manage:

- Marketing approvals
- Budget approvals
- Hiring approvals
- Business decisions

---

## Department Intelligence Modules

### Marketing
- Campaign tracking
- Content planning
- Lead generation initiatives

### Sales
- Corporate wellness campaigns
- Pipeline activities
- Outreach initiatives

### Finance
- Revenue tracking
- Expense monitoring
- Profitability reporting

### HR
- Hiring activities
- Employee initiatives
- Recruitment workflows

### Operations
- Daily business activities
- Process monitoring
- Organizational reporting

---

# Screenshots

## Executive Dashboard

![Dashboard](assets/screenshots/01_dashboard.png)

---

## AI Executive Assistant

![AI Assistant](assets/screenshots/04_ai_assistant.png)

---

## Executive Brief

![Executive Brief](assets/screenshots/02_executive_brief.png)

---

## Approval Workflow

![Approvals](assets/screenshots/03_approvals.png)

---

# Architecture

```text
Departments
     ↓
Business Memory Layer
     ↓
Executive Intelligence Engine
     ↓
AI Executive Assistant
     ↓
Executive Recommendations
```

---

# Project Structure

```text
agents/                Agent registration and routing
coo/                   COO workflows and notifications
core/                  Shared business utilities
executive/             Executive dashboard and AI logic
finance/               Finance intelligence modules
hr/                    HR workflows
marketing/             Marketing intelligence
operations/            Operations monitoring
sales/                 Sales workflows
services/              AI and utility services
database/              Business memory layer
generated/             Generated reports and exports
assets/                Screenshots and documentation
```

---

# Technology Stack

- Python
- Streamlit
- OpenRouter API
- JSON Business Memory System
- Modular AI Architecture

---

# Installation

### Clone Repository

```bash
git clone https://github.com/viralwaghela/LeadLens-AI.git
cd LeadLens-AI
```

### Create Virtual Environment

```bash
python -m venv venv
```

### Activate Environment

#### Windows

```bash
venv\Scripts\activate
```

#### Mac/Linux

```bash
source venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Configure Environment Variables

```

### Run Application

```bash
streamlit run app.py
```

---

# Example Questions

```text
What happened today?
What should I prioritize?
What approvals are pending?
Summarize the business.
What are the biggest risks?
```

---

# Current Status

## LeadLens v1.0 Complete

Implemented:

- Executive Dashboard
- AI Executive Assistant
- Business Memory Engine
- Approval System
- Executive Brief Automation
- Department Intelligence Modules

---

# Roadmap (V2)

Planned Features:

- Gmail Integration
- Google Calendar Integration
- Automated Daily Executive Briefs
- Claude-style AI Workspace
- Multi-Agent Collaboration
- Notification Center
- Database Migration (SQLite/PostgreSQL)
- Role-Based Access Control
- Advanced Analytics

---

# Security Notes

- `.env` files are excluded from version control.
- API keys should never be committed.
- Current business memory uses JSON and is intended for local usage.

---

# Author

## Viral Waghela

AI Automation | Executive Intelligence Systems | Business Process Automation

---

# Future Vision

LeadLens aims to become an AI-powered operating system for founders, capable of acting as an intelligent chief of staff by monitoring business activities, identifying risks, recommending actions and automating executive workflows.
