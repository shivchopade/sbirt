# Demo resumes (synthetic)

Eleven fictional candidates for the sample JD in `jd.txt` — each one shows one behaviour of the engine.
Upload them all to a job created from `jd.txt` and compare the candidate table with this list.

| File | What it demonstrates | Expected outcome |
|---|---|---|
| 01_aarav_sharma_strong_fit.pdf | strong, honest match | SHORTLISTED, no flags |
| 02_rohan_verma_tailored.pdf | wording tailored to the JD, nothing copied | no flags, borderline score |
| 03_priya_nair_dark_sidebar.pdf | white text on a dark sidebar (legit design) | no hidden-text flag |
| 04_karan_mehta_white_text_jd.pdf | the whole JD pasted in white text | FLAGGED, clean ≪ raw score |
| 05_sneha_kulkarni_prompt_injection.pdf | hidden "ignore all previous instructions…" | FLAGGED (PROMPT_INJECTION) |
| 06_vikram_rao_keyword_stuffing.pdf | keyword block repeated many times | stuffing flags, score unchanged |
| 07_meera_iyer_unrelated.pdf | chartered accountant applying for backend | NOT_SHORTLISTED |
| 08_ananya_joshi_hidden_word_text.docx | Word "hidden" text with JD lines | FLAGGED (DOCX_HIDDEN) |
| 09_arjun_patil_copied_jd_lines.pdf | JD sentences pasted as experience bullets | VERBATIM_JD_LINE, lines excluded |
| 10_divya_singh_scanned.pdf | image-only scan, no text layer | UNPARSEABLE → NEEDS_REVIEW |
| 11_nikhil_deshmukh_javascript.pdf | PDF with a JavaScript open action | FLAGGED (MALICIOUS_PDF_FEATURES) |
