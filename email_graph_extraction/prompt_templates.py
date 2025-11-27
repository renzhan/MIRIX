"""
LLM提示词模板：指导LLM如何抽取和分类实体
"""

# 核心系统提示词：基于新Schema（Person, Company, Business, Department）
ENTITY_EXTRACTION_GUIDELINES = """
[Entity Type Classification - CRITICAL RULES]

You have 4 node types available: Person, Company, Business, Department

=== 1. Person ===
**When to create Person node:**
- Individual employees (internal or external): "John Smith", "Alice Wang"
- Email senders/recipients with personal names

**Person Properties:**
- name: Full name
- email: Email address (if available)
- title: Job title (e.g. "Sales Manager", "CEO")
- department: Department name mentioned in signature (e.g. "IT Department")
- role: MUST be one of:
  * "self" - the user themselves (check "My Email Account")
  * "colleague" - internal colleague (same company)
  * "customer_contact" - customer contact person
  * "vendor_contact" - vendor/supplier contact
  * "carrier_contact" - logistics/carrier contact
  * "partner_contact" - partner contact

**DO NOT create Person nodes for:**
- Departments or teams (use Department instead)
- Group/functional mailboxes (use Department instead)

=== 2. Department ===
**When to create Department node:**
- Internal departments: "HR Department", "Finance Dept", "IT Team", "Operations Team"
- Group/functional mailboxes: hr@item.com, support@unisco.com, ops@company.com
- Team names: "Customer Support", "Sales Team", "Warehouse Team"

**Department Properties:**
- name: Department/team name (e.g. "Customer Support", "HR Department")
- company_name: Parent company name (if mentioned)
- is_internal: true if internal department, false if external

**Group Mailbox Keywords (trigger Department):**
hr, humanresources, finance, accounting, ops, operations, support, help, helpdesk, 
it, tech, sales, info, contact, team, dept, department, admin, service, marketing, legal

=== 3. Company ===
**When to create Company node:**
- Real legal entities: "ABC Logistics Co., Ltd.", "XYZ Trading Inc."
- Companies explicitly mentioned: customers, vendors, carriers, partners, competitors

**Company Properties:**
- name: Company name
- company_type: MUST be one of: supplier, carrier, customer, partner, competitor, internal
- location: Location/address (if mentioned)
- country: Country name (if mentioned)
- region: Region (if mentioned)

**Examples:**
✅ "UNIS Transportation" → Company (carrier)
✅ "Honey Stinger" → Company (customer)
✅ "Walmart" → Company (customer)
❌ "Customer Support" → Department (not Company)
❌ "IT Department" → Department (not Company)

=== 4. Business ===
**When to create Business node:**
- Tickets: "Ticket #12345", "C1425289"
- Projects: "Q4 Migration Project"
- Orders: "PO-2025-001", "Order #789"
- Services: "Email Support Service"

**Business Properties:**
- name: Business identifier (ticket#, project name, order#)
- description: Brief description
- business_type: MUST be one of: ticket, support, project, order, service

=== Relationship Extraction Rules ===

**Person Relationships:**
- Person -[WORKS_AT]-> Company: when text says "John works at ABC Corp"
- Person -[BELONGS_TO_DEPARTMENT]-> Department: when signature shows department
- Person -[MANAGES]-> Person: when text mentions hierarchy ("my manager", "reports to")
- Person -[REPORTS_TO]-> Person: explicit reporting relationship
- Person -[COLLABORATES_WITH]-> Person: ONLY if text shows interaction ("working with", "helping")
- Person -[INVOLVED_IN]-> Business: actively working on ticket/project
- Person -[REQUESTS]-> Business: requesting help/service

**Department Relationships:**
- Department -[PART_OF_COMPANY]-> Company: department belongs to company
- Department -[DEPARTMENT_INVOLVED_IN]-> Business: department handling business

**Company Relationships:**
- Company -[OPERATES]-> Business: company runs/owns the business
- Company -[PARTNERS_WITH]-> Company: partnership mentioned
- Company -[SUPPLIES_TO]-> Company: supplier relationship
- Company -[CARRIES_FOR]-> Company: carrier relationship

**Business Relationships:**
- Business -[RELATED_TO]-> Business: related tickets/projects
- Business -[DEPENDS_ON]-> Business: dependency mentioned

=== Key Extraction Guidelines ===

1. **Internal Domains** (for role/is_internal inference):
   - item.com, unisco.com

2. **Role Assignment:**
   - Check "My Email Account" to identify "self"
   - Same domain as user → "colleague"
   - Customer companies → "customer_contact"
   - Vendor/supplier → "vendor_contact"
   - Carrier → "carrier_contact"

3. **Content-Driven Only:**
   - Extract relationships ONLY when explicitly mentioned in email body
   - Do NOT infer relationships from email headers alone
   - Focus on business context and explicit interactions

4. **Attribute Completeness:**
   - Always fill role/company_type/business_type when creating nodes
   - Extract email addresses when available
   - Include job titles from signatures

[Goal]
Extract a precise knowledge graph with clear entity type separation.
Use Department for teams/group mailboxes, not Company or Person.
"""
