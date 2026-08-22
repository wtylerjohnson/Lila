# Varonis frame re-screen · work order A1 · 2026-08-17

GENERATED from data/state/frame_rescreen/varonis.rescreen.json; not hand-authored.
Screen: tools/frame_rescreen.py. Frame: clients/varonis/frame_tiers.json (operator-tiered buyer language).
Keep rule: tier 1 or tier 2 sentence match only; tier 3 is context and never qualifies alone.

## What ran

- Notice store: 358,540 notices (last ingest 2026-08-17), all scanned; guard ladder = term_tiers (tier 2 phrases need a tech code, tier 3 single words need a tech code and a domain anchor), entity guard = sam_lanes.reject_entity_hit, leverage and family dedupe = notice_join. Zero LLM, zero metered quota.
- Forecast stores: dhs_apfs 967 records, acquisition_gateway 7,654 records; same tiered screen.
- Frame surfaces rewritten this session: profile core = 26 tier 2 terms, adjacent = 4 tier 3 terms; taxonomy v2 (modes: acronym context for DLP/CUI/DSPM/M365); review packet amended through amend_terms (revision 0 to 1, 11 old capability/technology terms restorably in kept_out, 11 procurement-lens keywords untouched).

## The frame delta, measured

Old frame raw store yield (string hits before any guard; the titles were the receipt):

| old term | raw hits |
|---|---|
| data security platform | 0 |
| data security posture management | 1 |
| data discovery and classification | 0 |
| data access governance | 0 |
| insider threat detection | 0 |
| data loss prevention | 10 |
| database activity monitoring | 0 |
| AI security | 1 |
| FedRAMP Moderate | 77 |
| Zero Trust data security | 0 |
| OMB M-21-31 logging | 0 |

8 of 11 old terms matched zero of 358,540 notices. 77 of the 89 raw hits are the compliance attribute 'FedRAMP Moderate', not demand language. The buyer-language frame keeps 177 records after guards:

- kept 177 (by leverage rank: 77 shape-it, 38 door-open, 47 bid-now, 15 award evidence)
- 71 tier-3-only records dropped by the keep rule; 83 family duplicates collapsed; 3,179 records carried frame vocabulary at all
- ladder guard receipts: {"negative context 'substation'": 7}

Term yield on kept records (count is the index, the sentences in the artifact are the receipt):

| term | kept records |
|---|---|
| records management | 81 |
| CUI | 78 |
| Microsoft 365 | 40 |
| SharePoint | 38 |
| OneDrive | 13 |
| M365 | 11 |
| least privilege | 9 |
| DLP | 9 |
| data loss prevention | 9 |
| zero trust | 6 |
| insider threat | 6 |
| Copilot | 6 |
| user activity monitoring | 5 |
| unstructured data | 5 |
| data tagging | 5 |
| event logging | 4 |
| data classification | 3 |
| Netwrix | 3 |
| M-21-31 | 3 |
| Everfox | 3 |
| Microsoft Purview | 2 |
| cloud security | 1 |
| Varonis | 1 |
| Forcepoint | 1 |
| insider risk | 1 |
| data security posture | 1 |

## Bare Purview measurement (the rival name that is an English word)

- 42 notices carry bare 'purview'; classifier reads 30 as the idiom and 12 as unclassified, and every sampled unclassified row is a possessive or mojibake-apostrophe idiom form ('DLA's purview'). Zero product references found. Bare 'purview' never qualifies a keep; vendor-qualified 'Microsoft Purview' rides the entity route. Three witnessed collisions recorded in the polysemy corpus with notice ids.

## Tier 1 rows (8 kept)

- [Award Notice | rank 4 | due no deadline] DEPT OF DEFENSE · Varonis · Award Notice for Varonis Software
  - TITLE: Award Notice for Varonis Software
  - https://sam.gov/opp/0cea1f05ce4b4d08a418c3732d095303/view
- [Award Notice | rank 4 | due no deadline] ENERGY, DEPARTMENT OF · Forcepoint · 7A--Information Technology Purchase for Portsmouth Pad
  - Information Technology Purchase for Portsmouth Paducah Project Office -Forcepoint
  - https://sam.gov/opp/f9cde27acc5e44da8e3f8e0a53dad694/view
- [Award Notice | rank 4 | due no deadline] VETERANS AFFAIRS, DEPARTMENT OF · Netwrix · DJ10--SD-NetWrix SW-Re-compete (VA-26-00012587)
  - TITLE: DJ10--SD-NetWrix SW-Re-compete (VA-26-00012587)
  - https://sam.gov/opp/4fb0a18ba38c40f18361834b2bb1b8ab/view
- [Award Notice | rank 4 | due no deadline] INTERIOR, DEPARTMENT OF THE · Netwrix · D--Netwrix Change Tracker Support & Maintenance 1 Yea
  - TITLE: D--Netwrix Change Tracker Support & Maintenance 1 Yea
  - https://sam.gov/opp/c3f26bdcf9714739bada040a11d54223/view
- [Sources Sought | rank 1 | due 2026-05-29] DEPT OF DEFENSE · Everfox · Insider Threat Monitoring Tool
  - TITLE: Insider Threat Monitoring Tool
  - https://sam.gov/opp/fdbb698a3d8145e7ab27cab03378ce33/view
- [Sources Sought | rank 1 | due 2026-06-10] DEPT OF DEFENSE · Netwrix · Netwrix Data Classification Licensing and Sustainment
  - TITLE: Netwrix Data Classification Licensing and Sustainment
  - https://sam.gov/opp/7ee6bce34c394b769505cca2af6194e3/view
- [Sources Sought | rank 1 | due 2026-07-15] DEPT OF DEFENSE · Everfox · H92403-Everfox
  - TITLE: H92403-Everfox
  - https://sam.gov/opp/8d8ba8a404d34e5c94777017622a809b/view
- [Presolicitation | rank 2 | due 2026-08-26] DEPT OF DEFENSE · Everfox · Notice of Intent to Sole Source - Insider Threat Monitoring Tool
  - TITLE: Notice of Intent to Sole Source - Insider Threat Monitoring Tool
  - https://sam.gov/opp/99a23ebb700244c5bf3bf9f3909f2193/view

## Forecast re-screen, every wired source

- acquisition_gateway: 7654 records, 14 kept
- dhs_apfs: 967 records, 10 kept
- Registered adapters with NO store in this checkout (never pulled here, honestly not screened): army_acquisition_forecast, ed_forecast, hhs_sbcx, hud_forecast, nasa_naf, navy_nawcad_lraf, navy_nawcwd_lraf, sec_procurement_forecast, state_forecast. Filling them is a pull, not a screen; run the forecast refresh through the Command Center.

1. [dhs_apfs] DHS / DHS HQ/MGMT · solicitation 05/29/2026 · tier 2 (CUI) · DHS Network Operations Security Center (NOSC) Network, Cloud, and Cyber Services (NCCS) 2.0
   - ... t and incident management, and incident response and recovery for the DHS Onenet at all information processing and classification levels – open source, SBU and CUI, Classified (Secret and Top Secret), Sensitive Compartmented Information, and Special Access Program information. ...
   - https://apfs-cloud.dhs.gov/record/73416/public-print/
2. [dhs_apfs] DHS / TSA · solicitation 06/01/2026 · tier 2 (M365) · Cloud Service for Microsoft Azure and M365
   - TITLE: Cloud Service for Microsoft Azure and M365
   - https://apfs-cloud.dhs.gov/record/71407/public-print/
3. [dhs_apfs] DHS / USCG/CG-HCA · solicitation 06/29/2026 · tier 2 (Microsoft 365) · USCG Financial Service Center (FSC) Automation and Communication Support
   - This involves the full lifecycle of automation, from analyzing current financial accounting processes to designing and developing new automated solutions using tools such as the Microsoft 365 Power Platform, UiPath, and SharePoint.
   - https://apfs-cloud.dhs.gov/record/73481/public-print/
4. [dhs_apfs] DHS / FEMA · solicitation 07/10/2026 · tier 2 (records management) · Cloud Based Human Capital System
   - Integrated Employee Self-Service Portal: A portal allowing users to submit and track personnel and system-related inquiries through a case management system, including inquiries related to payroll, compensation, classification, and records management.
   - https://apfs-cloud.dhs.gov/forecast/72164
5. [dhs_apfs] DHS / CBP · solicitation 07/17/2026 · tier 2 (CUI) · MGD Project Management and Strategy Support
   - All personnel must undergo a background investigation and comply with DHS/CBP security and privacy policies, including handling Controlled Unclassified Information (CUI) and Personally Identifiable Information (PII).
   - https://apfs-cloud.dhs.gov/record/73812/public-print/
6. [dhs_apfs] DHS / CBP/OT · solicitation 07/28/2026 · tier 2 (insider threat) · Digital Guardian
   - This procurement will serve to provide the annual maintenance for previously purchase software licenses and support (technical and professional) for Digital Guardian providing insider threat identity, monitoring and investigatory capabilities within the CBP enterprise.
   - https://apfs-cloud.dhs.gov/record/72595/public-print/
7. [dhs_apfs] DHS / DHS HQ/CISA · solicitation 07/31/2026 · tier 2 (SharePoint) · Homeland Security Infrastructure Security (HSIN) Critical Infrastructure Information Sharing Environment Support Services
   - ... SE) to include: program management, stakeholder engagement, platform management, technical assistance for users and HSIN-CI Program office staff, site design / SharePoint design and architecture and maintenance support, along with technical project management, technical consulting, facilitation and documentation suppor ...
   - https://apfs-cloud.dhs.gov/record/71523/public-print/
8. [dhs_apfs] DHS / CBP/Office of Technology · solicitation 08/18/2026 · tier 1 (Varonis) · Varonis Atlas
   - TITLE: Varonis Atlas
   - https://apfs-cloud.dhs.gov/record/74623/public-print/
9. [dhs_apfs] DHS / ICE/M&A/CIO · solicitation 08/25/2026 · tier 2 (SharePoint) · Office of the Principal Legal Advisor (OPLA) Case Management System (OCMS)
   - The Contractor shall also maintain and enhance existing OCMS features, provide Tier II and III technical support, deliver UI/UX unification, and ensure timely server upgrades and SharePoint administration.
   - https://apfs-cloud.dhs.gov/record/74574/public-print/
10. [dhs_apfs] DHS / USCIS · solicitation 09/25/2026 · tier 2 (Microsoft 365) · Microsoft 365 Management, Operational Support, and Enhancements (MOSE)
   - TITLE: Microsoft 365 Management, Operational Support, and Enhancements (MOSE)
   - https://apfs-cloud.dhs.gov/record/73690/public-print/
11. [acquisition_gateway] Department of Transportation · solicitation window unstated · tier 1 (Varonis) · Varonis Systems Software Support
   - TITLE: Varonis Systems Software Support
   - https://acquisitiongateway.gov/forecast/resources/37070?nid=37070
12. [acquisition_gateway] Department of Transportation · solicitation window unstated · tier 1 (Varonis) · Varonis Systems Software Support
   - TITLE: Varonis Systems Software Support
   - https://acquisitiongateway.gov/forecast/resources/37080?nid=37080
13. [acquisition_gateway] General Services Administration · solicitation window unstated · tier 1 (Varonis) · FY25 Varonis Renewal
   - TITLE: FY25 Varonis Renewal
   - https://acquisitiongateway.gov/forecast/resources/36608?nid=36608
14. [acquisition_gateway] Department of Veterans Affairs · solicitation window unstated · tier 2 (data loss prevention) · Data Loss Prevention (DLP) Infrastructure Solution - New Requirement
   - TITLE: Data Loss Prevention (DLP) Infrastructure Solution - New Requirement
   - https://acquisitiongateway.gov/forecast/resources/41559?nid=41559
15. [acquisition_gateway] Department of Veterans Affairs · solicitation window unstated · tier 2 (records management) · Records Management Software
   - TITLE: Records Management Software
   - https://acquisitiongateway.gov/forecast/resources/42143?nid=42143
16. [acquisition_gateway] Department of the Interior · solicitation window unstated · tier 2 (Microsoft 365) · IR Microsoft 365 (Power Platform / .NET) Development
   - TITLE: IR Microsoft 365 (Power Platform / .NET) Development
   - https://acquisitiongateway.gov/forecast/resources/38863?nid=38863
17. [acquisition_gateway] Department of Transportation · solicitation window unstated · tier 2 (SharePoint) · Dev Ops System
   - ... The 'DevOps' provides operation and maintenance, System Administration, SharePoint Administration/Development, Dynamics 365 Development/Administration, Software Development, Mobile Application Development, Web Based Application Development, Script Writing, System Analysis, Database Administration, Business Process Reen ...
   - https://acquisitiongateway.gov/forecast/resources/37238?nid=37238
18. [acquisition_gateway] General Services Administration · solicitation window unstated · tier 2 (records management) · Alfresco Software Renewal
   - It offers enterprise document and records management functionalities for 'finished' GSA documents declared as official records.
   - https://acquisitiongateway.gov/forecast/resources/39637?nid=39637
19. [acquisition_gateway] Department of the Interior · solicitation window unstated · tier 2 (Microsoft 365) · Microsoft 365 Software
   - TITLE: Microsoft 365 Software
   - https://acquisitiongateway.gov/forecast/resources/37699?nid=37699
20. [acquisition_gateway] Department of the Interior · solicitation window unstated · tier 2 (records management) · Digitization Units
   - UII 010-000002837 - Digitization Units and licenses at the AIRR, part of the Digital Center of Excellence, Electronic Records Management Program
   - https://acquisitiongateway.gov/forecast/resources/39397?nid=39397
21. [acquisition_gateway] Department of Agriculture · solicitation window unstated · tier 2 (SharePoint) · FY26 USDA OCIO Microsoft Enterprise Software Agreement.
   - This includes 98000 Microsoft G5 licenses email SharePoint Windows Server licenses Microsoft SQL Server Teams calling lines CoPilot Microsoft Power Apps GitHub licenses.
   - https://acquisitiongateway.gov/forecast/resources/43095?nid=43095
22. [acquisition_gateway] Department of Agriculture · solicitation window unstated · tier 2 (unstructured data) · USDA C3ai (placed by OCP)
   - The project aims to enhance FSISs riskbased decision-making by improving the usefulness of risk indicators through expanded context from unstructured data and real-time SME feedback.
   - https://acquisitiongateway.gov/forecast/resources/46736?nid=46736
23. [acquisition_gateway] Department of Agriculture · solicitation window unstated · tier 2 (Microsoft 365) · PowerApps Operations and Maintenance Support
   - Support is required for several applications created in the Microsoft 365 Power Platform including all aspects of the SharePoint/Office 365 environments customizations development availability reliability performance monitoring security understanding event-driven system-oriented architecture.
   - https://acquisitiongateway.gov/forecast/resources/47730?nid=47730
24. [acquisition_gateway] Department of the Interior · solicitation window unstated · tier 2 (SharePoint) · SCAS III - Collaboration/eData Center/SharePoint support
   - TITLE: SCAS III - Collaboration/eData Center/SharePoint support
   - https://acquisitiongateway.gov/forecast/resources/40622?nid=40622

## Top 100 solicitable by leverage (live windows first; awards excluded)

1. [L1 shape-it | Sources Sought | due 2026-08-25] FEDERAL RETIREMENT THRIFT INVESTMENT BOARD · FOIA, eDiscovery, and Matter Management Solution(s) - Request for Information (RFI)
   - tier 2 (Microsoft 365, Microsoft Purview, records management)
   - FRTIB seeks information regarding integrated platforms, modular solutions, or interoperable technologies that can improve operational efficiency, reduce manual processes, enhance compliance and auditability, strengthen reporting and analytics, and integrate with the agency's Microsoft 365 environment.
   - https://sam.gov/opp/b2a14fde343545228daa4ef01279e0d4/view
2. [L1 shape-it | Sources Sought | due 2026-09-15] INTERNATIONAL TRADE COMMISSION, UNITED STATES (DUNS # 02-1877998) · REQUEST FOR INFORMATION (RFI) - Professional Integration Services for Enterprise Network Monitoring System Upgrade
   - tier 2 (CUI, least privilege)
   - Within the ITCNET, USITC maintains a large amount of Controlled Unclassified Information (CUI).
   - https://sam.gov/opp/707be0a96a5f4286aec99f3607196caf/view
3. [L2 door-open | Presolicitation | due 2026-08-26] DEPT OF DEFENSE · Notice of Intent to Sole Source - Insider Threat Monitoring Tool
   - tier 2 (Everfox, insider threat, user activity monitoring)
   - TITLE: Notice of Intent to Sole Source - Insider Threat Monitoring Tool
   - https://sam.gov/opp/99a23ebb700244c5bf3bf9f3909f2193/view
4. [L2 door-open | Special Notice | due 2028-02-08] DEPT OF DEFENSE · AFLCMC Data Operations Commercial Solutions Opening
   - tier 2 (CUI, least privilege, zero trust)
   - AFLCMC desires strong identity verification, validation of device compliance before granting access and ensuring least privilege access.
   - https://sam.gov/opp/e29fa52f046d4330a85d0798d3c1e3d0/view
5. [L2 door-open | Special Notice | due 2026-08-31] DEPT OF DEFENSE · INDUSTRY DAY FOR GROUP 1 AND GROUP 2 UNMANNED AIRCRAFT SYSTEM (UAS) TRAINING, MISSION PLANNING, MISSION REHEARSAL, SIMULATION, AND QUALIFICATION MANAGEMENT SYSTEMS
   - tier 2 (CUI)
   - ... ess Point of Contact (POC) Telephone Number Question(s) Questions shall not include proprietary, competition-sensitive, or Controlled Unclassified Information (CUI), as questions and corresponding Government responses may be addressed during the Industry Day or published in a consolidated Question and Answer (Q&A) docu ...
   - https://sam.gov/opp/6795b727e9ff40c39d7cd96683d1a1ac/view
6. [L2 door-open | Special Notice | due 2026-12-23] DEPT OF DEFENSE · Communications, Network, Engineering, Cybersecurity, and Information Technology Services (CNECTS), aka "Connects"
   - tier 2 (CUI)
   - All defense contractors must still comply with DFARS 252.204-7012, which requires the implementation of NIST SP 800-171 security controls to protect Controlled Unclassified Information (CUI).
   - https://sam.gov/opp/712561f65ecc42d3acac92b36adf422c/view
7. [L3 bid-now | Combined Synopsis/Solicitation | due 2026-08-19] DEPT OF DEFENSE · Court Reporter Equipment
   - tier 2 (Microsoft 365)
   - ... ormation (name, email and phone number), 2.2.3 UEI and CAGE codes 2.2.4 Tax identification number 2.3 The acceptable electronic format shall be compatible with Microsoft 365 or PDF Adobe. 2.4 All documents shall be labeled with the solicitation number (W912JB26QA053) and not be password protected. 2.5 The offeror shoul ...
   - https://sam.gov/opp/1911205f82c849efbe8b17a55b5539bf/view
8. [L3 bid-now | Combined Synopsis/Solicitation | due 2026-08-19] DEPT OF DEFENSE · Postal Scanner X-Ray
   - tier 2 (Microsoft 365)
   - ... ormation (name, email and phone number), 2.2.3 UEI and CAGE codes 2.2.4 Tax identification number 2.3 The acceptable electronic format shall be compatible with Microsoft 365 or PDF Adobe. 2.4 All documents shall be labeled with the solicitation number (W912JB26QA080) and not be password protected. 2.5 The offeror shoul ...
   - https://sam.gov/opp/6d1d8863f77640e4a30406d5f32050f1/view
9. [L3 bid-now | Combined Synopsis/Solicitation | due 2026-08-20] VETERANS AFFAIRS, DEPARTMENT OF · DG01--NextGEN Infrastructure Upgrades - COMM TEAM 2
   - tier 2 (records management)
   - ... stem security Attachment E Seasonal Influenza with Vaccines Attachment F VHA Directive 1061 Prevention of Healthcare Associated Legionella Disease Attachment G Records Management (End of Statement of Work) 4. WAGE DETERMINATION 4.1 Service Contract Labor Standards (SCA) Wage Determination Wage Determination No. 2015-41 ...
   - https://sam.gov/opp/02034df5778d4755b1825c9bd96b9f16/view
10. [L3 bid-now | Solicitation | due 2026-08-21] DEPT OF DEFENSE · Next Generation Surveillance Array (NGSA)
   - tier 2 (CUI)
   - The full version of this Request for Proposal (RFP) includes both Controlled Unclassified Information (CUI) and Controlled Technical Information (CTI) and will not be made publicly available.
   - https://sam.gov/opp/1a8ad238316249858c5d83b85b601a40/view
11. [L3 bid-now | Combined Synopsis/Solicitation | due 2026-08-27] DEPT OF DEFENSE · Mail Security Scanner
   - tier 2 (Microsoft 365)
   - ... ormation (name, email and phone number), 2.2.3 UEI and CAGE codes 2.2.4 Tax identification number 2.3 The acceptable electronic format shall be compatible with Microsoft 365 or PDF Adobe. 2.4 All documents shall be labeled with the solicitation number (W912JB26QA080) and not be password protected. 2.5 The offeror shoul ...
   - https://sam.gov/opp/c4f56d3a65884b16888d062acc05b37e/view
12. [L1 shape-it | Sources Sought | due 2026-05-29] DEPT OF DEFENSE · Insider Threat Monitoring Tool
   - tier 2 (Everfox, insider threat, user activity monitoring)
   - TITLE: Insider Threat Monitoring Tool
   - https://sam.gov/opp/fdbb698a3d8145e7ab27cab03378ce33/view
13. [L1 shape-it | Sources Sought | due 2026-06-10] DEPT OF DEFENSE · Netwrix Data Classification Licensing and Sustainment
   - tier 1 (Netwrix, data classification)
   - TITLE: Netwrix Data Classification Licensing and Sustainment
   - https://sam.gov/opp/7ee6bce34c394b769505cca2af6194e3/view
14. [L1 shape-it | Sources Sought | due 2026-07-15] DEPT OF DEFENSE · H92403-Everfox
   - tier 1 (Everfox)
   - TITLE: H92403-Everfox
   - https://sam.gov/opp/8d8ba8a404d34e5c94777017622a809b/view
15. [L1 shape-it | Sources Sought | due 2026-04-28] DEPT OF DEFENSE · Request for Information (RFI) for Enterprise Governance, Data Tagging, and Records Management Solution for Microsoft 365
   - tier 2 (Microsoft 365, OneDrive, SharePoint, data loss prevention, data tagging, records management)
   - TITLE: Request for Information (RFI) for Enterprise Governance, Data Tagging, and Records Management Solution for Microsoft 365
   - https://sam.gov/opp/12cf278f7ae042e6978d72d5b5de2f88/view
16. [L1 shape-it | Sources Sought | due 2026-07-08] DEPT OF DEFENSE · CIO Microsoft Sentinel Implementation FY26
   - tier 2 (M-21-31, M365, Microsoft 365, event logging)
   - These services require intimate and expert-level knowledge of USAWC's specific Microsoft 365 A5 licensing and security configuration.
   - https://sam.gov/opp/9844dc9a31814234bbfef9359f22676f/view
17. [L1 shape-it | Sources Sought | due 2025-10-10] NATIONAL AERONAUTICS AND SPACE ADMINISTRATION · Request for Information (RFI) for Microsoft Software Products and Services for NASA Enterprise IT Support
   - tier 2 (Microsoft 365, SharePoint)
   - Productivity and Collaboration: Microsoft 365 G5, Office Professional Plus, SharePoint, Power Apps, Teams Phone and Audio Conferencing GCC will empower teams to work seamlessly across locations and disciplines.
   - https://sam.gov/opp/e19a5c42fa3947d7b68a1abf966103c4/view
18. [L1 shape-it | Sources Sought | due 2025-11-07] DEPT OF DEFENSE · Hosted eDiscovery Platform for USACE
   - tier 2 (Microsoft 365, SharePoint)
   - System Architecture & Integration � Can your solution integrate with existing USACE systems or platforms (e.g., Microsoft 365, SharePoint, Exchange)?
   - https://sam.gov/opp/10df83a04a584be290a34e5da547f1a0/view
19. [L1 shape-it | Sources Sought | due 2026-01-02] DEPT OF DEFENSE · AI-Powered Software Development and Modernization
   - tier 2 (CUI, Copilot)
   - 3.2 Tools and Technologies AI Models and Tools: What specific AI models (e.g., GPT-4, Claude 3.5, Gemini) and AI coding assistant tools (e.g., GitHub Copilot, Cursor, Cody, Tabnine) does your company utilize and how do you incorporate use specifically with PII/PHI data?
   - https://sam.gov/opp/3ddb0534f71a41fbbfb16ba052af60a3/view
20. [L1 shape-it | Sources Sought | due 2026-04-10] FEDERAL MEDIATION AND CONCILIATION SERVICE · Federal Mediation and Conciliation Service Application Development and Support Services
   - tier 2 (M365, Microsoft 365)
   - The Federal Mediation and Conciliation Service (FMCS) is issuing this Sources Sought notice for market research purposes to identify qualified and interested firms capable of providing Application Development and Support Services (ADSS) in support of the agency�s Microsoft 365 (M365) environment and related systems.
   - https://sam.gov/opp/d025f6dcb1014df6a6efe74c53fab341/view
21. [L1 shape-it | Sources Sought | due 2026-04-24] VETERANS AFFAIRS, DEPARTMENT OF · 6515--NEW - Clinical Monitoring Solution, TheraDoc
   - tier 2 (SharePoint, records management)
   - ... ) Business Impact Analysis (BIA) and Business Continuity Plan-Continuity of Operations Plan (BCP-COOP) VHA Field VAMC Policy MCP 003-001, Internet and Intranet-SharePoint Content Development (4) VHA Field VAMC Policy MCP 674-ISO-003 Use of Electronic Mail and Internet Usage Guidelines (5) VHA Field VAMC Policy MCP 674- ...
   - https://sam.gov/opp/04b9df6a78344290a11b65a31e96b737/view
22. [L1 shape-it | Sources Sought | due 2026-05-06] ENERGY, DEPARTMENT OF · Sources Sought / Request for Information (RFI) 26-047 Unstructured Data Environment Scanning
   - tier 2 (CUI, unstructured data)
   - TITLE: Sources Sought / Request for Information (RFI) 26-047 Unstructured Data Environment Scanning
   - https://sam.gov/opp/eb92833f533c49a08c353eae8eb258d2/view
23. [L1 shape-it | Sources Sought | due 2026-06-12] HOMELAND SECURITY, DEPARTMENT OF · Commercial Off-the-Shelf Computer Aided Dispatch System
   - tier 2 (records management, zero trust)
   - ... generate and complete Quick Response Cards (QRCs)) and capture mission/case data for records management; Provide the capability to access and view available sensor data; Provide CAD-based AI to support queries, briefs and produce tailored operational reports Integrate with existing or intended USCG enterprise GIS capab ...
   - https://sam.gov/opp/476008224e0b4f08ac087807c7395450/view
24. [L1 shape-it | Sources Sought | due 2026-06-19] DEPT OF DEFENSE · DAF Insider Threat Program for User Activity Monitoring (UAM)
   - tier 2 (insider threat, user activity monitoring)
   - TITLE: DAF Insider Threat Program for User Activity Monitoring (UAM)
   - https://sam.gov/opp/65629c5e7273424998f60d85f29a4885/view
25. [L1 shape-it | Sources Sought | due 2026-08-04] DEPT OF DEFENSE · Request for Information (RFI) for Army Enterprise Identity, Credential, and Access Management (E-ICAM) Services Delivery Contract
   - tier 2 (least privilege, zero trust)
   - This service must manage the full lifecycle of identities for all users (soldiers, civilians, contractors, mission partners), enforce the principle of least privilege, and align with the DoD's Zero Trust Strategy.
   - https://sam.gov/opp/5da029b043a64740a058a5543687c326/view
26. [L1 shape-it | Sources Sought | due 2026-08-12] VETERANS AFFAIRS, DEPARTMENT OF · 7B22--614-26-3-560-0221 - IVX Omnicell Workflows Equipment
   - tier 2 (M-21-31, records management)
   - ... with National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management, VA Handbook 6300.1, Records Management Procedures, and applicable VA Records Control Schedules. The contractor shall provide its plan for destruction of all VA data in its possessi ...
   - https://sam.gov/opp/19da7d2c9f114d49a196edf15502d058/view
27. [L1 shape-it | Sources Sought | due 2025-10-07] VETERANS AFFAIRS, DEPARTMENT OF · DG11--Satellite TV Programming Services Sources Sought only
   - tier 2 (records management)
   - Records & Training -- Contractor shall comply with all applicable records management laws.
   - https://sam.gov/opp/fdc85f4fa2a24679adaafe0f0fb7d9e4/view
28. [L1 shape-it | Sources Sought | due 2025-10-13] DEPT OF DEFENSE · Mission Planning Collaborative Server
   - tier 2 (CUI)
   - Contractors must be certified through Defense Logistics Information Services (DLIS) in order to access Controlled Unclassified Information (CUI) or Export Controlled Information.
   - https://sam.gov/opp/b834f99b94c24c35925b38a6888813ba/view
29. [L1 shape-it | Sources Sought | due 2025-11-14] DEPT OF DEFENSE · eDiscovery Software Solution for USACE
   - tier 2 (Microsoft 365)
   - � Does your solution support integration with Microsoft 365, Exchange, or other USACE platforms?
   - https://sam.gov/opp/139d59fe236e4e849cb57fa273d240ac/view
30. [L1 shape-it | Sources Sought | due 2025-11-14] TREASURY, DEPARTMENT OF THE · Sources Sought Notice - Enterprise Network and Information Security Enhancement (ENISE) Services
   - tier 2 (DLP)
   - ... The IRS seeks contractor support to optimize IRS CIPEC Program�s Data Loss Protection (DLP) Technical team with experienced Cloud Technical, Federal Information Security Management Act (FISMA) Compliance, and Program support Subject Matter Experts (SMEs), who successfully supported the implementation/integration of an ...
   - https://sam.gov/opp/b26b3a8d27844104953141eb8339b3a0/view
31. [L1 shape-it | Sources Sought | due 2025-11-15] TRANSPORTATION, DEPARTMENT OF · Request for Information: Mobile Telecom Expense Management (TEM) Solution
   - tier 2 (records management)
   - Ensure compliance with federal security, accessibility, and records management standards.
   - https://sam.gov/opp/c18cce1cbd8341e1bcf225df977aa81a/view
32. [L1 shape-it | Sources Sought | due 2025-11-25] VETERANS AFFAIRS, DEPARTMENT OF · C1DA--AE-NRM-687-26-100 Modernize Physical/Electronic Security Systems
   - tier 2 (data classification)
   - ... Physical Security and Resiliency Design Manual AE SOW 687-26-100 SOW Attachment 01 - Required Div 01 Spec Sections SOW Attachment 02 - Sensitive Infrastructure Data Classification Memo SOW Attachment 03 - 01 32 16.01 EHRM A-E CPM Schedules SOW Attachment 04 - VAWW Campus Map SOW Attachment 05 - Fiber Optic Distribution ...
   - https://sam.gov/opp/4df5584dd47d40a2bd0969196bed5481/view
33. [L1 shape-it | Sources Sought | due 2025-12-02] VETERANS AFFAIRS, DEPARTMENT OF · DA01--550-26-1-985-0034 InstyMed Dispenser System (VA-26-00024776)
   - tier 2 (records management)
   - ... with National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management, VA Handbook 6300.1, Records Management Procedures, and applicable VA Records Control Schedules. The contractor shall provide its plan for destruction of all VA data in its possessi ...
   - https://sam.gov/opp/82070a7c43c54012a7d1dbd648bba014/view
34. [L1 shape-it | Sources Sought | due 2025-12-04] DEPT OF DEFENSE · Sources Sought for Department of the Navy Large Aircraft Infrared Countermeasures (DoN LAIRCM) FY27-FY31 Production
   - tier 2 (DLP)
   - ... The hardware procurement includes the following: Control Indicator Unit Replacements (CIURs) DoN LAIRCM Processors (DLP) Infrared (IR) Missile Warning System (MWS) Sensors Advanced Threat Warning (ATW) Sensors Multi-Role Electro-Optical End-To-End Test Sets (MEONs) Guardian Laser Transmitter Assemblies (GLTAs) GLTA Joi ...
   - https://sam.gov/opp/48216d0208e4499bb2fe1d8544a203ef/view
35. [L1 shape-it | Sources Sought | due 2025-12-05] DEPT OF DEFENSE · Request for Information - Link 16 Ground Station
   - tier 2 (CUI)
   - To request acces to document labeled as CUI please reach out to Denis Grenier in order to complete a NDA.
   - https://sam.gov/opp/25f1721e5b79444f97ca45612f0e4c07/view
36. [L1 shape-it | Sources Sought | due 2025-12-10] JUSTICE, DEPARTMENT OF · RFI - DOJ JMD Secure Communications
   - tier 2 (records management)
   - ... Records Management Capability to facilitate adherence to the Federal Records Act (44 USC 3301) and federal recordkeeping requirements (36 CFR 1220) Degree of compliance with the National Archives and Records Administration�s Universal Electronic Records Management (ERM) �system� requirements which address electronic re ...
   - https://sam.gov/opp/b6ebbbdb22f44dff8eddce82f3e926f2/view
37. [L1 shape-it | Sources Sought | due 2025-12-29] HOMELAND SECURITY, DEPARTMENT OF · SOURCES SOUGHT - OFFICE OF PROFESSIONAL RESPONSIBILITY INSIDER THREAT PROGRAM (ITP)
   - tier 2 (insider threat)
   - TITLE: SOURCES SOUGHT - OFFICE OF PROFESSIONAL RESPONSIBILITY INSIDER THREAT PROGRAM (ITP)
   - https://sam.gov/opp/105c4d9ac915498d8227019be387f5d5/view
38. [L1 shape-it | Sources Sought | due 2025-12-30] VETERANS AFFAIRS, DEPARTMENT OF · J065--Software Evolution Services for Philips Equipment
   - tier 2 (records management)
   - RECORDS MANAGEMENT OBLIGATIONS: 1.
   - https://sam.gov/opp/23f5b7af632442d3add8129029c4a131/view
39. [L1 shape-it | Sources Sought | due 2026-01-19] DEPT OF DEFENSE · Request for Information - Mounted Assured Positioning, Navigation, and Timing System (MAPS) GEN II Production and Sustainment IDIQ
   - tier 2 (CUI)
   - Does your organization have access to store Controlled Unclassified Information (CUI)?
   - https://sam.gov/opp/ca676ca22cc748aca331a33d546687dc/view
40. [L1 shape-it | Sources Sought | due 2026-02-03] VETERANS AFFAIRS, DEPARTMENT OF · DA10--Alternative to BigFix Endpoint Management (VA-26-00012805)
   - tier 2 (least privilege)
   - Role-Based Control / Least Privilege Implement role-based access controls, granting users the minimum necessary privileges to reduce security risks and enhance operational efficiency.
   - https://sam.gov/opp/668696925b9d491d8036262581d0d148/view
41. [L1 shape-it | Sources Sought | due 2026-02-05] COMMERCE, DEPARTMENT OF · Office of Law Enforcement VMS Units Sources Sought
   - tier 2 (OneDrive)
   - Zip Files, Cloud storage providers, google docs, web-based drop boxes, OneNote/OneDrive, URLs, web-based format, or any other virtual/web-based memory service are NOT acceptable methods of submitting a response electronically.
   - https://sam.gov/opp/9c3fc30a33594158b0120e3ad7818df8/view
42. [L1 shape-it | Sources Sought | due 2026-02-11] DEPT OF DEFENSE · Sources sought NSN: 6340-014712595; P/N 35790-211-400, 058094-2, Sensing Element, Fire
   - tier 2 (CUI)
   - SARs may not be processed if it is determined that the SAR is not cost effective due Source Selection Info-See FAR 2.101 & 3.104 CUI to low item demand.
   - https://sam.gov/opp/98e4520a74b741a2a2478f22e6351d0d/view
43. [L1 shape-it | Sources Sought | due 2026-02-12] DEPT OF DEFENSE · Commercial Positioning, Navigation, and Timing (PNT) Solutions Request for Information (RFI)
   - tier 2 (CUI)
   - Does your organization have access to store Controlled Unclassified Information (CUI)?
   - https://sam.gov/opp/13c99481a919466bbe7bbcc36e4a57c2/view
44. [L1 shape-it | Sources Sought | due 2026-02-16] DEPT OF DEFENSE · Records Management/Declassification
   - tier 2 (records management)
   - TITLE: Records Management/Declassification
   - https://sam.gov/opp/41a26f2354454c7887e269a533cd9e97/view
45. [L1 shape-it | Sources Sought | due 2026-02-23] VETERANS AFFAIRS, DEPARTMENT OF · 6540--Intraoperative Digital Guidance System - Cataract Surgery Lebanon VA Medical Center
   - tier 2 (records management)
   - ... with National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management, VA Handbook 6300.1, Records Management Procedures, and applicable VA Records Control Schedules. n. The contractor shall provide its plan for destruction of all VA data in its posse ...
   - https://sam.gov/opp/eed6d77a26264cb6bcd2dd1a123a315e/view
46. [L1 shape-it | Sources Sought | due 2026-02-25] VETERANS AFFAIRS, DEPARTMENT OF · 7B22--Mobile Security Trailer
   - tier 2 (records management)
   - ... h National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management and its Handbook 6300.1 Records Management Procedures, applicable VA Records Control Schedules, and VA Handbook 6500.1, Electronic Media Sanitization. Self-certification by the contrac ...
   - https://sam.gov/opp/2e6f566f3fbd4b30b88df42916f54ef6/view
47. [L1 shape-it | Sources Sought | due 2026-03-06] ENERGY, DEPARTMENT OF · Records Management and/or Security Management System
   - tier 2 (records management)
   - TITLE: Records Management and/or Security Management System
   - https://sam.gov/opp/bcb27c2724174ca9818c58b9f89d7507/view
48. [L1 shape-it | Sources Sought | due 2026-03-14] AGRICULTURE, DEPARTMENT OF · PHIS Predictive Analytics
   - tier 2 (unstructured data)
   - Risk Scoring & Analytics: - Describe capabilities for generating dynamic risk scores using structured and unstructured data.
   - https://sam.gov/opp/603ca464a73d48d883ee09f22685195e/view
49. [L1 shape-it | Sources Sought | due 2026-03-27] DEPT OF DEFENSE · Photogrammetry Software
   - tier 2 (CUI)
   - Before access is granted, you must complete, sign, and email Attachment 02 � CUI NDA to the Points of Contact listed in this RFI.
   - https://sam.gov/opp/f99ab166156746f0b0f7ec192cd52b78/view
50. [L1 shape-it | Sources Sought | due 2026-03-30] HOMELAND SECURITY, DEPARTMENT OF · Financial Close Modernization Tool
   - tier 2 (records management)
   - What options exist for data retention, archiving, and purging in compliance with federal records management requirements?
   - https://sam.gov/opp/6b814801e2e141d2be3ef72656569065/view
51. [L1 shape-it | Sources Sought | due 2026-04-01] VETERANS AFFAIRS, DEPARTMENT OF · 6530--VISN H-Wave H4
   - tier 2 (records management)
   - Contractor agrees to comply with Federal and Agency records management policies, including those policies associated with the safeguarding of records covered by the Privacy Act of 1974.
   - https://sam.gov/opp/a995c8f5a5d445b38e756dffa32e0279/view
52. [L1 shape-it | Sources Sought | due 2026-04-02] VETERANS AFFAIRS, DEPARTMENT OF · 6515--Audiology Equipment Upgrade (Replacement) For the Manchester VAMC
   - tier 2 (records management)
   - ... ontactor policies and procedures shall comply with all VA Privacy & Security, the Privacy Act and Health Insurance Portability and Accountability Act (HIPAA).� Records Management Language for Statement of Work (SOW):� The following standard items relate to records generated in executing the contract and shall be includ ...
   - https://sam.gov/opp/0e5e392f32d0409f8cbf1fa39bd6466e/view
53. [L1 shape-it | Sources Sought | due 2026-04-03] HOMELAND SECURITY, DEPARTMENT OF · Internal Control Program Management System
   - tier 2 (SharePoint)
   - Describe how your solution integrates with Microsoft PowerApps and other Microsoft products (SharePoint, Teams, Outlook).
   - https://sam.gov/opp/1495ea2f888844bda020623d0c543917/view
54. [L1 shape-it | Sources Sought | due 2026-04-13] VETERANS AFFAIRS, DEPARTMENT OF · 5998--Tube System Hardware Upgrade Central Arkansas Veterans Healthcare Sys
   - tier 2 (records management)
   - RECORDS MANAGER: Contractor shall comply with all applicable records management laws and regulations, as well as National Archives and Records Administration (NARA) records policies, including but not limited to the Federal Records Act (44 U.S.C.
   - https://sam.gov/opp/c87359c8fa7848b694843e038af3993a/view
55. [L1 shape-it | Sources Sought | due 2026-04-17] VETERANS AFFAIRS, DEPARTMENT OF · 6525--ENT/URO Scope Consumables *** BRAND NAME OR EQUAL***
   - tier 2 (records management)
   - ... with National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management, VA Handbook 6300.1, Records Management Procedures, and applicable VA Records Control Schedules. n. The contractor shall provide its plan for destruction of all VA data in its posse ...
   - https://sam.gov/opp/fb8b0ea6992f43ac93a82f2829ae41ff/view
56. [L1 shape-it | Sources Sought | due 2026-04-22] DEPT OF DEFENSE · NetOps Solution Software Licensing
   - tier 2 (CUI)
   - (U) Access to Controlled Unclassified Information (CUI).
   - https://sam.gov/opp/fb518ac28a0742368427797c4b55122b/view
57. [L1 shape-it | Sources Sought | due 2026-04-22] DEPT OF DEFENSE · DON CIO � Information Technology Division (ITD) is seeking IT and IT-related support services, in classified and unclassified areas.
   - tier 2 (SharePoint)
   - ... s and helpdesk, Video Teleconferencing (VTC), telecommunications, NMCI tech refreshes, mobility and wireless equipment upgrades, inventory logs, maintenance of SharePoint server farms and sites, portal upgrades, and engineering/development/integration of DON CIO Claimancy/SECNAV Headquarters� hardware, software, and re ...
   - https://sam.gov/opp/1a79cb7dd281485b9d5424654f064c3c/view
58. [L1 shape-it | Sources Sought | due 2026-04-24] HEALTH AND HUMAN SERVICES, DEPARTMENT OF · TITLE � SCIENTIFIC FREEZER
   - tier 2 (event logging)
   - Audit trails and logging: Provide comprehensive audit trail/event logging for user actions, orders, inventory changes, and system/environment events with exportable records.
   - https://sam.gov/opp/312c9ceb4d3f47cba2f85d65f49d24e1/view
59. [L1 shape-it | Sources Sought | due 2026-04-29] VETERANS AFFAIRS, DEPARTMENT OF · AN11--Sources Sought - Visiopharm Software Maintenance B 4
   - tier 2 (records management)
   - ... h National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management and its Handbook 6300.1 Records Management Procedures, applicable VA Records Control Schedules, and VA Handbook 6500.1, Electronic Media Sanitization. Self-certification by the contrac ...
   - https://sam.gov/opp/0c690a60e82e49d2a6b660ad13ba7519/view
60. [L1 shape-it | Sources Sought | due 2026-04-30] DEPT OF DEFENSE · Sources Sought: Commercial Space Domain Awareness (SDA), Training Large Language Model (LLM) Module
   - tier 2 (CUI)
   - What specific DoD/DoW security and data handling standards (e.g., Defense Information Systems Agency (DISA) Security Technical Implementation Guides (STIGs), Controlled Unclassified Information (CUI) handling) have you implemented in past projects?
   - https://sam.gov/opp/7dc605c65757438182dd397148777baa/view
61. [L1 shape-it | Sources Sought | due 2026-05-01] JUSTICE, DEPARTMENT OF · ATR IT MODERNIZATION: DATA ANALYTICS SOLUTION
   - tier 2 (unstructured data)
   - Specifically, this RFI seeks to: Identify platforms capable of supporting large-scale data ingestion, processing, and analysis of structured and unstructured data.
   - https://sam.gov/opp/2716860507ff4b0c8cfaa708a47afc27/view
62. [L1 shape-it | Sources Sought | due 2026-05-06] ENERGY, DEPARTMENT OF · Sources Sought / Request for Information (RFI) 26-046 Electronic Records Management System (ERMS)
   - tier 2 (records management)
   - TITLE: Sources Sought / Request for Information (RFI) 26-046 Electronic Records Management System (ERMS)
   - https://sam.gov/opp/3452f6d7e8eb4a7d930321c614227440/view
63. [L1 shape-it | Sources Sought | due 2026-05-18] DEPT OF DEFENSE · SOURCES SOUGHT FOR AN/PVS-7 NIGHT VISION DEVICES FOR FOREIGN MILITARY SALES (FMS)
   - tier 2 (CUI)
   - ADDITIONAL TECHNICAL REQUIREMENTS (CUI) Figure of Merit (FOM) for AN/PVS-7B Night Vision Goggles (NVG) and spare Image Intensifier Tubes (IIT) must not exceed 1800 or less 1600, where FOM is calculated by multiplying the signal-to-noise ratio by the resolution as measured in line pairs per millimeter.
   - https://sam.gov/opp/fef716e672b14e77bf81e124d8df4278/view
64. [L1 shape-it | Sources Sought | due 2026-05-20] DEPT OF DEFENSE · MP19 V1 Control and Indicator Unit (CUI)
   - tier 2 (CUI)
   - TITLE: MP19 V1 Control and Indicator Unit (CUI)
   - https://sam.gov/opp/7e85035b9a1a451d85b307654efd3f38/view
65. [L1 shape-it | Sources Sought | due 2026-05-22] LABOR, DEPARTMENT OF · RFI for AI Enabled Accessibility Tooling
   - tier 2 (Microsoft 365)
   - Document Remediation and Tagging Assistance � The solution shall support remediation of non-web content, including PDF tagging, reading order correction, and remediation of content authored in Microsoft Office and Microsoft 365 applications.
   - https://sam.gov/opp/01da58849ec24c608f56e57caa18b0b1/view
66. [L1 shape-it | Sources Sought | due 2026-05-22] ENERGY, DEPARTMENT OF · R--Cancelled: RFI for COTS ERM System
   - tier 2 (records management)
   - This RFI for Commercial-Off-The-Shelf Electronics Records Management System is hereby cancelled.
   - https://sam.gov/opp/2069793b7c4f46cc9167a461c486ec76/view
67. [L1 shape-it | Sources Sought | due 2026-05-22] DEPT OF DEFENSE · TELEMETRY BEST SOURCE SELECTOR
   - tier 2 (event logging)
   - Event logging for what happened source selection wise during a mission set.
   - https://sam.gov/opp/91775050a1e141e0ba7e9194e248b21c/view
68. [L1 shape-it | Sources Sought | due 2026-05-26] VETERANS AFFAIRS, DEPARTMENT OF · 7A21--Web-based on-call and physician scheduling software brand name or equal to AMION FOR ENTERPRISES Please see Sources Sought Notice for info requested
   - tier 2 (records management)
   - 10.9 The contractor agrees to comply with Federal and Agency records management policies, including those policies associated with the safeguarding of records covered by the Privacy Act of 1974.
   - https://sam.gov/opp/5f35156b37444d638146d813b24756bc/view
69. [L1 shape-it | Sources Sought | due 2026-05-27] VETERANS AFFAIRS, DEPARTMENT OF · DA01--Software maintenance and support for MyPath® tools in support of VA Puget Sound Healthcare System, Seattle, WA
   - tier 2 (records management)
   - ... h National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management and its Handbook 6300.1 Records Management Procedures, applicable VA Records Control Schedules, and VA Handbook 6500.1, Electronic Media Sanitization. Self-certification by the contrac ...
   - https://sam.gov/opp/65c654104c4d49faa29b2f00c7efdcc8/view
70. [L1 shape-it | Sources Sought | due 2026-06-03] ENERGY, DEPARTMENT OF · R--Request for Information (RFI) for COTS ERM System
   - tier 2 (records management)
   - Request for Information (RFI) for Commercial-Off-The-Shelf Electronics Records Management System INTRODUCTION: The U.S.
   - https://sam.gov/opp/d618890a0f39444b886917ecb367236f/view
71. [L1 shape-it | Sources Sought | due 2026-06-05] ENERGY, DEPARTMENT OF · R--Request for Proposal (RFI) Records Digitization Services
   - tier 2 (records management)
   - The primary objective is to convert these physical records into digital formats that are NARA-compliant and can be seamlessly integrated into a future commercial off-the-shelf (COTS) electronic records management (ERM) system.
   - https://sam.gov/opp/61b17b16e7f345e993edb80a540394c3/view
72. [L1 shape-it | Sources Sought | due 2026-06-11] DEPT OF DEFENSE · Managed Service Provider
   - tier 2 (SharePoint)
   - ... S.� Describe your experience with projects of similar size, scope, and complexity, particularly in supporting Microsoft-based enterprise systems (Dynamics CRM, SharePoint, Azure, SQL) within the DoD.� Provide at least two (2) examples of relevant past performance from the last five (5) years. 3. Rough Order of Magnitud ...
   - https://sam.gov/opp/a7e939c1b10b4f7d96fe7b8bfcd96b3d/view
73. [L1 shape-it | Sources Sought | due 2026-06-12] DEPT OF DEFENSE · Microsoft Unified Defense Language Institute Foreign Language Center (DLIFLC)
   - tier 2 (SharePoint)
   - ... ions across a broad range of Microsoft products and platforms, including but not limited to Active Directory (AD), Windows Server OS, Exchange, Entra AD, ADFS, SharePoint, Project, PowerApps solutions, pooled storage, account management, asset inventory, Azure, and equipment tracking systems. The Contractor shall also ...
   - https://sam.gov/opp/9b933a39a7f74e738ca6552a949df7f2/view
74. [L1 shape-it | Sources Sought | due 2026-06-13] HOUSING AND URBAN DEVELOPMENT, DEPARTMENT OF · Home Equity Conversion Mortgage (HECM) Business Service Provider Solution that fully supports all functional requirements, processes, steps, timelines, and reports associated with FHA�s current HECM insurance program and regulatory changes.
   - tier 2 (records management)
   - ... ractices for financial management and reporting; Federal government and Credit Program accounting; auditing; state-of-the-art information technology, security, records management, and privacy standards; and project management. Provide a HECM services Help Desk to address operational and technical issues related to deli ...
   - https://sam.gov/opp/a068570161394904b09840bb8b805273/view
75. [L1 shape-it | Sources Sought | due 2026-06-15] DEPT OF DEFENSE · Secure Test Data Management, Storage Modernization, and Distributed Access Architecture
   - tier 2 (data tagging)
   - ... 3, Secret, Special Access Program (SAP)-capable) - Distributed data availability across authorized test nodes - Role-based and attribute-based access control - Data tagging, lineage tracking, and lifecycle management - Long-term archival storage with rapid retrieval capabilities - Controlled cross-caveat data transfer ...
   - https://sam.gov/opp/8338647de8764559b67883d60e4943e1/view
76. [L1 shape-it | Sources Sought | due 2026-06-29] DEPT OF DEFENSE · USAF F-16 Next-Generation Mission Compute (NGMC) Upgrade for Combat Capabilities for F-16, Blocks 40/42/50/52
   - tier 2 (CUI)
   - This is a controlled attachment that contains Export Control, Controlled Unlcassified Information (CUI), Controlled Technical Data.
   - https://sam.gov/opp/02dd838c88b04dfc8b26efff68883bcf/view
77. [L1 shape-it | Sources Sought | due 2026-07-13] VETERANS AFFAIRS, DEPARTMENT OF · 6640--598-26-4-6701-0095 Illumina NextSeq 2000 and DRAGEN POP 09/30/2026-9/29/2027
   - tier 2 (records management)
   - RECORDS MANAGER: Contractor shall comply with all applicable records management laws and regulations, as well as National Archives and Records Administration (NARA) records policies, including but not limited to the Federal Records Act (44 U.S.C.
   - https://sam.gov/opp/556282f14e0041fcb7ee5423d82f66a0/view
78. [L1 shape-it | Sources Sought | due 2026-07-13] DEPT OF DEFENSE · RFI - Fog Tester Instrument Image Analysis Software JUN 2026
   - tier 2 (least privilege)
   - ... Software Development Lifecycle, including input validation of all data received from the FogTester application and TCP interface, adherence to the principle of least privilege, and elimination of compiler warnings at the highest available warning level. The Contractor shall apply these practices to all custom-developed ...
   - https://sam.gov/opp/3d811fcfe9ae402d9c9998d1a120f1fe/view
79. [L1 shape-it | Sources Sought | due 2026-07-22] VETERANS AFFAIRS, DEPARTMENT OF · DH10--HAMPTON VAMC TELE TOWNHALL SOURCES SOUGHT
   - tier 2 (records management)
   - RECORDS MANAGEMENT STATEMENT 8.1 Contractor shall comply with all applicable records management laws and regulations, as well as National Archives and Records Administration (NARA) records policies, including but not limited to the Federal Records Act (44 U.S.C.
   - https://sam.gov/opp/0b61ba4ce6ee491db0b2a143f4008d4f/view
80. [L1 shape-it | Sources Sought | due 2026-07-22] JUSTICE, DEPARTMENT OF · Gimmal ERP Link Subscription
   - tier 2 (SharePoint)
   - Gimmal ERP-Link allows SAP content to be managed against a complete content lifecycle in SharePoint as the single ECM platform/repository, and negates the requirement for an additional ECM system to be purchased and implemented to support information management and enhanced business process management.
   - https://sam.gov/opp/917487b99d4c45e39295e77736a85122/view
81. [L1 shape-it | Sources Sought | due 2026-08-03] VETERANS AFFAIRS, DEPARTMENT OF · U099--VIRTUAL EMPLOYEE WHOLE HEALTH
   - tier 2 (records management)
   - National Archives and Records Administration (NARA) Records Management Language for Contracts Citations to pertinent laws, codes and regulations such as 44 U.S.C.
   - https://sam.gov/opp/32446a819bb74f2fb511c5fdf676dde1/view
82. [L1 shape-it | Sources Sought | due 2026-08-03] HOMELAND SECURITY, DEPARTMENT OF · Real Property Management System (RPMS)
   - tier 2 (records management)
   - The Contractor shall comply with all DHS and OCIO standards and regulations including those for development, performance, testing, project management, operations support, security, privacy, records management, and accessibility.
   - https://sam.gov/opp/92622ec205c54829b3cb8c28fdf6b9d4/view
83. [L1 shape-it | Sources Sought | due 2026-08-03] NATIONAL AERONAUTICS AND SPACE ADMINISTRATION · Fire & Emergency Medical Services Records Management System (FEMSRMS) BASE + 4 Option Years
   - tier 2 (records management)
   - TITLE: Fire & Emergency Medical Services Records Management System (FEMSRMS) BASE + 4 Option Years
   - https://sam.gov/opp/f12d61f283744818904563bfb4b2322c/view
84. [L1 shape-it | Sources Sought | due 2026-08-10] VETERANS AFFAIRS, DEPARTMENT OF · Q301--New - QuantiFERON Testing Base + 4
   - tier 2 (records management)
   - Records Management All records (administrative and program specific) created during the period of the contract belong to VA North Texas Health Care System (VANTHCS) and must be returned to VANTHCS at the end of the contract.
   - https://sam.gov/opp/53f71c121d41463c9554a1a2c9a41f7d/view
85. [L1 shape-it | Sources Sought | due 2026-08-12] GENERAL SERVICES ADMINISTRATION · OGE 450 Filer Software
   - tier 2 (records management)
   - The solution shall support HUD OIG ethics officials and authorized users while complying with applicable Federal ethics, privacy, records management, accessibility, and cybersecurity requirements.
   - https://sam.gov/opp/4648634e99044937b6be489ea98f3e66/view
86. [L1 shape-it | Sources Sought | due no deadline] VETERANS AFFAIRS, DEPARTMENT OF · DA10--Payment Integrity Validation and Oversight Tool (PIVOT) (VA-26-00015670) RFI 3
   - tier 2 (least privilege)
   - The demo should confirm compliance with VA Identity and Access Management standards, including PIV authentication, and show how the system adheres to the principle of least privilege.
   - https://sam.gov/opp/f9df025632db4b0a847a486b6c400d39/view
87. [L2 door-open | Presolicitation | due 2025-12-26] TREASURY, DEPARTMENT OF THE · IT Support Specialist - Personal Services Contractor
   - tier 2 (M365, Microsoft 365, OneDrive, SharePoint, cloud security, data loss prevention)
   - ... ensure compliance with government-wide cyber-security directives and FISMA compliant reporting, as well as enterprise initiatives like cyber hygiene scans and data loss prevention IT service delivery to include remote technical support such as device troubleshooting, patching, IT asset provisioning, software/hardware ...
   - https://sam.gov/opp/025ee12bc11e4fe88b3c90e6f2844de1/view
88. [L2 door-open | Special Notice | due 2026-02-13] AGRICULTURE, DEPARTMENT OF · USDA Environmental Review As a Service (ERAS) Platform
   - tier 2 (CUI, zero trust)
   - ... iance: Contractor must adhere to USDA security policies, Zero Trust Architecture, encryption standards, and handle PII and Controlled Unclassified Information (CUI) per NIST guidelines. Place of Performance: Remote; occasional travel to USDA facilities may be required. Set-aside Status: Competition among USDA STRATUS B ...
   - https://sam.gov/opp/3f4e9f17bcfa455e9ff74f46d135e830/view
89. [L2 door-open | Special Notice | due 2026-03-02] DEPT OF DEFENSE · Request for Information - Information Technology Global Operations
   - tier 2 (CUI, zero trust)
   - PROPRIETARY INFORMATION Do not submit classified information or CUI.
   - https://sam.gov/opp/0fa8f7efa44340dbafb8111e761b72ba/view
90. [L2 door-open | Presolicitation | due 2026-03-06] VETERANS AFFAIRS, DEPARTMENT OF · 7A21--Avicenna MedRec Software Brand Name Only
   - tier 2 (M-21-31, records management)
   - ... with National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management, VA Handbook 6300.1, Records Management Procedures, and applicable VA Records Control Schedules. The contractor shall provide its plan for destruction of all VA data in its possessi ...
   - https://sam.gov/opp/77c8ca72cc2342f5a23f02c1e8634f84/view
91. [L2 door-open | Presolicitation | due 2025-10-14] VETERANS AFFAIRS, DEPARTMENT OF · Q502--Brand Name or Equal - Mobile Cardiac Monitoring Services. 5 Year IDIQ - Period of Performance: 12/06/2025 thru 12/05/2030. Total SDVOSB Set-aside.
   - tier 2 (records management)
   - Nominated COR: Christopher Small Phone: (717) 272-6621, x4112 Email: Christopher.Small@va.gov Records Management Language for Contracts: When Federal agencies acquire goods or services, they need to determine what Federal records management requirements should be included in the contract.
   - https://sam.gov/opp/97f579723b57469a8869c113771de944/view
92. [L2 door-open | Presolicitation | due 2025-11-20] VETERANS AFFAIRS, DEPARTMENT OF · 6540--Zeiss Callisto Eye System | 595
   - tier 2 (records management)
   - ... with National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management, VA Handbook 6300.1, Records Management Procedures, and applicable VA Records Control Schedules. n. The contractor shall provide its plan for destruction of all VA data in its posse ...
   - https://sam.gov/opp/f90f169b8bcd44558f993129a0838b84/view
93. [L2 door-open | Special Notice | due 2025-12-04] VETERANS AFFAIRS, DEPARTMENT OF · DG10--WIFI for Poplar Bluff & associated CBOCS 657-26-1-3919-0006
   - tier 2 (records management)
   - Contractor shall comply with all applicable records management laws and regulations, as well as National Archives and Records Administration (NARA) records policies, including but not limited to the Federal Records Act (44 U.S.C.
   - https://sam.gov/opp/2c981c6d25cd423bad30013171eafe98/view
94. [L2 door-open | Special Notice | due 2025-12-22] DEPT OF DEFENSE · Industry Feedback on NGC2 Emerging Architecture
   - tier 2 (CUI)
   - Industry is asked to submit their responses using a fillable form version of these multiple choice and open-ended questions (https://forms.osi.apps.mil/r/uuqYdd7hDB), that corresponds to the CUI document attached to this posting, by the RFI closing date of 22 Dec 2025.
   - https://sam.gov/opp/c9797c80bafb43d79bc2c0f4281db268/view
95. [L2 door-open | Special Notice | due 2025-12-29] VETERANS AFFAIRS, DEPARTMENT OF · J065--Omnicell IVX Rx Framework Interface/IVX Vista Interface DSS Omnicell
   - tier 2 (records management)
   - ... h National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management and its Handbook 6300.1 Records Management Procedures, applicable VA Records Control Schedules, and VA Handbook 6500.1, Electronic Media Sanitization. Self-certification by the contrac ...
   - https://sam.gov/opp/49849f028f1c49fc8c39bd258d81283e/view
96. [L2 door-open | Presolicitation | due 2026-01-06] DEPT OF DEFENSE · Synopsis for Department of the Navy Large Aircraft Infrared Countermeasures (DoN LAIRCM) FY27-FY29 Production
   - tier 2 (DLP)
   - ... The hardware procurement includes the following: Control Indicator Unit Replacements (CIURs) DoN LAIRCM Processors (DLP) Infrared (IR) Missile Warning System (MWS) Sensors Advanced Threat Warning (ATW) Sensors Multi-Role Electro-Optical End-To-End Test Sets (MEONs) Guardian Laser Transmitter Assemblies (GLTAs) GLTA Joi ...
   - https://sam.gov/opp/c57ead8aca014450a97a19392d47a6af/view
97. [L2 door-open | Special Notice | due 2026-01-21] VETERANS AFFAIRS, DEPARTMENT OF · 7A21--NOTICE OF INTENT TO SOLE SOURCE Neurology: Analytics Subscription
   - tier 2 (records management)
   - ... h National Archives and Records Administration (NARA) requirements as outlined in VA Directive 6300, Records and Information Management and its Handbook 6300.1 Records Management Procedures, applicable VA Records Control Schedules, and VA Handbook 6500.1, Electronic Media Sanitization. Self-certification by the Contrac ...
   - https://sam.gov/opp/6020962a4bd24a4690862291354548ef/view
98. [L2 door-open | Special Notice | due 2026-02-19] DEPT OF DEFENSE · Notice of Intent to procure brand name only Dell Laptops through Digital Marketplace/CHESS
   - tier 2 (Copilot)
   - English US non-backlit Copilot key keyboard (583-BMLH).
   - https://sam.gov/opp/b3085fe18ece4d03b184583546c58ade/view
99. [L2 door-open | Presolicitation | due 2026-02-20] DEPT OF DEFENSE · Siemens Software Licenses
   - tier 2 (CUI)
   - Contractors must be certified through Defense Logistics Information Services (DLIS) in order to access Controlled Unclassified Information (CUI) or Export Controlled Information.
   - https://sam.gov/opp/3bf18bfb37c342e79b55595dbdaf5c33/view
100. [L2 door-open | Presolicitation | due 2026-02-21] DEPT OF DEFENSE · Bridge - Technology Support Services
   - tier 2 (SharePoint)
   - ... ms, Non-Tactical Data Processing System (NTDPS) - a multimodule, administrative software application installed on surface and sub-surface platforms and ashore, SharePoint - a collection of sites within Keyport for the storage, retrieval, processing, and collaboration of technical, organizational, and project/task infor ...
   - https://sam.gov/opp/2532bb396bef4b6c92dafde2df460cd6/view

## Owed after this session

- Replay the packet amendment in the primary checkout (its data/ is untouched): `.venv/bin/python -m tools.apply_varonis_a1_frame` after merge, or through the Analyst Layer.
- research_entities additions (Everfox, Forcepoint, Proofpoint rivals; DatAlert product) cannot ride amend_terms on an approved packet; operator decision to unlock-revise-reapprove or accept profile-only carriage.
- Nine wired forecast adapters have no store here; a Command Center forecast refresh fills them, then re-run the forecast leg.
- A2 to A10 supply steps from the Varonis work order remain owed and untouched.
