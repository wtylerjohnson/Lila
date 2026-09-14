# APFS forecast POC audit: F2026074247

Finding: source acquisition retains all three published contact records. The recent local NETSCOUT Tom Zinzi report includes primary Casey Cauffman and alternate Shauntynee Penix with phones and emails, but places them below four broader agency targets. It omits Kimberly Witcher, the small-business specialist/APFS coordinator.

The shared native contact path is incomplete: ForecastRecord has only small_business_poc, the APFS adapter populates it with coordinator name/email, and the contact graph harvests only sam.gov. Complete primary/alternate/coordinator fields survive in source_fields and can render inside the original-source details, but that is not a structured forecast-contact workflow.

Official source: https://apfs-cloud.dhs.gov/record/74247/public-print/ (HTTP200; retrieval time/hash in SOURCE_RECEIPT.json). All three current names, phones and public display emails verified against the saved record. Public HTML masks email text but carries the normal display encodings; decoded values match the saved/API and user-provided addresses.

## Contact checks

| Source role | Name | Email | Phone | Recent report |
| --- | --- | --- | --- | --- |
| Primary forecast POC | Casey Cauffman | casey.cauffman@usss.dhs.gov | (917) 843-3204 | Present below broader targets |
| Alternate forecast POC | Shauntynee Penix | Shauntynee.Penix@usss.dhs.gov | (305) 407-5598 | Present below broader targets |
| Small-business specialist / APFS coordinator | Kimberly Witcher | pro.smallbusiness@usss.dhs.gov | (202) 836-3519 | Omitted |

## Required repair

Promote all source-published forecast POCs into structured contact records with role, email, published phone, source identity and observation time. Carry them through assessment, research/lead output and report contact sections. Put the forecast-specific primary/alternate routes before broader agency targets; keep the coordinator role distinct. Do not label published phones as mobile without evidence, or contact publication as purchasing authority. Preserve missing fields explicitly and test primary/alternate/coordinator retention plus deduplication.

## Limits

Read-only verification; no code/report mutation, ingestion or client rerun. The current saved operating NETSCOUT sweep does not contain this record. The recent local card is therefore evidence of its report content, not proof that the existing ordinary sweep automatically produced this contact presentation. A pure saved-source mapping/render probe confirms all source fields survive the current code; it creates no LeadTarget or contact-graph entry.
