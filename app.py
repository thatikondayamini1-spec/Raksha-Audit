import io
import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError
from pypdf import PdfReader
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

load_dotenv()

MODEL_NAME = "gemini-3.8-flash"
MAX_ANALYSIS_ATTEMPTS = 5
LEGAL_DISCLAIMER = (
    "Raksha Audit is an AI-assisted contract review tool and is not a substitute for advice from a "
    "qualified lawyer or other legal professional. It helps identify possible issues in a contract, "
    "but it does not provide legal advice or guarantee that a contract is fair, enforceable, or suitable "
    "for your situation."
)

SAMPLE_CONTRACT = """INDEPENDENT CONTRACTOR AGREEMENT

1. WORK PRODUCT AND INTELLECTUAL PROPERTY. Contractor hereby irrevocably assigns to Company all right, title, and interest in any and all inventions, discoveries, works of authorship, ideas, improvements, and other intellectual property created by Contractor at any time, whether or not related to the services, including all pre-existing materials and tools.

2. NON-COMPETE. During the term and for twenty-four (24) months after termination, Contractor shall not directly or indirectly engage in, advise, or invest in any business that competes with Company anywhere in the world.

3. INDEMNIFICATION. Contractor shall defend, indemnify, and hold harmless Company from any and all claims, losses, liabilities, damages, costs, and expenses arising out of or related to the services, without limitation.

4. TERMINATION. Company may terminate this Agreement immediately and without notice for any reason. Contractor may terminate only upon ninety (90) days written notice and must complete all outstanding work during that period.
"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "risk_score": {"type": "STRING", "enum": ["High", "Medium", "Low"]},
        "summary": {"type": "STRING"},
        "red_flags": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "clause_type": {"type": "STRING"},
                    "flagged_text": {"type": "STRING"},
                    "risk_reason": {"type": "STRING"},
                    "suggested_fix": {"type": "STRING"},
                },
                "required": [
                    "clause_type",
                    "flagged_text",
                    "risk_reason",
                    "suggested_fix",
                ],
            },
        },
    },
    "required": ["risk_score", "summary", "red_flags"],
}

SYSTEM_PROMPT = """You are a startup-friendly contract review assistant focused on protecting founders and small businesses from one-sided legal terms.
Analyze the supplied contract for risky clauses, including but not limited to intellectual property assignment, payment terms, termination, confidentiality, liability, indemnification, non-compete or non-solicitation, data/privacy, dispute resolution, and automatic renewal. Identify material founder-hostile terms and return ONLY valid JSON matching the provided schema.
The summary must be exactly two sentences and should read like a clear executive overview. Every flagged_text must be an exact, concise snippet copied directly from the contract. Explain why it is risky in plain English and suggest a founder-friendly clarification or safer alternative. Do not invent clauses that are not present. This is an issue-spotting aid and not legal advice.
"""


def set_page_style() -> None:
    st.markdown(
        """
        <style>
        .main { background: #f5f7fb; }
        .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
        .card {
            background: white;
            border: 1px solid #e5e7eb;
            border-radius: 18px;
            padding: 1.1rem 1.2rem;
            box-shadow: 0 2px 12px rgba(15, 23, 42, 0.04);
            margin-bottom: 1rem;
        }
        .eyebrow {
            letter-spacing: 0.08em;
            text-transform: uppercase;
            font-size: 0.72rem;
            font-weight: 700;
            color: #64748b;
            margin-bottom: 0.35rem;
        }
        .big-number {
            font-size: 2rem;
            font-weight: 800;
            line-height: 1.1;
        }
        .small-muted {
            color: #475569;
            font-size: 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def safe_text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def extract_contract_text(uploaded_file: Any) -> tuple[str | None, str | None, dict[str, Any]]:
    """Read supported contract files and return clean text plus metadata."""
    if uploaded_file is None:
        return None, None, {"status": "error", "message": "No file was uploaded."}

    file_name = safe_text(uploaded_file.name, "uploaded_file")
    file_size = getattr(uploaded_file, "size", 0) or 0
    file_extension = os.path.splitext(file_name)[1].lower()

    if file_extension not in {".pdf", ".txt"}:
        return None, file_name, {
            "status": "error",
            "message": "Unsupported file type. Please upload a PDF or TXT contract.",
        }

    try:
        file_bytes = uploaded_file.getvalue()
    except Exception:
        return None, file_name, {
            "status": "error",
            "message": "The file could not be read. Please try a different file.",
        }

    if not file_bytes or file_size <= 0:
        return None, file_name, {
            "status": "error",
            "message": "The uploaded file is empty. Please upload a valid contract.",
        }

    if file_extension == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(pages).strip()
        except Exception:
            return None, file_name, {
                "status": "error",
                "message": "This PDF could not be processed. Please check whether the file is valid and readable.",
            }
    else:
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = file_bytes.decode("latin-1")
            except Exception:
                return None, file_name, {
                    "status": "error",
                    "message": "This text file could not be read properly. Please try another file.",
                }

    if not text or not text.strip():
        return None, file_name, {
            "status": "error",
            "message": "No readable text was found in the uploaded file. Please paste the contract text manually.",
        }

    return text, file_name, {
        "status": "success",
        "message": "File loaded successfully.",
        "size": file_size,
        "extension": file_extension,
    }


def analyze_contract(contract_text: str, api_key: str) -> dict[str, Any]:
    """Send the contract to Gemini and return structured analysis results."""
    client = genai.Client(api_key=api_key)
    prompt = f"{SYSTEM_PROMPT}\n\nCONTRACT TEXT:\n{contract_text}"
    response = None

    for attempt in range(1, MAX_ANALYSIS_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                ),
            )
            break
        except (ServerError, ClientError, APIError) as error:
            status = getattr(error, "code", None) or getattr(error, "status_code", None)
            error_text = str(error).lower()
            retryable = (
                isinstance(error, ServerError)
                or status in {429, 500, 502, 503, 504, "429", "500", "502", "503", "504"}
                or any(marker in error_text for marker in ("429", "500", "502", "503", "504", "unavailable", "resource_exhausted"))
            )
            if not retryable:
                raise RuntimeError(f"The contract could not be analyzed: {error}") from error
            if attempt == MAX_ANALYSIS_ATTEMPTS:
                raise RuntimeError(
                    "Gemini is temporarily unavailable or rate-limited. "
                    f"The analysis was retried {MAX_ANALYSIS_ATTEMPTS} times; please wait a moment and try again."
                ) from error
            time.sleep(2 ** (attempt - 1))

    if response is None or not response.text:
        raise ValueError("The AI could not generate a response for this contract.")

    payload = response.text.strip()
    payload = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", payload, flags=re.IGNORECASE).strip()
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("The AI response must be a JSON object.")
    if result.get("risk_score") not in {"High", "Medium", "Low"}:
        raise ValueError("The AI response is missing a valid risk_score.")
    if not isinstance(result.get("summary"), str) or not result["summary"].strip():
        raise ValueError("The AI response is missing a usable summary.")
    if not isinstance(result.get("red_flags"), list):
        raise ValueError("The AI response is missing a valid red_flags list.")
    required_flag_fields = ("clause_type", "flagged_text", "risk_reason", "suggested_fix")
    for flag in result["red_flags"]:
        if not isinstance(flag, dict) or any(
            not isinstance(flag.get(field), str) or not flag[field].strip()
            for field in required_flag_fields
        ):
            raise ValueError("The AI response contains an incomplete risk clause.")
    return result


def render_risk_banner(risk_score: str) -> None:
    colors = {
        "High": ("#dc2626", "#fff1f2"),
        "Medium": ("#d97706", "#fffbeb"),
        "Low": ("#16a34a", "#f0fdf4"),
    }
    accent, background = colors.get(risk_score, ("#475569", "#f8fafc"))
    st.markdown(
        f"""
        <div class="card" style="border-left: 8px solid {accent}; background: {background}; padding: 1rem 1.2rem;">
            <div class="eyebrow">Overall risk</div>
            <div class="big-number" style="color: {accent};">{risk_score}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_pdf_report(analysis: dict[str, Any], contract_name: str) -> bytes:
    """Create a simple PDF report showing the analysis results and disclaimer."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleStyle", parent=styles["Title"], fontSize=18, leading=22, spaceAfter=10)
    heading_style = ParagraphStyle("HeadingStyle", parent=styles["Heading2"], fontSize=12, leading=14, spaceBefore=12, spaceAfter=8)
    body_style = ParagraphStyle("BodyStyle", parent=styles["BodyText"], fontSize=9, leading=12, spaceAfter=6)
    story: list[Any] = []

    analysis_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    risk_score = safe_text(analysis.get("risk_score", "Unknown"), "Unknown")
    summary = safe_text(analysis.get("summary", "No summary was provided."), "No summary was provided.")
    red_flags = analysis.get("red_flags", [])

    story.append(Paragraph("Raksha Audit", title_style))
    story.append(Paragraph(f"Uploaded contract: {contract_name}", body_style))
    story.append(Paragraph(f"Analysis time: {analysis_time}", body_style))
    story.append(Paragraph(f"Overall risk: {risk_score}", heading_style))
    story.append(Paragraph("Executive summary", heading_style))
    story.append(Paragraph(summary, body_style))
    story.append(Paragraph("Identified risk clauses", heading_style))

    if not red_flags:
        story.append(Paragraph("No material risk clauses were identified in this pass.", body_style))
    else:
        for index, flag in enumerate(red_flags, start=1):
            clause_type = safe_text(flag.get("clause_type", "Uncategorized clause"), "Uncategorized clause")
            flagged_text = safe_text(flag.get("flagged_text", ""), "No clause text returned")
            risk_reason = safe_text(flag.get("risk_reason", "No risk explanation returned."), "No risk explanation returned.")
            suggested_fix = safe_text(flag.get("suggested_fix", "No fix suggested."), "No fix suggested.")
            story.append(Paragraph(f"{index}. {clause_type}", body_style))
            story.append(Paragraph(f"Relevant text: {flagged_text}", body_style))
            story.append(Paragraph(f"Risk explanation: {risk_reason}", body_style))
            story.append(Paragraph(f"Suggested fix: {suggested_fix}", body_style))
            story.append(Spacer(1, 8))

    story.append(Paragraph("Legal disclaimer", heading_style))
    story.append(Paragraph(LEGAL_DISCLAIMER, body_style))

    doc.build(story)
    return buffer.getvalue()


st.set_page_config(page_title="Raksha Audit", page_icon="🛡️", layout="wide")
set_page_style()

st.title("🛡️ Raksha Audit")
st.caption("AI-assisted contract review designed for founders, freelancers, and early-stage teams.")

if "contract_text" not in st.session_state:
    st.session_state.contract_text = ""
if "uploaded_contract_name" not in st.session_state:
    st.session_state.uploaded_contract_name = ""
if "analysis" not in st.session_state:
    st.session_state.analysis = None

with st.sidebar:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.header("Contract input")
    uploaded_file = st.file_uploader("Upload a PDF or TXT contract", type=["pdf", "txt"], help="Supported files: PDF and TXT")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    if st.button("Load sample contract", use_container_width=True):
        st.session_state.contract_text = SAMPLE_CONTRACT
        st.session_state.uploaded_contract_name = "sample_contract.txt"
        st.session_state.analysis = None
        st.rerun()
    st.caption("Paste a contract below if you prefer manual entry.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.caption(LEGAL_DISCLAIMER)
    st.markdown("</div>", unsafe_allow_html=True)

if uploaded_file is not None:
    text_result, file_name, info = extract_contract_text(uploaded_file)
    st.session_state.uploaded_contract_name = file_name or ""
    if info["status"] == "success":
        st.session_state.contract_text = text_result or ""
        st.success(f"Loaded {file_name} successfully.")
    else:
        st.warning(info["message"])

contract_text = st.text_area(
    "Contract text",
    key="contract_text",
    height=350,
    placeholder="Paste the agreement text here or upload a PDF/TXT contract from the sidebar...",
)

if st.session_state.get("uploaded_contract_name"):
    st.caption(f"Document loaded: {st.session_state.uploaded_contract_name}")

scan_col, export_col = st.columns([1, 0.6])
with scan_col:
    if st.button("Scan contract", type="primary", use_container_width=True):
        if not contract_text.strip():
            st.warning("Please add contract text or upload a valid file before scanning.")
        else:
            api_key = os.getenv("GEMINI_API_KEY", "").strip()
            if not api_key:
                st.error("Gemini API key is missing. Add it to your .env file and restart the app.")
            else:
                with st.spinner("Reviewing the contract for risky clauses..."):
                    try:
                        st.session_state.analysis = analyze_contract(contract_text, api_key)
                    except json.JSONDecodeError:
                        st.error("The AI returned unreadable results. Please retry the scan.")
                    except RuntimeError as error:
                        traceback.print_exc()
                        st.error(str(error))
                    except ValueError as error:
                        traceback.print_exc()
                        st.error(f"The contract could not be analyzed: {error}")
                    except Exception as error:
                        traceback.print_exc()
                        st.error("The analysis failed unexpectedly. Please check the contract text and try again.")

analysis = st.session_state.get("analysis")

if analysis:
    risk_score = safe_text(analysis.get("risk_score", "Unknown"), "Unknown")
    render_risk_banner(risk_score)

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Executive summary")
    st.write(analysis.get("summary", "No summary was returned."))
    st.markdown("</div>", unsafe_allow_html=True)

    red_flags = analysis.get("red_flags", [])
    st.subheader(f"Risk clauses ({len(red_flags)})")
    if not red_flags:
        st.success("No material red flags were identified in this pass.")
    else:
        for index, flag in enumerate(red_flags, start=1):
            clause_type = safe_text(flag.get("clause_type", "Uncategorized clause"), "Uncategorized clause")
            with st.expander(f"{index}. {clause_type}", expanded=False):
                st.markdown("**Relevant clause text**")
                st.code(safe_text(flag.get("flagged_text", ""), "No clause text returned."), language=None)
                st.markdown("**Why this is risky**")
                st.write(safe_text(flag.get("risk_reason", "No risk explanation was returned."), "No risk explanation was returned."))
                st.markdown("**Safer alternative / clarification**")
                st.info(safe_text(flag.get("suggested_fix", "No suggested fix was returned."), "No suggested fix was returned."))

    with export_col:
        pdf_bytes = build_pdf_report(analysis, st.session_state.get("uploaded_contract_name") or "manual_contract.txt")
        st.download_button(
            label="Export PDF report",
            data=pdf_bytes,
            file_name="raksha_audit_report.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.caption(LEGAL_DISCLAIMER)
    st.markdown("</div>", unsafe_allow_html=True)

else:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("How it works")
    st.markdown(
        """
        1. Upload a PDF or TXT contract, or paste the text manually.  
        2. Click "Scan contract" to review the agreement.  
        3. Review the summary, flagged clauses, and suggested fixes.  
        4. Export a PDF report for record keeping or sharing with a lawyer.
        """
    )
    st.markdown("</div>", unsafe_allow_html=True)
