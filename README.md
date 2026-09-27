# Raksha Audit

Raksha Audit is an AI-assisted contract review application designed for founders, freelancers, and early-stage teams who need an easier way to spot risky contract language before signing.

## Overview

The app accepts a contract upload or pasted text, analyzes the agreement for founder-hostile or high-risk clauses, and returns a clear summary with red flags, explanations, and safer alternatives. It is intended to support early contract review, not replace legal advice from a qualified lawyer.

## Features

- Upload PDF or TXT contracts
- Extract readable text safely from supported files
- Paste contract text manually when needed
- Review overall risk score: High, Medium, or Low
- Identify risky clause categories including:
  - intellectual property
  - payment terms
  - termination
  - confidentiality
  - liability
  - indemnification
  - non-compete / non-solicitation
  - data and privacy
  - dispute resolution
  - automatic renewal
- Show clause text, explanation, and safer alternative language
- Export the analysis as a PDF report
- Gracefully handle invalid files, empty documents, and API errors

## Technology Stack

- Python
- Streamlit
- Google Gemini API
- PyPDF
- ReportLab
- python-dotenv

## Setup Instructions

1. Create and activate a virtual environment:

   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create a local environment file from the example:

   ```bash
   copy .env.example .env
   ```

4. Add your Gemini API key in `.env`:

   ```text
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

5. Run the app:

   ```bash
   streamlit run app.py
   ```

## Environment Variable Setup

The app reads the Gemini key from environment variables using `python-dotenv`. Do not hard-code your API key in source files.

Example `.env`:

```text
GEMINI_API_KEY=your_gemini_api_key_here
```

## Example Workflow

1. Open the app in the browser.
2. Upload a PDF or TXT contract.
3. Review the extracted text and edit if needed.
4. Click "Scan Contract".
5. Review the risk score, executive summary, and flagged clauses.
6. Download the analysis report as a PDF if needed.

## Current Limitations

Live analysis requires a valid Gemini API key and depends on the Gemini API's service availability and account quota. Rate limits, daily quota exhaustion, or temporary service errors can prevent analysis until the quota resets or the service recovers. Live browser analysis has not been verified in the current test session because the API returned a quota-exhaustion response.

## Legal Disclaimer

Raksha Audit is an AI-assisted contract review tool and is not a substitute for advice from a qualified lawyer or other legal professional. It helps identify possible issues in a contract but does not provide legal advice or guarantee a contract is enforceable or fair.
